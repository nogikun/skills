// Domain: task state machine and limits. Pure functions only (AM-DD-001 §3).

export type TaskState =
  | 'SUBMITTED' | 'WORKING' | 'INPUT_REQUIRED' | 'AUTH_REQUIRED'
  | 'COMPLETED' | 'FAILED' | 'CANCELED' | 'REJECTED';

const NEXT: Record<TaskState, TaskState[]> = {
  SUBMITTED: ['WORKING', 'REJECTED', 'CANCELED', 'FAILED'],
  WORKING: ['INPUT_REQUIRED', 'AUTH_REQUIRED', 'COMPLETED', 'FAILED', 'CANCELED', 'REJECTED'],
  INPUT_REQUIRED: ['WORKING', 'CANCELED', 'FAILED'],
  AUTH_REQUIRED: ['WORKING', 'CANCELED', 'FAILED'],
  COMPLETED: [], FAILED: [], CANCELED: [], REJECTED: [],
};

export const isTerminal = (s: TaskState) => NEXT[s].length === 0;

/** Local execution side: only the transitions of the state diagram. */
export const canTransition = (from: TaskState, to: TaskState) => NEXT[from].includes(to);

/** Observed (remote) side: may skip intermediate states, but terminal never moves and nothing returns to SUBMITTED (late/reordered notifications). */
export const acceptObserved = (cur: TaskState, next: TaskState) => cur !== next && !isTerminal(cur) && next !== 'SUBMITTED';

const FROM_A2A: Record<string, TaskState> = {
  submitted: 'SUBMITTED', working: 'WORKING', 'input-required': 'INPUT_REQUIRED',
  'auth-required': 'AUTH_REQUIRED', completed: 'COMPLETED', failed: 'FAILED',
  canceled: 'CANCELED', rejected: 'REJECTED',
};

/** Unknown states return null: the caller keeps rawState and must NOT treat it as completed. */
export const fromA2A = (raw: string): TaskState | null => FROM_A2A[raw] ?? null;
export const toA2A = (s: TaskState) => s.toLowerCase().replace('_', '-');

export const LIMITS = {
  maxDepth: 3,
  maxTasksPerRoot: 10,
  maxInputBytes: 64 * 1024,
  maxResultBytes: 64 * 1024,
  resumeBatch: 5,
  resumeBytes: 32 * 1024,
};

export const byteLength = (s: string) => Buffer.byteLength(s, 'utf8');

const TRUNCATED = '\n[agent-mesh: truncated]';

/** Byte-bounded (marker included) and never splits a UTF-8 character. */
export function truncate(s: string, max: number) {
  const b = Buffer.from(s, 'utf8');
  if (b.length <= max) return s;
  let end = Math.max(0, max - byteLength(TRUNCATED));
  while (end > 0 && (b[end] & 0xc0) === 0x80) end--; // back up to a character boundary
  return b.subarray(0, end).toString('utf8') + TRUNCATED;
}

/** Returns an error code or null. depth = depth of the task that would be created. */
export function checkDelegation(depth: number, tasksInRoot: number, l = LIMITS): string | null {
  if (depth > l.maxDepth) return 'LIMIT_EXCEEDED: max delegation depth';
  if (tasksInRoot >= l.maxTasksPerRoot) return 'LIMIT_EXCEEDED: max tasks per root';
  return null;
}
