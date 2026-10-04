# Hook migration

Hook conversion is deterministic. yi translates supported command handlers into a shared source-protocol runner and target-native callbacks. It does not ask a model to rewrite source logic, execute hooks during generation, install dependencies, or grant trust.

## Activation and resources

The generated runtime lives under `.local/share/yi/hooks/<plugin>/` in the target HOME. Its `source/` tree preserves inspected source-relative resources, imports, and license files. Known credentials, local state, symlinks, and unsupported files are refused or excluded; this is not a universal secret scanner or sandbox. Host programs such as Python, Bash, Node, and source-specific libraries must already be available. Preview diagnostics identify retained executable resources.

Registration is not permission to execute source handlers. After reviewing the generated files and their dependencies, enable only the intended plugin in the target process:

```sh
YI_ENABLE_MIGRATED_HOOKS=my-plugin target-cli
```

Use comma-separated exact plugin names to enable multiple plugins. An empty value or `*` does not enable them. Codex additionally requires its normal native hook trust approval. yi does not generate trusted hashes or use hook-trust bypass flags. Installation and `check --native` do not activate source handlers.

OpenCode v2 adapters are native Effect packages with pinned SDK dependencies declared in their `package.json`. Review and install those dependencies separately in the installed package directory. yi does not install them automatically. The native loader may still require dependencies even when source execution is disabled.

## Event boundaries

| Source event | Codex | Amp Code | Pi | OpenCode v2 |
| --- | --- | --- | --- | --- |
| PreToolUse | Native hook | `tool.call` | `tool_call` | `execute.before` |
| PostToolUse | Native hook | `tool.result` for calls not rejected by this adapter | `tool_result` | Completed `execute.after` |
| SessionStart | Native hook | `session.start`; context at next agent start | `session_start`; context at next agent start | First observed primary request; cached context on each primary request |
| UserPromptSubmit | Native hook | `agent.start`, including plugin continuations | `input`, before skill/template expansion | Blocked: no equivalent cancellation barrier |
| Stop | Native hook | Done `agent.end` with bounded continuation | Completed `agent_before_settle` with bounded continuation | Blocked: no equivalent pre-stop barrier |
| SessionEnd | Native hook, short native deadline | Blocked | `session_shutdown`, approximate reason mapping | Blocked |
| PreCompact / PostCompact | Native hooks | Blocked | Blocked | Blocked |
| SubagentStart / SubagentStop | Native hooks | Blocked | Blocked | Blocked |
| PermissionRequest / Interrupt | Native hooks | Blocked | Blocked | Blocked |

The generated report remains **unverified** until the installed adapter is observed in the target. A supported event does not imply every source hook output or target tool schema is equivalent. Unsupported handlers are diagnosed independently so one model/prompt handler does not discard adjacent portable command handlers.

## Protocol translation

Matching handlers receive JSON through stdin with source event names, target-observable session and workspace fields, and translated built-in tool names/inputs. The runner supplies `CLAUDE_PLUGIN_ROOT`, `PLUGIN_ROOT`, and `CLAUDE_PROJECT_DIR` for relocated resources. It bounds input/output and execution time, and runs matching foreground handlers concurrently. Unsupported matcher syntax is refused rather than evaluated as unrestricted regular expressions.

- Pre-tool denial prevents the selected tool call. Permission overrides are not automatically granted. Reviewed built-in input updates are translated back to native fields; unknown/custom tool updates are rejected. Pi queues pre-tool context for its next context callback; Amp defers it to the subsequent tool result. These are not identical pre-execution context delivery.
- Post-tool context is feedback after execution, not rollback. Native outputs and errors are preserved. Some targets deliver post callbacks for executed failures as well as successes.
- SessionStart plain stdout and supported `additionalContext` become model-visible context. Target timing and message roles may differ.
- UserPromptSubmit denial suppresses input where the target has a cancellation boundary.
- Stop denial requests continuation; Amp and Pi bound it to five continuations. `stop_hook_active` is a guard, not proof of identical continuation provenance.
- `systemMessage` is a notice, not model context: Amp/Pi use native UI, Codex uses its native hook output, and OpenCode uses diagnostic stderr.

## Deliberate limitations

Prompt/agent hooks, background commands, conditional/rewake fields, and unreviewed shells are not silently translated. LSP migration is excluded. No environment-export persistence through `CLAUDE_ENV_FILE` is provided. Native transcript formats are not converted into Claude transcripts; absent fields are not fabricated. Pi SessionEnd observes runtime teardown, including reload, not exclusively final session exit. Codex patch payloads are not losslessly Claude Write/Edit payloads. Amp uses its workspace root and has no reliable user-versus-plugin continuation provenance. PreToolUse `continue:false` blocks the tool on Amp/Pi/OpenCode; it is not a full-session abort. OpenCode startup delivery is not a session-created or resume barrier, and `continue:false` cannot universally halt an OpenCode session.

Inspect each diagnostic before activation. Native discovery, hook registration, source script execution, policy enforcement, context delivery, and whole-plugin behavioral parity are separate claims.
