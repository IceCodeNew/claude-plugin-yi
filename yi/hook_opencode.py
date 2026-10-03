"""Render an inert, explicitly gated OpenCode v2 hook package."""

import json
import re
from pathlib import Path

SUPPORTED_EVENTS = frozenset({"PreToolUse", "PostToolUse", "SessionStart"})
SEMANTIC_NOTES = (
    "OpenCode v2 Effect hooks are required for controlled pre-tool denial; the v1 plugin API is not used.",
    (
        "SessionStart additional context runs once at the first primary request observed by this adapter, "
        "not at session creation. Its cached context is included in each primary request without "
        "rerunning the source command. Its source is startup, not a claim of resume detection. "
        "Source environment exports and full SessionStart timing are unavailable. "
        "A failed startup hook logs once and leaves startup context empty; later requests continue. "
        "Unsupported startup control outputs are diagnosed, not enforced as a session barrier."
    ),
    (
        "Pre-tool additional context is queued for the next primary request. Post-tool context is appended "
        "to result content and textual output; structured output is preserved. "
        "continue:false at PreToolUse rejects the tool call, not the whole session; "
        "at SessionStart or PostToolUse it is reported as unsupported."
    ),
    (
        "UserPromptSubmit cancellation, Stop barriers, and SessionEnd semantics are unsupported. "
        "Native notifications are not equivalent source lifecycle barriers. "
        "Hook notices go to diagnostic stderr, not model context or native UI notifications."
    ),
    (
        "Built-in shell/read/write/edit/glob/grep names and inputs are translated. Other tools, including "
        "multi-file patch tools, keep their native names and require target-specific matchers. "
        "updatedInput is supported only for reviewed built-ins and fields; unsupported or mistyped "
        "updates reject the tool call. Unrelated original native fields are retained. "
        "PostToolUse receives the native result "
        "object, not a universally Claude-shaped response. A post-tool deny adds feedback after the "
        "side effect; it cannot undo execution."
    ),
    (
        "Registration alone does not execute source hooks. YI_ENABLE_MIGRATED_HOOKS must contain the exact "
        "plugin name as a comma-separated token. Review and install the declared pinned SDK dependencies separately."
    ),
)


def render_opencode(plugin: str, config: dict) -> tuple[dict[str, bytes], dict]:
    """Return package files under target HOME and an optional native config contribution."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin):
        msg = "Plugin name must use lowercase letters, digits, and hyphens."
        raise ValueError(msg)
    if config.get("plugin") != plugin:
        msg = "Hook configuration must identify the rendered plugin."
        raise ValueError(msg)
    unsupported = set(config.get("events", {})) - SUPPORTED_EVENTS
    if unsupported:
        msg = "Unsupported OpenCode hook events: " + ", ".join(sorted(unsupported))
        raise ValueError(msg)
    package = Path(".local/share/yi/hooks") / plugin / "opencode"
    destination = Path("opencode-v2/home") / package
    metadata = {
        "name": f"yi-{plugin}-hooks",
        "private": True,
        "type": "module",
        "main": "index.js",
        "dependencies": {
            "@opencode/plugin": "2.0.19",
            "@opencode/schema": "2.0.19",
            "effect": "4.0.0-rc.112",
        },
    }
    template = Path(__file__).with_suffix(".mjs").read_text(encoding="utf-8")
    # The source root is resolved beside the installed runner, never against the conversion host.
    adapter = template.replace("__YI_HOOK_CONFIG__", json.dumps(config, ensure_ascii=True))
    files = {
        str(destination / "package.json"): (json.dumps(metadata, indent=2) + "\n").encode(),
        str(destination / "index.js"): adapter.encode(),
    }
    # Verified against v2.0.19 config/variable.ts and config/plugin/source.ts:
    # {env:HOME} expands in config text; configured local targets must be directories.
    return files, {"plugins": [f"{{env:HOME}}/{package.as_posix()}"]}
