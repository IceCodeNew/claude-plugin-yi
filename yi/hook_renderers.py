"""Render dormant native adapters for the reviewed command-hook protocol."""

import json
import re
from pathlib import Path

SUPPORTED_EVENTS = {
    "pi": frozenset({"PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"}),
    "ampcode": frozenset({"PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "Stop"}),
}


def render_hook_adapter(target: str, plugin: str, runtime_relative: Path, events: dict) -> dict[str, bytes]:
    """Embed configuration in a native entrypoint without importing or running hooks."""
    templates = {"pi": "hook_pi.mjs", "ampcode": "hook_amp.mjs"}
    destinations = {
        "pi": f"pi/home/.pi/agent/extensions/yi-{plugin}-hooks.mjs",
        "ampcode": f"ampcode/home/.config/amp/plugins/yi-{plugin}-hooks.js",
    }
    if target not in templates:
        msg = f"No native hook adapter for target: {target}."
        raise ValueError(msg)
    source = Path(__file__).with_name(templates[target]).read_text(encoding="utf-8")
    replacements = {
        "__PLUGIN_JSON__": json.dumps(plugin, ensure_ascii=False),
        "__RUNTIME_RELATIVE_JSON__": json.dumps(runtime_relative.as_posix(), ensure_ascii=False),
        "__EVENTS_JSON__": json.dumps(events, ensure_ascii=False, sort_keys=True),
    }
    # One pass prevents source data containing another placeholder from being rewritten.
    source = re.sub(
        r"__PLUGIN_JSON__|__RUNTIME_RELATIVE_JSON__|__EVENTS_JSON__", lambda match: replacements[match[0]], source
    )
    return {destinations[target]: source.encode("utf-8")}
