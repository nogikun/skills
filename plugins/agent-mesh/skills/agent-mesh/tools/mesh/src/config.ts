// Node config (AM-IF-001 §8, prototype subset) and binary resolution.
import { readFileSync, existsSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, delimiter, resolve, dirname } from 'node:path';

export type Agent = {
  id: string;
  provider: 'claude' | 'codex';
  workspace: string;
  allowedSenders: string[];        // caller ids allowed to submit tasks to this agent
  allowedPeers?: string[];         // peers this agent may delegate to
  autoResume?: boolean;            // resume this agent's session when delegated results arrive (opt-in)
  allowedTools?: string[];         // claude: --allowedTools (no permission bypass)
  extraArgs?: string[];            // provider specific, e.g. codex -c overrides
  command?: string[];              // override binary (tests use a fake provider)
};
export type Peer = { id: string; url: string; token: string };            // url = A2A JSON-RPC endpoint
export type Caller = { id: string; token: string; pushOrigin: string };  // who may call us, and where we may push
export type Config = {
  schemaVersion: '1.0';
  nodeId: string;
  port: number;
  publicUrl: string;
  dbPath: string;
  // shortcut: tokens live in this 0600 file, not the OS keychain (credentialRef); upgrade before multi-user/remote use
  localToken: string;
  callers: Caller[];
  agents: Agent[];
  peers: Peer[];
  policy: { maxConcurrentTurns: number; defaultDeadlineSeconds: number };
  allowApiKeyBilling?: boolean;
};

export function loadConfig(path = process.env.MESH_CONFIG): Config & { path: string } {
  if (!path) throw Object.assign(new Error('No config: pass --config or set MESH_CONFIG'), { code: 'CONFIG_INVALID' });
  const abs = resolve(path);
  if ((statSync(abs).mode & 0o077) !== 0) {
    throw Object.assign(new Error(`${abs} must not be readable by group/others (chmod 600)`), { code: 'CONFIG_INVALID' });
  }
  const c = JSON.parse(readFileSync(abs, 'utf8')) as Config;
  if (c.schemaVersion !== '1.0' || !c.nodeId || !c.port || !c.localToken) {
    throw Object.assign(new Error('Invalid config'), { code: 'CONFIG_INVALID' });
  }
  return { ...c, dbPath: resolve(dirname(abs), c.dbPath), path: abs };
}

const FALLBACKS: Record<string, string[]> = {
  claude: [join(homedir(), '.local/bin/claude')],
  codex: [
    '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex',
    join(homedir(), '.codex/plugins/.plugin-appserver/codex-cli/bin/codex'),
  ],
};

export function resolveBin(name: 'claude' | 'codex'): string | null {
  const env = process.env[`MESH_${name.toUpperCase()}_BIN`];
  if (env) return env;
  for (const d of (process.env.PATH ?? '').split(delimiter)) {
    if (d && existsSync(join(d, name))) return join(d, name);
  }
  return FALLBACKS[name].find((p) => existsSync(p)) ?? null;
}

export const BIN_DIR = resolve(import.meta.dirname, '../bin');
