"""Prepare native command-hook adapters without executing source handlers."""

import json
import math
import re
from pathlib import Path

from yi.hook_assets import collect_hook_runtime
from yi.hook_opencode import SEMANTIC_NOTES, render_opencode
from yi.hook_opencode import SUPPORTED_EVENTS as OPENCODE_EVENTS
from yi.hook_renderers import SUPPORTED_EVENTS, render_hook_adapter

CODEX_EXIT_EVENTS = frozenset({"SessionEnd", "Interrupt"})
CODEX_EXIT_TIMEOUT = 3
MAX_MATCHER_LENGTH = 512
MAX_COMMAND_TIMEOUT = 3600

CODEX_EVENTS = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "Stop",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "PermissionRequest",
        "Interrupt",
    }
)


def prepare_hooks(source: Path, plugin: str, target: str, events: dict) -> tuple[dict[str, bytes], list[dict]]:
    """Convert supported handlers separately and retain exact incompatibility reasons."""
    converted, diagnostics = plan_handlers(source, plugin, target, events)
    if not converted:
        return {}, diagnostics
    runtime = Path(".local/share/yi/hooks") / plugin
    destination = Path(target) / "home" / runtime
    files, executable = collect_hook_runtime(source, destination / "source")
    retained_paths = {Path(name).relative_to(destination / "source") for name in files}
    converted, diagnostics = plan_handlers(source, plugin, target, events, retained_paths=retained_paths)
    if not converted:
        return {}, diagnostics
    files[str(destination / "runner.mjs")] = Path(__file__).with_name("hook_runtime.mjs").read_bytes()
    config = {"plugin": plugin, "root": "source", "events": converted}
    files[str(destination / "config.json")] = (json.dumps(config, indent=2) + "\n").encode()
    if target == "codex":
        files.update(codex_hooks(runtime, converted))
    elif target == "opencode-v2":
        generated, contribution = render_opencode(plugin, config)
        files.update(generated)
        files["opencode-v2/home/.config/opencode/opencode.json"] = (json.dumps(contribution, indent=2) + "\n").encode()
        for item in diagnostics:
            if item["status"] == "unverified":
                item["reason"] += " " + " ".join(SEMANTIC_NOTES)
    else:
        files.update(render_hook_adapter(target, plugin, runtime, converted))
    if executable:
        diagnostics.append(
            {
                "name": f"{plugin}:hooks",
                "kind": "hook-runtime",
                "status": "unverified",
                "reason": "Relocated source runtime retained; host dependencies are not installed.",
                "executables": executable,
            }
        )
    return files, diagnostics


def plan_handlers(
    source: Path,
    plugin: str,
    target: str,
    events: dict,
    *,
    retained_paths: set[Path] | None = None,
) -> tuple[dict, list]:
    """Plan each source event independently of runtime file rendering."""
    supported = (
        CODEX_EVENTS if target == "codex" else OPENCODE_EVENTS if target == "opencode-v2" else SUPPORTED_EVENTS[target]
    )
    converted = {}
    diagnostics = []
    for event, groups in events.items():
        owner = {"kind": "hooks", "name": f"{plugin}:hooks:{event}"}
        if event not in supported:
            diagnostics.append(
                {**owner, "status": "blocked", "reason": f"No equivalent native event barrier for {event} on {target}."}
            )
            continue
        if not groups:
            diagnostics.append({**owner, "status": "blocked", "reason": "No command handlers declared for this event."})
            continue
        retained = []
        for index, group in enumerate(groups):
            reason = group_blocker(group)
            if reason:
                diagnostics.append({**owner, "status": "blocked", "reason": f"Hook group {index}: {reason}"})
                continue
            handlers = []
            for number, handler in enumerate(group["hooks"]):
                reason = handler_blocker(handler, source, target, event, retained_paths)
                if reason:
                    diagnostics.append({**owner, "status": "blocked", "reason": f"Handler {index}/{number}: {reason}"})
                else:
                    handlers.append(handler)
            if handlers:
                retained.append({"matcher": group.get("matcher", ""), "hooks": handlers})
        if retained:
            converted[event] = retained
            reason = hook_reason(plugin, target, event)
            diagnostics.append({**owner, "status": "unverified", "reason": reason})
    return converted, diagnostics


def group_blocker(group: object) -> str | None:
    """Validate group structure and matching before examining executable handlers."""
    if not isinstance(group, dict) or set(group) - {"matcher", "hooks"} or not isinstance(group.get("hooks"), list):
        return "Unsupported fields or handler shape."
    return matcher_blocker(group.get("matcher"))


def handler_blocker(
    handler: dict, source: Path, target: str, event: str, retained_paths: set[Path] | None
) -> str | None:
    """Check one handler's executable, relocation, and native deadline compatibility."""
    reason = command_blocker(handler)
    if reason:
        return reason
    reason = root_command_blocker(handler, source_root=source, retained_paths=retained_paths)
    if reason:
        return reason
    if target == "codex" and event in CODEX_EXIT_EVENTS and handler.get("timeout", 60) > CODEX_EXIT_TIMEOUT:
        return (
            f"Codex {event} native timeout cap is {CODEX_EXIT_TIMEOUT}s (default 1s); "
            "declare a compatible source timeout explicitly. The source default is 60s."
        )
    return None


def hook_reason(plugin: str, target: str, event: str) -> str:
    """Describe explicit activation and nonidentical native delivery boundaries."""
    reason = (
        f"Native {target} command-hook adapter prepared. Source execution is dormant until "
        f"YI_ENABLE_MIGRATED_HOOKS={plugin}. Review runtime requirements and activate explicitly."
    )
    if target == "codex":
        reason += " Native trust is also required; approval overrides and patch/file schemas may differ."
        if event in CODEX_EXIT_EVENTS:
            reason += (
                f" Native {event} deadline: default 1s, cap {CODEX_EXIT_TIMEOUT}s; "
                "the bridge explicitly requests the cap, including startup overhead. "
                "Longer source timeouts are blocked."
            )
    if target != "codex" and event == "SessionStart":
        reason += " Context arrives at the next native request; source and context roles may differ."
    if target == "ampcode":
        reason += " Workspace root is used for cwd; transcript and continuation provenance are unavailable."
    notes = {
        ("pi", "PreToolUse"): (
            " Additional context is deferred until the next native context callback, after tool execution. "
            "continue:false blocks only the tool, not the session."
        ),
        ("ampcode", "PreToolUse"): (
            " Additional context is deferred until the native tool-result callback. "
            "continue:false rejects only the tool; the session continues."
        ),
        ("pi", "SessionStart"): " Native reload maps to startup; fork maps to resume.",
        ("pi", "SessionEnd"): " Native shutdown reasons are approximate; reload is not a final session exit.",
        ("ampcode", "UserPromptSubmit"): " Native agent-start includes plugin follow-ups, not only user input.",
        ("ampcode", "SessionStart"): " Startup/resume is inferred only from threads observed by this adapter.",
    }
    if target == "pi":
        reason += " transcript_path is native Pi JSONL, not Claude transcript schema, and can be empty."
    return reason + notes.get((target, event), "")


def matcher_blocker(matcher: object) -> str | None:
    """Validate the bounded matcher subset used by the JavaScript bridge."""
    if matcher is None or matcher in ("", "*"):
        return None
    if not isinstance(matcher, str) or len(matcher) > MAX_MATCHER_LENGTH:
        return "Hook matcher must be a string of at most 512 characters."
    structure = re.sub(r"\\.|\[(?:\\.|[^\]\\])*\]", "x", matcher)
    if (
        re.search(r"\\(?:[1-9]|k<)|\(\?|\)[*+?{]", matcher)
        or re.search(r"[{}]", structure)
        or len(re.findall(r"[*+?]", structure)) > 1
    ):
        return "Hook matcher requires unsupported repetition or regex constructs. Adapt it to a bounded matcher."
    try:
        re.compile(matcher)
    except re.error:
        return "Hook matcher has invalid regex syntax."
    return None


def command_blocker(handler: object) -> str | None:
    """Limit executable protocol to declared commands and bounded foreground semantics."""
    if not isinstance(handler, dict) or set(handler) - {
        "type",
        "command",
        "timeout",
        "shell",
        "statusMessage",
        "async",
    }:
        return "Unsupported command-hook fields; conditional/model/rewake behavior needs a separate adapter."
    command = handler.get("command")
    if handler.get("type") != "command" or not isinstance(command, str) or not command.strip():
        return "Only nonempty command handlers are supported; prompt/agent hooks are not silently rewritten."
    if handler.get("async", False) is not False or handler.get("shell", "bash") not in {"bash", "sh"}:
        return "Foreground bash/sh commands are supported; background or other shell semantics need an adapter."
    timeout = handler.get("timeout", 60)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_COMMAND_TIMEOUT
    ):
        return "Command timeout must be finite positive seconds, at most 3600."
    if re.search(r"(?:^|\s)(?:python3?|bash|sh|node)\s+[\"']?/(?!\$)", command):
        return "Absolute external script paths cannot be relocated automatically."
    return None


def root_command_blocker(handler: dict, *, source_root: Path, retained_paths: set[Path] | None = None) -> str | None:
    """Check literal root resources and simple assigned root aliases without executing shell."""
    command = handler["command"]
    aliases = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=[\"']?\$\{CLAUDE_PLUGIN_ROOT(?::-[^}]*)?\}", command)
    roots = [r"\$\{CLAUDE_PLUGIN_ROOT(?::-[^}]*)?\}", r"\$CLAUDE_PLUGIN_ROOT\b"]
    roots.extend(r"\$\{?" + re.escape(alias) + r"\}?" for alias in aliases)
    for pattern in roots:
        for match in re.finditer(pattern + r"/([^\s\"';|&]+)", command):
            path = source_root / match[1]
            if not path.resolve().is_relative_to(source_root) or not path.is_file() or path.is_symlink():
                return f"Plugin-root command resource is missing or unsafe: {match[1]}."
            if retained_paths is not None and path.resolve().relative_to(source_root) not in retained_paths:
                return f"Plugin-root command resource is excluded from the relocated runtime: {match[1]}."
    return None


def codex_hooks(runtime: Path, events: dict) -> dict[str, bytes]:
    """Register untrusted native events through the source-protocol bridge."""
    groups = {}
    for event, handlers in events.items():
        timeout = max(handler.get("timeout", 60) for group in handlers for handler in group["hooks"])
        command = (
            f'node "$HOME/{runtime}/runner.mjs" --config "$HOME/{runtime}/config.json" --event {event} --target codex'
        )
        deadline = CODEX_EXIT_TIMEOUT if event in CODEX_EXIT_EVENTS else math.ceil(timeout) + 5
        groups[event] = [{"hooks": [{"type": "command", "command": command, "timeout": deadline}]}]
    return {"codex/home/.codex/hooks.json": (json.dumps({"hooks": groups}, indent=2) + "\n").encode()}
