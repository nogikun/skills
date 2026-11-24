// A2A v0.3 JSON-RPC subset: message/send (non-blocking + push config), tasks/get, tasks/cancel, Agent Card.
// shortcut: hand-written minimal binding instead of the a2a CLI / official SDK (ADR-01); swap in once G0 pins a version.
import { randomUUID } from 'node:crypto';
import { fromA2A, toA2A, type TaskState } from './domain.ts';
import type { TaskRow } from './store.ts';

export const PROTOCOL_VERSION = '0.3.0';
export const META = 'agent-mesh/'; // mesh extension keys travel in metadata, never required from generic peers

export class MeshError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

type Part = { kind: string; text?: string };
export type A2ATask = {
  kind: 'task';
  id: string;
  contextId: string;
  status: { state: string; timestamp: string; message?: { kind: 'message'; role: 'agent'; messageId: string; parts: Part[] } };
  artifacts?: { artifactId: string; name: string; parts: Part[] }[];
  metadata?: Record<string, unknown>;
};
export type A2AMessage = { kind: 'message'; messageId: string; contextId?: string; parts: Part[] };

export const textOf = (parts: Part[] | undefined) =>
  (parts ?? []).filter((p) => p.kind === 'text' && typeof p.text === 'string').map((p) => p.text).join('\n');

/** Server side: our task row -> A2A Task (no local paths, session ids or secrets). */
export function taskToA2A(t: TaskRow): A2ATask {
  const out: A2ATask = {
    kind: 'task',
    id: t.task_id,
    contextId: t.context_id ?? t.task_id,
    status: { state: toA2A(t.state), timestamp: t.updated_at },
  };
  if (t.error) out.status.message = { kind: 'message', role: 'agent', messageId: `${t.task_id}-status`, parts: [{ kind: 'text', text: t.error }] };
  if (t.result) out.artifacts = [{ artifactId: `${t.task_id}-result`, name: 'result', parts: [{ kind: 'text', text: t.result }] }];
  return out;
}

export type Snapshot = {
  responseKind: 'task' | 'message';
  remoteTaskId: string | null;
  contextId: string | null;
  state: TaskState | null; // null = unknown remote state
  rawState: string;
  result: string | null;
  error: string | null;
};

/** Client side: normalize a Task or direct Message response. */
export function snapshot(r: A2ATask | A2AMessage): Snapshot {
  if (r?.kind === 'message') {
    return { responseKind: 'message', remoteTaskId: null, contextId: r.contextId ?? null, state: 'COMPLETED', rawState: 'message', result: textOf(r.parts), error: null };
  }
  if (r?.kind !== 'task' || typeof r.id !== 'string' || typeof r.status?.state !== 'string') {
    throw new MeshError('INVALID_AGENT_RESPONSE', 'Peer returned neither a Task nor a Message');
  }
  const result = (r.artifacts ?? []).map((a) => textOf(a.parts)).filter(Boolean).join('\n') || null;
  return {
    responseKind: 'task',
    remoteTaskId: r.id,
    contextId: r.contextId ?? null,
    state: fromA2A(r.status.state),
    rawState: r.status.state,
    result,
    error: textOf(r.status.message?.parts) || null,
  };
}

// errors that happen before any request byte can reach the peer
const PRE_SEND = new Set(['ECONNREFUSED', 'ENOTFOUND', 'EHOSTUNREACH', 'ENETUNREACH', 'EAI_AGAIN', 'UND_ERR_CONNECT_TIMEOUT']);

/**
 * JSON-RPC call. Connection refused => TRANSPORT_ERROR (provably not accepted).
 * Anything after the request may have been sent => OUTCOME_UNKNOWN (never blindly retried).
 */
export async function rpc<T>(url: string, token: string, method: string, params: unknown, timeoutMs = 15000): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      redirect: 'error',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: randomUUID(), method, params }),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    const code = (e as { cause?: { code?: string } }).cause?.code ?? '';
    if (PRE_SEND.has(code) || code.startsWith('CERT_') || code.includes('SELF_SIGNED')) throw new MeshError('TRANSPORT_ERROR', `Peer unreachable (${code})`);
    throw new MeshError('OUTCOME_UNKNOWN', `Request outcome unknown: ${(e as Error).name}`);
  }
  if (res.status === 401 || res.status === 403) throw new MeshError('FORBIDDEN', `Peer refused credentials (HTTP ${res.status})`);
  let body: { result?: T; error?: { code: number; message?: string; data?: { meshCode?: string } } | string };
  try {
    body = await res.json() as typeof body;
  } catch {
    // a non-JSON 4xx is an explicit refusal (not accepted); 5xx / 2xx garbage stays unknown
    if (res.status >= 400 && res.status < 500 && res.status !== 408) throw new MeshError('TRANSPORT_ERROR', `Peer rejected request (HTTP ${res.status})`);
    throw new MeshError('OUTCOME_UNKNOWN', `Unparseable peer response (HTTP ${res.status})`);
  }
  if (body.error && typeof body.error === 'object' && typeof body.error.code === 'number') {
    throw new MeshError(body.error.data?.meshCode ?? 'REMOTE_ERROR', body.error.message ?? `JSON-RPC error ${body.error.code}`);
  }
  if (!res.ok) { // plain HTTP error (e.g. {"error":"unknown agent"}), not a JSON-RPC response
    const msg = `Peer HTTP ${res.status}: ${typeof body.error === 'string' ? body.error : 'error'}`;
    throw new MeshError(res.status >= 500 || res.status === 408 ? 'OUTCOME_UNKNOWN' : 'TRANSPORT_ERROR', msg);
  }
  return body.result as T;
}

export async function fetchCard(rpcUrl: string) {
  const res = await fetch(`${rpcUrl}/.well-known/agent-card.json`, { redirect: 'error', signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new MeshError('NOT_FOUND', `Agent card HTTP ${res.status}`);
  return res.json();
}
