// Direct execution adapters for Claude Code / Codex CLI (AM-IF-001 §6).
// Contracts verified 2026-11-24: claude 2.1.331 `-p --output-format json`, codex-cli 0.167.0 `exec --json` / `exec resume`.
// argv arrays, shell=false, prompt via stdin, process-group kill, no permission-bypass flags.
import { spawn } from 'node:child_process';
import { realpathSync } from 'node:fs';
import type { TaskState } from './domain.ts';
import { resolveBin, type Agent } from './config.ts';

export type Turn = { state: TaskState; text: string; error?: string; sessionId?: string };

const MAX_STDOUT = 8 * 1024 * 1024;

function argsFor(agent: Agent, sessionId?: string): string[] {
  if (agent.provider === 'claude') {
    const a = ['-p', '--output-format', 'json'];
    if (agent.allowedTools?.length) a.push('--allowedTools', agent.allowedTools.join(','));
    if (sessionId) a.push('--resume', sessionId);
    return [...a, ...(agent.extraArgs ?? [])];
  }
  // codex: read-only sandbox unless the agent config overrides it; `exec resume` has no -s, so use -c.
  const base = ['--json', '--skip-git-repo-check', '-c', 'sandbox_mode="read-only"', ...(agent.extraArgs ?? [])];
  return sessionId ? ['exec', 'resume', ...base, sessionId, '-'] : ['exec', ...base, '-'];
}

export function parseClaude(stdout: string): Turn {
  let j: { session_id?: string; is_error?: boolean; subtype?: string; result?: string; permission_denials?: { tool_name?: string }[] };
  try {
    j = JSON.parse(stdout.trim());
  } catch {
    return { state: 'FAILED', text: '', error: 'PROVIDER_OUTPUT_INVALID: claude output is not JSON' };
  }
  if (!j || typeof j !== 'object') return { state: 'FAILED', text: '', error: 'PROVIDER_OUTPUT_INVALID: claude output is not an object' };
  const denied = (j.permission_denials ?? []).map((d) => d.tool_name).filter(Boolean);
  const note = denied.length ? `\n[agent-mesh] permission denied for: ${[...new Set(denied)].join(', ')}` : '';
  if (j.is_error || j.subtype !== 'success' || typeof j.result !== 'string') {
    return { state: 'FAILED', text: j.result ?? '', error: `claude ${j.subtype ?? 'error'}${note}`, sessionId: j.session_id };
  }
  return { state: 'COMPLETED', text: j.result + note, sessionId: j.session_id };
}

export function parseCodex(stdout: string): Turn {
  let sessionId: string | undefined, text = '', done = false, error: string | undefined;
  for (const line of stdout.split('\n')) {
    if (!line.trim()) continue;
    let e: { type?: string; thread_id?: string; item?: { type?: string; text?: string }; error?: { message?: string }; message?: string };
    try {
      e = JSON.parse(line);
    } catch {
      return { state: 'FAILED', text, error: 'PROVIDER_OUTPUT_INVALID: codex emitted a non-JSON line', sessionId };
    }
    // `null` / scalars parse fine but are not events (found by claude-dev + codex-dev live review)
    if (!e || typeof e !== 'object') return { state: 'FAILED', text, error: 'PROVIDER_OUTPUT_INVALID: codex emitted a non-object line', sessionId };
    if (e.type === 'thread.started') sessionId = e.thread_id;
    else if (e.type === 'item.completed' && e.item?.type === 'agent_message') text = e.item.text ?? '';
    else if (e.type === 'turn.completed') done = true;
    else if (e.type === 'turn.failed' || e.type === 'error') error = e.error?.message ?? e.message ?? e.type;
  }
  if (error || !done) return { state: 'FAILED', text, error: `codex: ${error ?? 'turn did not complete'}`, sessionId };
  return { state: 'COMPLETED', text, sessionId };
}

export function runTurn(
  agent: Agent,
  opts: { prompt: string; sessionId?: string; env: NodeJS.ProcessEnv; timeoutMs: number; signal: AbortSignal },
): Promise<Turn> {
  const bin = agent.command ?? [resolveBin(agent.provider) ?? ''];
  if (!bin[0]) return Promise.resolve({ state: 'FAILED', text: '', error: `DEPENDENCY_MISSING: ${agent.provider} CLI not found` });
  let cwd: string;
  try {
    cwd = realpathSync(agent.workspace);
  } catch {
    return Promise.resolve({ state: 'FAILED', text: '', error: 'CONFIG_INVALID: workspace does not exist' });
  }
  return new Promise((resolve) => {
    const child = spawn(bin[0], [...bin.slice(1), ...argsFor(agent, opts.sessionId)], {
      cwd, env: opts.env, detached: true, stdio: ['pipe', 'pipe', 'pipe'], shell: false,
    });
    let out = '', errTail = '', killedFor: string | undefined;
    const kill = (why: string) => {
      killedFor ??= why;
      try { process.kill(-child.pid!, 'SIGTERM'); } catch { /* already gone */ }
      setTimeout(() => { try { process.kill(-child.pid!, 'SIGKILL'); } catch { /* gone */ } }, 5000).unref();
    };
    const timer = setTimeout(() => kill('TIMEOUT: deadline exceeded'), opts.timeoutMs);
    const onAbort = () => kill('CANCELED');
    opts.signal.addEventListener('abort', onAbort, { once: true });
    child.stdout.on('data', (d) => { if (out.length < MAX_STDOUT) out += d; else kill('LIMIT_EXCEEDED: provider output too large'); });
    child.stderr.on('data', (d) => { errTail = (errTail + d).slice(-2000); });
    child.on('error', (e) => { errTail += e.message; });
    child.on('close', (code) => {
      clearTimeout(timer);
      opts.signal.removeEventListener('abort', onAbort);
      if (killedFor) return resolve({ state: killedFor === 'CANCELED' ? 'CANCELED' : 'FAILED', text: '', error: killedFor });
      const turn = agent.provider === 'claude' ? parseClaude(out) : parseCodex(out);
      // exit code 0 alone is not success; a non-zero exit with a valid result is still a failure
      if (code !== 0 && turn.state === 'COMPLETED') return resolve({ ...turn, state: 'FAILED', error: `exit ${code}` });
      if (turn.state === 'FAILED' && !turn.text && errTail) turn.error += ` (stderr: ${redact(errTail).slice(-300)})`;
      resolve(turn);
    });
    // a provider that exits before reading its prompt raises EPIPE; unhandled, that would crash the runtime
    child.stdin.on('error', (e) => { errTail += ` stdin: ${e.message}`; });
    child.stdin.end(opts.prompt);
  });
}

const redact = (s: string) => s.replace(/(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|[A-Fa-f0-9]{32,})/g, '[redacted]');
