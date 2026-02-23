# MCP Client Build Spec v2.2 (Final Tightening Pass)

This is v2.1 rewritten with the remaining precision gaps closed. Everything below is now explicit and buildable.

---

## 0) Decisions added in v2.2

1. **stdio stderr policy**: stream, cap, and surface last N lines per server in observability state.
2. **schema drift removal**: diff tool sets on every successful `list_tools()` and unregister stale tools (ownership-checked).
3. **hot reconfigure**: **explicit non-goal in v2.2**. Add a `reconfigure()` hook placeholder, but no runtime reload behavior yet.
4. **subprocess kill sequence**: SIGTERM -> wait -> SIGKILL for stdio. No orphan `npx`.
5. **semaphore lifecycle across reconnect**: epoch-scoped semaphore and waiters are failed on disconnect.
6. **artifact persistence failure fallback**: fallback to hash-only metadata, never fail tool call due to disk/artifact-store issues.
7. **list_tools timeout**: enforced using server timeout.
8. **health check cadence**: optional periodic ping for SSE. Default enabled with a sane interval.
9. **busy error behavior**: explicitly retriable with short delay, not argument repair.
10. **serialization invariant test**: add test that asserts two concurrent calls execute sequentially.

---

## 1) Stdio stderr/stdout handling policy

### stderr handling (required)

MCP stdio servers commonly write logs and tracebacks to stderr. We implement:

* Per server, maintain a ring buffer of the last `stderr_lines_max` lines (default 200).
* Stream stderr lines to logger at `DEBUG` level, with throttling for extremely chatty servers.
* Include `stderr_tail` in manager observability state (redacted for secrets if needed).

Config:

* `stderr_lines_max: int = 200`
* `stderr_log_level: str = "DEBUG"`
* `stderr_rate_limit_per_sec: int = 50` (drop beyond, but keep in ring buffer)
* `stderr_line_max_chars: int = 2000` (truncate long lines)

Memory safety:

* ring buffer fixed size
* per-line truncate

### stdout handling

stdout is reserved for MCP protocol. Do not log stdout. Ever.

### Observability snapshot includes

* `last_stderr_lines: list[str]` (tail)
* `last_stderr_at: datetime | None`

---

## 2) Schema drift removal (tool set diff)

On every successful `list_tools()`:

* Compute `new_set = {namespaced(server, tool) for tool in list_tools}`
* Compute `old_set = current_known_tools_for_server_epoch_or_server` (see below)
* `stale = old_set - new_set`
* For each tool_name in stale:

  * Unregister only if ownership matches current server ownership for that tool name.
  * Remove from ownership ledger.

This prevents "zombie tools" that no longer exist on the remote server.

Important nuance with degraded mode:

* Even though we keep tools registered through disconnect windows, we still remove tools that the server definitively no longer advertises after reconnect.

Data structure refinement:

* Maintain `self._known_tools_by_server: dict[str, set[str]]` representing latest advertised tool names for that server (regardless of epoch).
* On reconnect, update it to `new_set`.
* Diff against previous value for drift removal.

---

## 3) Hot config reload / reconfigure

### Explicit non-goal for v2.2

No automatic runtime config reload.

However, we add a clean seam so nobody hacks `_servers` mid-flight:

* Add method signature:

  * `async def reconfigure(self, registry: ToolRegistry, servers: list[MCPServerConfig]) -> None`
* In v2.2, it raises NotImplementedError with a clear log message.
* Document that server add/remove requires restart.

This prevents "silent partial reconfig" bugs.

---

## 4) Subprocess cleanup (stdio) with kill sequence

On stop or disconnect cleanup for stdio servers:

1. Attempt graceful transport/session close.
2. If subprocess still alive:

   * send SIGTERM
   * wait `kill_grace_seconds` (default 1.0)
3. If still alive:

   * send SIGKILL
   * wait `kill_force_seconds` (default 1.0)
4. Reap process (`await proc.wait()`), handle exceptions.

Config:

* `kill_grace_seconds: float = 1.0`
* `kill_force_seconds: float = 1.0`

This avoids orphan `npx` processes and zombie children.

---

## 5) Semaphore lifecycle across reconnect and disconnect

Problem: waiters can block forever if server disconnects while they're waiting to acquire concurrency permits.

Decision:

* **Epoch-scoped execution gate** per server:

  * `self._epoch_gate[server] = EpochGate(epoch_id, semaphore, generation_counter)`
* On disconnect or epoch change:

  * replace the gate with a new one
  * all new callers use the new gate
  * waiting callers on old gate must fail quickly

Implementation approach:

* Replace raw `asyncio.Semaphore` with an `EpochSemaphore` wrapper that includes an `epoch_id` and a `closed` flag.
* When disconnect occurs:

  * mark gate closed
  * wake waiters by releasing a condition or by checking closed state during acquire loop
* Acquire logic:

  * attempt to acquire with timeout
  * after acquiring, verify gate is still current and not closed
  * if closed, release immediately and return disconnected

This ensures disconnect propagates to queued work.

Config:

* `call_concurrency: int = 1`
* `acquire_timeout: float = 2.0`

---

## 6) Artifact/binary persistence failure fallback

Binary persistence logic:

* Try to persist payload if size > inline cap

  * Primary: artifact_store (preferred)
  * Secondary: cache dir write
* If persistence fails (disk full, permission, store unavailable):

  * Do not fail tool call
  * Fall back to:

    * `metadata.binary = [{sha256, bytes, mime, kind}]` without ref
    * `content` placeholder includes "(not persisted)"
  * Log warning with server/tool and exception

This prevents disk issues from cascading into tool failures.

---

## 7) Timeouts applied consistently

Use `cfg.timeout` for:

* `initialize()`
* `list_tools()`
* `call_tool()`

Each wrapped in `asyncio.wait_for`.

Also apply a timeout to shutdown waits:

* `shutdown_timeout` (default 5.0) for task completion.

---

## 8) Health checks and cadence

### Stdio

* Health is primarily subprocess liveness (proc exit is decisive).
* Optional ping still ok but not required.

### SSE

SSE connections can silently stall. Add proactive health check:

Config:

* `ping_interval_seconds: float = 15.0` default enabled for SSE
* `ping_timeout_seconds: float = min(5.0, cfg.timeout)` default

Behavior:

* After connect+register, task enters a loop:

  * if stop_event set: exit
  * else every ping interval:

    * run `ping()` or equivalent with timeout
    * if ping fails: treat as disconnect, mark degraded, reconnect loop

If MCP lib lacks ping:

* Attempt a lightweight call like `list_tools()` as health check (still timeout protected) but be careful not to spam. Prefer a dedicated ping if available.

---

## 9) Error code taxonomy and orchestrator semantics

Codes:

* `timeout`: tool execution exceeded cfg.timeout
* `disconnected`: server not connected or gate closed
* `user_error`: argument validation/mismatch (best-effort classifier)
* `remote_error`: tool failed server-side
* `exception`: wrapper/transport/client error
* `busy`: could not acquire execution permit within acquire_timeout

Orchestrator guidance (explicit):

* `user_error`: do not retry blindly. Repair args and replan.
* `remote_error`: retry only if you have idempotency and evidence of transient failure; otherwise surface.
* `timeout`: retriable once with backoff, then surface.
* `disconnected`: retriable after reconnect; optionally wait for `tools_changed` "connected".
* `busy`: retriable after short delay (e.g., 250ms to 1s jitter). Not an argument repair signal.

Include `metadata.retry_after_ms` for `busy` and `disconnected` when possible.

---

## 10) list_tools / tools_changed event emission rules

Emit `tools_changed` on:

* first connect success per server
* transition connected -> degraded
* degraded -> connected
* schema drift removal (tools removed)
* new tool discovered (tools added)
* shutdown removal (tools unregistered)

Payload includes:

* `server`, `status`, `epoch`, `tools_added`, `tools_removed`, `tools_total`

This makes UI updates cheap without polling. Polling can remain as fallback.

---

## 11) Server loop: updated step-by-step

Per server task:

1. Resolve secrets in env/headers.

   * If missing: mark `misconfigured`, emit `tools_changed(status="misconfigured")`, sleep 30s, retry.
2. Backoff state init: delay=initial.
3. Loop until stop:

   * epoch_id = uuid4
   * create new epoch gate (semaphore + closed=False)
   * connect transport (stdio or sse)

     * stdio: spawn proc, start stderr reader task with ring buffer
   * initialize() with timeout
   * list_tools() with timeout
   * sanitize schemas
   * compute namespaced set `new_set`
   * diff vs `known_set` for that server:

     * removed -> unregister ownership-checked
     * added/changed -> register/overwrite per policy
   * set `known_set = new_set`
   * mark status connected, store epoch
   * emit tools_changed with added/removed
   * enter health loop until:

     * stop_event OR
     * disconnect detected (proc exit, ping fail, transport close)
   * on disconnect:

     * mark status degraded (tools remain registered)
     * close epoch gate (wake waiters)
     * emit tools_changed(status="degraded")
     * cleanup transport/proc (kill sequence)
     * backoff sleep with jitter
     * reset backoff if stable >= stable_reset_seconds

---

## 12) Tests (additions)

Add these tests to the existing list:

1. **stderr ring buffer**

   * simulate stderr spam, verify buffer caps and truncation
   * verify observability includes last N lines

2. **schema drift removal**

   * initial list_tools returns A,B
   * reconnect list_tools returns A only
   * assert B unregistered

3. **semaphore waiters released on disconnect**

   * start one call holding the permit
   * start second call waiting to acquire
   * simulate disconnect (close gate)
   * assert second call returns disconnected/busy rather than hanging

4. **list_tools timeout**

   * list_tools never returns, assert server loop doesn't hang and enters backoff

5. **health check cadence**

   * SSE ping fails, assert transition to degraded and reconnect attempt

6. **busy serialization invariant**

   * Two concurrent calls:

     * call1 blocks inside mocked call_tool until event set
     * call2 starts and must not enter call_tool until call1 finishes
   * Assert ordering of mock call_tool invocations

---

## 13) Non-goals (explicit)

* Runtime add/remove servers without restart (reconfigure placeholder only).
* Surfacing MCP prompts/resources as tools in v2.2.
* Sampling and progress still V1 behavior (sampling rejected, progress ignored but hook-ready).
