// mesh CLI (Facade, AM-IF-001 §3). Talks to the local Runtime; stdout is always a single JSON document.
// shortcut: R mode only (no S-mode receipts without a runtime); control channel is loopback HTTP + token, not a 0600 Unix socket.
import { parseArgs } from 'node:util';
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdirSync, writeFileSync, existsSync, chmodSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, join, relative, isAbsolute } from 'node:path';
import { loadConfig, resolveBin, type Config } from './config.ts';

const EXIT: Record<string, number> = {
  INVALID_ARGUMENT: 2, CONFIG_INVALID: 2, AUTH_REQUIRED: 3, FORBIDDEN: 3, SANDBOX_BLOCKED: 3, DEPENDENCY_MISSING: 4, UNSUPPORTED_CAPABILITY: 4,
  NOT_FOUND: 5, TRANSPORT_ERROR: 6, TIMEOUT: 6, OUTCOME_UNKNOWN: 7, SESSION_BUSY: 7, CONFLICT: 7, LIMIT_EXCEEDED: 8, STORAGE_FULL: 8,
};

function out(body: unknown, code = 0): never {
  process.stdout.write(JSON.stringify(body, null, 2) + '\n');
  process.exit(code);
}
const fail = (code: string, message: string): never =>
  out({ schemaVersion: '1.0', ok: false, error: { code, message, retryable: false } }, EXIT[code] ?? 9);

/**
 * Inside a runtime-managed turn: always the turn's scoped token (an agent must not escalate to the node owner
 * via --config). Otherwise the node owner's config.
 */
function endpoint(configPath?: string) {
  if (process.env.MESH_TURN_TOKEN) {
    if (configPath) fail('FORBIDDEN', '--config is not allowed inside an agent turn');
    if (!process.env.MESH_URL) fail('CONFIG_INVALID', 'MESH_URL missing');
    return { url: process.env.MESH_URL!, token: process.env.MESH_TURN_TOKEN };
  }
  const c = loadConfig(configPath);
  return { url: `http://127.0.0.1:${c.port}`, token: c.localToken };
}

async function call(configPath: string | undefined, method: string, body: object = {}) {
  const { url, token } = endpoint(configPath);
  let res: Response;
  try {
    res = await fetch(`${url}/local/${method}`, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify(body), signal: AbortSignal.timeout(30000),
    });
  } catch (e) {
    // a sandbox (e.g. Codex) denies the connect with EPERM even when the runtime is up; ECONNREFUSED means it is down
    const code = (e as { cause?: { code?: string } }).cause?.code;
    if (code === 'EPERM' || code === 'EACCES') {
      return fail('SANDBOX_BLOCKED', `Connecting to ${url} was blocked by this process's sandbox (${code}); the runtime may be running. Re-run this command with network access / outside the sandbox (needs the user's approval).`);
    }
    return fail('DEPENDENCY_MISSING', `Runtime not reachable at ${url}. Start it with: mesh runtime --config <file>`);
  }
  if (res.status === 401) return fail('AUTH_REQUIRED', 'Runtime rejected the local token');
  let j: { schemaVersion?: string; ok?: boolean; error?: { code: string } | string } | undefined;
  try { j = await res.json() as typeof j; } catch { /* handled below */ }
  if (!res.ok || j?.schemaVersion !== '1.0') { // plain HTTP error from the runtime: keep the single-JSON contract
    const code = ({ 400: 'INVALID_ARGUMENT', 404: 'NOT_FOUND', 413: 'LIMIT_EXCEEDED' } as Record<number, string>)[res.status] ?? 'INTERNAL_ERROR';
    return fail(code, `Runtime HTTP ${res.status}: ${typeof j?.error === 'string' ? j.error : 'unexpected response'}`);
  }
  out(j, j.ok ? 0 : EXIT[(j.error as { code: string }).code] ?? 9);
}

function doctor(configPath?: string) {
  const version = (bin: string | null, args: string[]) => {
    if (!bin) return null;
    const r = spawnSync(bin, args, { encoding: 'utf8', timeout: 15000, stdio: ['ignore', 'pipe', 'pipe'] });
    return r.status === 0 ? (r.stdout.trim() || r.stderr.trim()).split('\n')[0] : null; // `codex login status` prints to stderr
  };
  const claude = resolveBin('claude'), codex = resolveBin('codex');
  const codexLogin = version(codex, ['login', 'status']);
  const billing = ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY'].filter((v) => process.env[v]);
  let config: unknown = null;
  try {
    const c = loadConfig(configPath);
    config = { path: c.path, nodeId: c.nodeId, port: c.port, agents: c.agents.map((a) => a.id), peers: c.peers.map((p) => p.id) };
  } catch (e) {
    config = { error: (e as Error).message };
  }
  out({
    schemaVersion: '1.0', ok: true,
    data: {
      node: process.version,
      providers: {
        claude: { bin: claude, version: version(claude, ['--version']), auth: 'unverified (run a task to confirm subscription login)' },
        codex: { bin: codex, version: version(codex, ['--version']), auth: codexLogin ?? 'unknown' },
      },
      apiKeyEnv: billing.length ? { warning: `set: ${billing.join(', ')} (runtime refuses to start)` } : 'none',
      config,
      capabilities: {
        asyncSend: 'supported', a2aReceive: 'supported (A2A 0.3 JSON-RPC subset, loopback)', pushReceive: 'supported',
        streamReceive: 'unsupported', autoResumeManagedSession: 'supported (opt-in, runtime-managed sessions only)',
        interactiveSessionInjection: 'unsupported', museOutboundTools: 'unsupported', museExternalWake: 'unsupported',
      },
    },
  });
}

/** Writes two node configs on this host (A: claude-dev :47120, B: codex-dev :47121) with fresh tokens. */
function initDemo(dir: string, workspace: string, force: boolean) {
  const t = () => randomBytes(24).toString('base64url');
  const tokAtoB = t(), tokBtoA = t(), tokAtoA = t(), tokBtoB = t();
  const a = 'http://127.0.0.1:47120', b = 'http://127.0.0.1:47121';
  const policy = { maxConcurrentTurns: 2, defaultDeadlineSeconds: 1800 };
  const ws = resolve(workspace);
  // tokens must not be readable by the agents themselves (their workspace is shared with remote callers' requests)
  const rel = relative(ws, resolve(dir));
  if (!rel || (!rel.startsWith('..') && !isAbsolute(rel))) fail('INVALID_ARGUMENT', `--dir must be outside the agent workspace (${ws})`);
  const hostA: Config = {
    schemaVersion: '1.0', nodeId: 'host-a', port: 47120, publicUrl: a, dbPath: 'host-a.db', localToken: t(),
    callers: [{ id: 'host-b', token: tokBtoA, pushOrigin: b }, { id: 'host-a', token: tokAtoA, pushOrigin: a }],
    agents: [{ id: 'claude-dev', provider: 'claude', workspace: ws, allowedSenders: ['host-a', 'host-b'], allowedPeers: ['codex-dev'], autoResume: true, allowedTools: ['Read', 'Grep', 'Glob', 'Bash(mesh send:*)', 'Bash(mesh task get:*)'] }],
    peers: [{ id: 'codex-dev', url: `${b}/agents/codex-dev`, token: tokAtoB }, { id: 'claude-dev', url: `${a}/agents/claude-dev`, token: tokAtoA }],
    policy,
  };
  const hostB: Config = {
    schemaVersion: '1.0', nodeId: 'host-b', port: 47121, publicUrl: b, dbPath: 'host-b.db', localToken: t(),
    callers: [{ id: 'host-a', token: tokAtoB, pushOrigin: a }, { id: 'host-b', token: tokBtoB, pushOrigin: b }],
    agents: [{ id: 'codex-dev', provider: 'codex', workspace: ws, allowedSenders: ['host-a', 'host-b'], allowedPeers: ['claude-dev'], autoResume: true }],
    peers: [{ id: 'claude-dev', url: `${a}/agents/claude-dev`, token: tokBtoA }, { id: 'codex-dev', url: `${b}/agents/codex-dev`, token: tokBtoB }],
    policy,
  };
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const files = [join(dir, 'host-a.json'), join(dir, 'host-b.json')];
  if (!force && files.some(existsSync)) fail('CONFLICT', `${dir} already has configs (use --force to overwrite)`);
  writeFileSync(files[0], JSON.stringify(hostA, null, 2), { mode: 0o600 });
  writeFileSync(files[1], JSON.stringify(hostB, null, 2), { mode: 0o600 });
  for (const f of files) chmodSync(f, 0o600); // mode above applies only to newly created files (--force)
  out({ schemaVersion: '1.0', ok: true, data: { files, workspace: ws } });
}

const USAGE = `mesh <command> [--config FILE]
  init-demo [--dir ~/.config/agent-mesh] [--workspace DIR] [--force]
  runtime                       start the node runtime (foreground)
  doctor
  agent list | agent inspect ID
  send --to ID --message TEXT [--idempotency-key KEY]   (always async)
  task get ID | task cancel ID
  inbox | status`;

async function main() {
  const { values: v, positionals: p } = parseArgs({
    allowPositionals: true,
    options: {
      config: { type: 'string' }, to: { type: 'string' }, message: { type: 'string' }, 'idempotency-key': { type: 'string' },
      dir: { type: 'string' }, workspace: { type: 'string' }, force: { type: 'boolean' },
      json: { type: 'boolean' }, async: { type: 'boolean' }, // accepted for spec compatibility; output is always JSON and send is always async
    },
  });
  const cfg = v.config;
  const [cmd, sub, arg] = p;
  if (cmd === 'runtime') {
    const { startRuntime } = await import('./runtime.ts');
    const c = loadConfig(cfg);
    const rt = await startRuntime(c).catch((e: NodeJS.ErrnoException) => e.code === 'EADDRINUSE'
      ? fail('CONFLICT', `Port ${c.port} is already in use: this runtime is probably already running (check with: mesh status --config ${c.path}). Stop it first to start another.`)
      : Promise.reject(e));
    console.error(`[mesh] ${c.nodeId} listening on ${rt.url} (agents: ${c.agents.map((a) => a.id).join(', ')})`);
    const stop = () => { console.error('[mesh] stopping'); void rt.close().then(() => process.exit(0)); };
    process.on('SIGINT', stop);
    process.on('SIGTERM', stop);
    return;
  }
  if (cmd === 'init-demo') return initDemo(v.dir ?? join(homedir(), '.config/agent-mesh'), v.workspace ?? process.cwd(), Boolean(v.force));
  if (cmd === 'doctor') return doctor(cfg);
  if (cmd === 'send') return call(cfg, 'send', { to: v.to, message: v.message, idempotencyKey: v['idempotency-key'] });
  if (cmd === 'agent' && sub === 'list') return call(cfg, 'agents');
  if (cmd === 'agent' && sub === 'inspect' && arg) return call(cfg, 'inspect', { id: arg });
  if (cmd === 'task' && (sub === 'get' || sub === 'cancel') && arg) return call(cfg, sub, { id: arg });
  if (cmd === 'inbox' || cmd === 'status') return call(cfg, cmd);
  process.stderr.write(USAGE + '\n');
  fail('INVALID_ARGUMENT', `unknown command: ${p.join(' ')}`);
}

main().catch((e) => fail((e as { code?: string }).code && EXIT[(e as { code: string }).code] ? (e as { code: string }).code : 'INTERNAL_ERROR', (e as Error).message));
