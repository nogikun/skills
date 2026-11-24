# mesh CLI reference

`MESH=<skill dir>/tools/mesh/bin/mesh`. Outside a mesh turn add `--config <node.json>` (or set `MESH_CONFIG`). `--json` and `--async` are accepted for compatibility; output is always one JSON document and `send` is always asynchronous.

| Command | Does |
|---|---|
| `init-demo [--dir DIR] [--workspace DIR] [--force]` | Write host-a/host-b configs (default dir `~/.config/agent-mesh`, must be outside the workspace) |
| `runtime` | Run a node runtime in the foreground |
| `doctor` | CLI paths, versions, login state, API-key env check, capabilities |
| `agent list` | Local agents and known peers |
| `agent inspect ID` | Fetch the peer's A2A Agent Card |
| `send --to ID --message TEXT [--idempotency-key KEY]` | Delegate; returns `requestId` immediately |
| `task get ID` | State and result (refreshes from the peer if not finished) |
| `task cancel ID` | Request cancellation (best effort) |
| `inbox` | Last 50 finished results, with auto-resume status |
| `status` | Counts of received/sent tasks, outbox and resumes |

## Success

```json
{
  "schemaVersion": "1.0",
  "ok": true,
  "traceId": "tr_…",
  "data": {
    "requestId": "req_…",
    "peerId": "codex-dev",
    "remoteTaskId": "tsk_…",
    "state": "SUBMITTED",
    "result": null,
    "error": null,
    "updatedAt": "2026-11-24T14:36:44.148Z"
  }
}
```

`task get` adds `cached` (true when the peer could not be asked) and `continuations` (auto-resume turns of a task this node executed: `status` PENDING / RUNNING / DONE / FAILED / NEEDS_ATTENTION / UNCERTAIN, and `output`).

## Failure

```json
{ "schemaVersion": "1.0", "ok": false, "error": { "code": "OUTCOME_UNKNOWN", "message": "…", "retryable": false, "requestId": "req_…" } }
```

Exit codes: 0 ok, 2 invalid argument/config, 3 auth/forbidden/sandbox blocked, 4 dependency missing, 5 not found, 6 transport error, 7 outcome unknown/conflict, 8 limit exceeded, 9 internal error.

## Node config (`~/.config/agent-mesh/host-*.json`)

Per agent: `allowedSenders` (which nodes may send to it), `allowedPeers` (whom it may delegate to), `autoResume`, `allowedTools` (Claude `--allowedTools`), `extraArgs` (extra CLI args, e.g. Codex `-c` overrides). Tokens live only in these files (mode 0600). Codex runs with `sandbox_mode="read-only"`; letting Codex itself call `mesh` needs loopback network access in its sandbox, which is a permission decision for the user.
