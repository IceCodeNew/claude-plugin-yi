---
name: verify
description: Observe yi migration through its CLI and real target harnesses, keeping captured evidence outside the checkout.
---

# Verify yi migration

Runtime observation only: tests and type checks are separate development gates.

1. Inspect the full committed and uncommitted diff. Record branch/commit and target CLI versions. Put fixtures, artifacts, target HOME directories, and captured output under a fresh `mktemp -d`; never put credentials or transcripts in the checkout.
2. Create a task-owned plugin manifest and inspected harmless command hook. Its command should read stdin JSON, append event/tool IDs to a receipt file in `cwd`, deny one unique tool command, and emit unique startup/post-tool context. Use a once-only Stop guard. Never execute arbitrary retained plugin hooks.
3. Drive `python3 scripts/yi.py --data-dir DATA migrate --source SOURCE --target TARGET --output ARTIFACTS --dry-run --json`, then generation and `install` preview. Apply only into the disposable HOME with explicit acceptance of unverified results. Rename the original source to prove the installed runtime does not depend on it.
4. Launch the actual target with the disposable HOME and exact `YI_ENABLE_MIGRATED_HOOKS=PLUGIN_NAME`. Reference existing authorized authentication through supported target mechanisms; do not read, copy, or symlink credential files. Use absolute CLI paths because version-manager shims may fail under another HOME.
5. Ask the native agent to attempt the unique denied command, avoid bypass/retries, execute a harmless allowed command, report startup/post-tool receipts, and obey Stop feedback. Capture native events or the TUI pane plus receipt IDs and filesystem side effects. A script receipt alone does not prove context delivery or policy enforcement.
6. Probe adjacent paths: rejected user input, no activation variable, repeated session/request, malformed output or timeout, and input updates where claimed. Denied calls must not acquire success post-hook receipts. Preserve real executed errors/results.

## Handles and gotchas

- Pi: native RPC with an explicitly selected generated extension, `--no-extensions --no-skills --no-context-files --no-prompt-templates --no-session`. `PI_CODING_AGENT_DIR` can reference the authorized existing agent directory while `HOME` points to the disposable runtime. Wait for `agent_settled`, not just `agent_end`, to observe Stop continuation.
- Amp: isolate tmux with `tmux -L yi-verify`; use disposable `HOME` and settings file. Supported auth reuse may reference existing `XDG_DATA_HOME`. Capture `tool.result` correlation: Amp emits it even for a rejected call. Inspect optional existing integration environment variables before launch; unset unrelated task integrations rather than activating them.
- OpenCode v2: private localhost `serve` process, bind a fresh port, obtain its ephemeral server password without printing it, then drive `/api/session` and `/api/session/<id>/prompt`. Generated Effect packages require separately reviewed pinned SDK dependencies; installing them is setup, not proof. Startup context must remain visible across primary requests while the source startup command runs once.
- Codex: top-level TUI does not support exec-only `--ignore-user-config` flags. Existing authentication uses `CODEX_HOME`, which also owns user hooks. A disposable project `.codex/hooks.json` can register inspected generated hooks but is not globally config-isolated. Inspect all native `/hooks` sources. Never grant new persisted hook trust or use `--dangerously-bypass-hook-trust` without explicit authorization. Record trust-blocked behavior as BLOCKED, not PASS.

Report PASS/FAIL/BLOCKED/SKIP per scope with native output excerpts inline and local evidence paths. File generation, registration, resource discovery, source execution, context delivery, and complete plugin parity are different claims.
