import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "yi/hook_runtime.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for the hook runtime")


def _invoke(tmp_path, hooks, *, event="PreToolUse", payload=None, enabled="demo", matcher=None):  # noqa: PLR0913
    config = {
        "plugin": "demo",
        "root": str(tmp_path),
        "events": {event: [{"matcher": matcher, "hooks": hooks}]},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    environment = {**os.environ, "YI_ENABLE_MIGRATED_HOOKS": enabled}
    assert NODE is not None
    return subprocess.run(  # noqa: S603 - Real local runtime with controlled probe commands.
        [NODE, str(RUNTIME), "--config", str(path), "--event", event],
        input=json.dumps(payload or {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {}}),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("enabled", ["", "other", "*", "demonstration"])
def test_runtime_is_dormant_without_explicit_plugin_opt_in(tmp_path, enabled) -> None:
    marker = tmp_path / "ran"
    result = _invoke(tmp_path, [{"type": "command", "command": "touch ran"}], enabled=enabled)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert not marker.exists()


def test_matching_hook_receives_canonical_stdin_and_runtime_roots(tmp_path) -> None:
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        "import {readFileSync} from 'node:fs';"
        "const p=JSON.parse(readFileSync(0,'utf8'));"
        "const reason=[p.hook_event_name,p.tool_name,p.tool_input.command,"
        "process.env.CLAUDE_PLUGIN_ROOT,process.env.PLUGIN_ROOT,"
        "process.env.CLAUDE_PROJECT_DIR,process.cwd()].join('|');"
        "console.log(JSON.stringify({hookSpecificOutput:{permissionDecision:'deny',permissionDecisionReason:reason}}));",
        encoding="utf-8",
    )
    payload = {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "fixture-only"}}
    hooks = [{"type": "command", "command": f'node "${{CLAUDE_PLUGIN_ROOT}}/{probe.name}"'}]
    skipped = _invoke(tmp_path, hooks, payload=payload, matcher="^Read$")
    assert skipped.returncode == 0, skipped.stderr
    assert json.loads(skipped.stdout) == {}
    result = _invoke(tmp_path, hooks, payload=payload, matcher="^Bash$")
    assert result.returncode == 2, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    expected = "|".join(["PreToolUse", "Bash", "fixture-only", *([str(tmp_path)] * 4)])
    assert output["hookSpecificOutput"]["permissionDecisionReason"] == expected


@pytest.mark.parametrize(
    ("event", "denied"), [("PreToolUse", True), ("UserPromptSubmit", True), ("Stop", True), ("PostToolUse", False)]
)
def test_exit_two_blocks_only_blocking_events_and_provides_post_feedback(tmp_path, event, denied) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": "printf 'review needed' >&2; exit 2"}], event=event)
    assert result.returncode == (2 if denied else 0), result.stderr
    output = json.loads(result.stdout)
    if event == "PreToolUse":
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "review needed"
    elif denied:
        assert output["decision"] == "block"
        assert output["reason"] == "review needed"
    else:
        assert output["hookSpecificOutput"]["additionalContext"] == "review needed"


@pytest.mark.parametrize(
    ("event", "context"), [("SessionStart", "hello"), ("UserPromptSubmit", "hello"), ("PreToolUse", None)]
)
def test_plain_stdout_is_context_only_for_prompt_and_session_events(tmp_path, event, context) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": "printf hello"}], event=event)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    if context:
        assert output["hookSpecificOutput"]["additionalContext"] == context
    else:
        assert output == {}


def test_parallel_results_keep_deny_context_input_update_and_stop(tmp_path) -> None:
    outputs = [
        {
            "hookSpecificOutput": {
                "updatedInput": {"command": "changed"},
                "additionalContext": "first",
            }
        },
        {"decision": "block", "reason": "blocked", "systemMessage": "second", "continue": False},
    ]
    hooks = [{"type": "command", "command": "printf '%s' '" + json.dumps(output) + "'"} for output in outputs]
    result = _invoke(tmp_path, hooks)
    assert result.returncode == 2, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"] == "blocked"
    assert output["hookSpecificOutput"]["updatedInput"] == {"command": "changed"}
    assert output["hookSpecificOutput"]["additionalContext"] == "first"
    assert output["systemMessage"] == "second"
    assert output["continue"] is False


def test_permission_ask_is_reported_as_unsupported_not_silently_allowed(tmp_path) -> None:
    result = _invoke(
        tmp_path,
        [{"type": "command", "command": 'printf \'%s\' \'{"hookSpecificOutput":{"permissionDecision":"ask"}}\''}],
    )
    assert result.returncode == 1
    assert "unsupported" in result.stderr.lower()
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("command", "timeout"),
    [("sleep 3; touch late", 0.1), ("node -e \"process.stdout.write('x'.repeat(1048577))\"; touch late", 2)],
)
def test_timeout_and_output_limit_fail_without_raw_output_or_late_effects(tmp_path, command, timeout) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": command, "timeout": timeout}])
    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr) < 200
    assert not (tmp_path / "late").exists()


@pytest.mark.parametrize("timeout", [0, -1, "2", True, 3601])
def test_invalid_timeout_does_not_start_hook(tmp_path, timeout) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": "touch ran", "timeout": timeout}])
    assert result.returncode == 1
    assert not (tmp_path / "ran").exists()


def test_native_payload_aliases_and_relative_root_are_normalized(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    config = {
        "plugin": "demo",
        "root": "source",
        "events": {
            "PreToolUse": [
                {
                    "matcher": "^Bash$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'node -e \'let p=JSON.parse(require("fs").readFileSync(0,"utf8"));'
                            'console.log(JSON.stringify({hookSpecificOutput:{permissionDecision:"deny",'
                            'permissionDecisionReason:p.tool_input.command+"|"+process.env.CLAUDE_PLUGIN_ROOT}}))\'',
                        }
                    ],
                }
            ]
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Controlled native payload exercises the public local CLI.
        [NODE, str(RUNTIME), "--config", str(path), "--event", "PreToolUse"],
        input=json.dumps({"cwd": str(tmp_path), "tool_name": "shell_command", "input": {"command": "native probe"}}),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "demo"},
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == f"native probe|{root}"


@pytest.mark.parametrize(
    ("output", "permission"),
    [({"permissionDecision": "allow"}, None), ({"updatedInput": {"command": "fixed"}}, "allow")],
)
def test_codex_profile_does_not_grant_source_allow_and_applies_input_updates(tmp_path, output, permission) -> None:
    path = tmp_path / "config.json"
    hook_output = json.dumps({"hookSpecificOutput": output})
    path.write_text(
        json.dumps(
            {
                "plugin": "demo",
                "root": str(tmp_path),
                "events": {
                    "PreToolUse": [{"hooks": [{"type": "command", "command": "printf '%s' '" + hook_output + "'"}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Controlled fixture tests the native CLI output contract.
        [NODE, str(RUNTIME), "--config", str(path), "--event", "PreToolUse", "--target", "codex"],
        input=json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "input": {}}),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "demo"},
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual.get("hookSpecificOutput", {}).get("permissionDecision") == permission
    if permission:
        assert actual["hookSpecificOutput"]["updatedInput"] == {"command": "fixed"}
    else:
        assert "not migrated" in actual["systemMessage"]


def test_handlers_run_in_parallel_but_context_is_in_declared_order(tmp_path) -> None:
    hooks = [
        {
            "type": "command",
            "timeout": 1,
            "command": "touch first; while [ ! -f second ]; do sleep 0.01; done; printf first",
        },
        {
            "type": "command",
            "timeout": 1,
            "command": "touch second; while [ ! -f first ]; do sleep 0.01; done; printf second",
        },
    ]
    result = _invoke(tmp_path, hooks, event="SessionStart")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] == "first\nsecond"


@pytest.mark.parametrize("matcher", [r"(Bash)\1", "(Bash|BashBash)+", "(Bash+)+", "[", "^a+a+a+a+a+$", "^a*a*$"])
def test_unsupported_regex_does_not_start_any_handler(tmp_path, matcher) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": "touch ran"}], matcher=matcher)
    assert result.returncode == 1
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("event", ["PreToolUse", "PermissionRequest", "Stop"])
def test_codex_denial_json_is_successful_protocol_output_not_ignored_exit_two(tmp_path, event) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "plugin": "demo",
                "root": str(tmp_path),
                "events": {event: [{"hooks": [{"type": "command", "command": "printf 'denied fixture' >&2; exit 2"}]}]},
            }
        ),
        encoding="utf-8",
    )
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Native protocol serialization through the public runner.
        [NODE, str(RUNTIME), "--config", str(path), "--event", event, "--target", "codex"],
        input=json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "input": {}}),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "demo"},
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    if event == "PreToolUse":
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "denied fixture"
    elif event == "PermissionRequest":
        assert output["hookSpecificOutput"]["decision"] == {"behavior": "deny", "message": "denied fixture"}
    else:
        assert output["decision"] == "block"
        assert output["reason"] == "denied fixture"


@pytest.mark.parametrize(
    "command",
    [
        'printf \'%s\' \'{"hookSpecificOutput":{"decision":{"behavior":"deny","message":"approval denied"}}}\'',
        "printf 'approval denied' >&2; exit 2",
    ],
)
def test_permission_request_denials_use_native_decision_protocol(tmp_path, command) -> None:
    result = _invoke(tmp_path, [{"type": "command", "command": command}], event="PermissionRequest")
    assert result.returncode == 2, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"] == {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "deny", "message": "approval denied"},
    }


@pytest.mark.parametrize("control", ["interrupt", "updatedInput", "updatedPermissions"])
def test_unsupported_permission_request_controls_do_not_become_approval(tmp_path, control) -> None:
    decision = {"behavior": "deny", control: True}
    command = "printf '%s' '" + json.dumps({"hookSpecificOutput": {"decision": decision}}) + "'"
    result = _invoke(tmp_path, [{"type": "command", "command": command}], event="PermissionRequest")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Unsupported hook output: PermissionRequest decision controls" in result.stderr


@pytest.mark.parametrize(
    ("matcher", "tool"),
    [("^mcp__.*$", "mcp__fixture"), ("^(Bash|Read)$", "Read"), ("^[A-Za-z]+$", "Bash"), (r"^a\+a\*$", "a+a*")],
)
def test_supported_matchers_preserve_literals_alternatives_and_single_repetition(tmp_path, matcher, tool) -> None:
    result = _invoke(
        tmp_path,
        [{"type": "command", "command": "touch matched"}],
        matcher=matcher,
        payload={"cwd": str(tmp_path), "tool_name": tool, "tool_input": {}},
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "matched").exists()


def test_permission_request_allow_does_not_grant_approval(tmp_path) -> None:
    command = 'printf \'%s\' \'{"hookSpecificOutput":{"decision":{"behavior":"allow"}}}\''
    result = _invoke(tmp_path, [{"type": "command", "command": command}], event="PermissionRequest")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "hookSpecificOutput" not in output
    assert "not migrated" in output["systemMessage"]
