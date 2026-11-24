// Persistence: SQLite (node:sqlite, WAL). Only the Runtime process opens the DB (AM-DD-001 §4, subset).
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { TaskState } from './domain.ts';

export type TaskRow = {
  task_id: string;
  direction: 'in' | 'out';
  agent_id: string;            // in: local executing agent / out: local sending agent ('human' if none)
  peer_id: string;             // in: caller id / out: target peer id
  remote_task_id: string | null;
  context_id: string | null;
  response_kind: 'task' | 'message';
  state: TaskState;
  raw_state: string | null;
  input: string;
  result: string | null;
  error: string | null;
  idempotency_key: string;
  payload_hash: string;
  push_url: string | null;
  push_token: string | null;
  origin_task_id: string | null;
  root_id: string;
  depth: number;
  provider_session_id: string | null;
  deadline_at: number;
  revision: number;
  created_at: string;
  updated_at: string;
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  task_id TEXT PRIMARY KEY,
  direction TEXT NOT NULL CHECK(direction IN ('in','out')),
  agent_id TEXT NOT NULL, peer_id TEXT NOT NULL,
  remote_task_id TEXT, context_id TEXT,
  response_kind TEXT NOT NULL DEFAULT 'task',
  state TEXT NOT NULL, raw_state TEXT,
  input TEXT NOT NULL, result TEXT, error TEXT,
  idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
  push_url TEXT, push_token TEXT,
  origin_task_id TEXT, root_id TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0,
  provider_session_id TEXT, deadline_at INTEGER NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(direction, agent_id, peer_id, idempotency_key),
  UNIQUE(peer_id, remote_task_id)
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(direction, state);
CREATE INDEX IF NOT EXISTS tasks_root ON tasks(root_id);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_out_push ON tasks(push_token) WHERE direction = 'out';
CREATE TABLE IF NOT EXISTS inbox(
  event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
  dedupe_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox(
  delivery_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
  url TEXT NOT NULL, token TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL, last_error TEXT,
  UNIQUE(task_id, url)
);
CREATE TABLE IF NOT EXISTS continuations(
  continuation_id TEXT PRIMARY KEY, origin_task_id TEXT NOT NULL REFERENCES tasks(task_id),
  event_id TEXT NOT NULL REFERENCES inbox(event_id), status TEXT NOT NULL,
  output TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(origin_task_id, event_id)
);
CREATE TABLE IF NOT EXISTS audit(
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  action TEXT NOT NULL, actor TEXT NOT NULL, target TEXT NOT NULL, outcome TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations VALUES (1, datetime('now'));
`;

export type Db = DatabaseSync;

export function openStore(path: string): Db {
  mkdirSync(dirname(path), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON;');
  db.exec(SCHEMA);
  return db;
}

export function tx<T>(db: Db, fn: () => T): T {
  db.exec('BEGIN IMMEDIATE');
  try {
    const r = fn();
    db.exec('COMMIT');
    return r;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

export const now = () => new Date().toISOString();

export const getTask = (db: Db, id: string) =>
  db.prepare('SELECT * FROM tasks WHERE task_id = ?').get(id) as TaskRow | undefined;

/** Audit never stores prompt bodies or secrets. */
export function audit(db: Db, action: string, actor: string, target: string, outcome: string) {
  db.prepare('INSERT INTO audit(at, action, actor, target, outcome) VALUES (?,?,?,?,?)').run(now(), action, actor, target, outcome);
}
