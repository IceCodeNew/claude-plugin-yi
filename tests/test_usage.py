import json
import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "yi.py"


def run_cli(tmp_path, *args, event=None) -> dict:
    result = subprocess.run(  # noqa: S603 - Execute the fixed local entry point, without a shell.
        [sys.executable, str(ENTRY), "--data-dir", str(tmp_path / "data"), *args],
        input=json.dumps(event) if event else "",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_user_counts_unique_calls_and_ranks_usage(tmp_path) -> None:
    # Given successful automatic and manually expanded skill invocations.
    event = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-one",
        "tool_use_id": "tool-one",
        "tool_name": "Skill",
        "tool_input": {"skill": "demo:check", "args": "private input"},
    }
    # When a duplicate delivery and a distinct call arrive.
    run_cli(tmp_path, "record", event=event)
    run_cli(tmp_path, "record", event=event)
    run_cli(tmp_path, "record", event={**event, "tool_use_id": "tool-two"})
    run_cli(
        tmp_path,
        "record",
        event={
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "session_id": "session-one",
            "prompt_id": "prompt-two",
            "command_name": "demo:build",
            "command_source": "plugin",
        },
    )
    # Then each execution counts once and private arguments are not stored.
    items = run_cli(tmp_path, "usage", "--json")["items"]
    assert [(item["name"], item["count"]) for item in items] == [("demo:check", 2), ("demo:build", 1)]
    assert b"private input" not in (tmp_path / "data" / "usage.sqlite3").read_bytes()
    plugins = run_cli(tmp_path, "usage", "--group", "plugin", "--json")["items"]
    assert plugins == [{"name": "demo", "count": 3}]


def test_user_rejects_unidentified_events_without_persisting_input(tmp_path) -> None:
    # Given an event without a stable execution identity.
    event = {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": "demo:check"}}
    # When collection runs in non-blocking hook mode.
    result = subprocess.run(  # noqa: S603 - Fixed local entry point and synthetic stdin.
        [sys.executable, str(ENTRY), "--data-dir", str(tmp_path / "data"), "record", "--hook"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the task continues, a diagnostic is emitted, and no event is counted.
    assert result.returncode == 0
    assert result.stdout == ""
    assert "identity" in result.stderr
    assert run_cli(tmp_path, "usage", "--json")["items"] == []


def test_user_ignores_non_skill_tools(tmp_path) -> None:
    # Given an ordinary tool call, when collection receives it, then no usage is recorded.
    assert run_cli(tmp_path, "record", event={"hook_event_name": "PostToolUse", "tool_name": "Read"}) == {
        "recorded": False,
    }


def test_user_receives_nonblocking_diagnostic_for_malformed_event(tmp_path) -> None:
    # Given a JSON value that is not a hook object.
    # When the collector runs as a hook, then Claude continues without a traceback.
    result = subprocess.run(  # noqa: S603 - Fixed local CLI and synthetic malformed input.
        [sys.executable, str(ENTRY), "--data-dir", str(tmp_path / "data"), "record", "--hook"],
        input="[]",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "collection failed" in result.stderr


def test_user_collects_without_third_party_site_packages(tmp_path) -> None:
    # Given Python with site packages disabled, collection must still use only the standard library.
    result = subprocess.run(  # noqa: S603 - Fixed interpreter, local entry point, and synthetic input.
        [sys.executable, "-S", str(ENTRY), "--data-dir", str(tmp_path / "data"), "record", "--hook"],
        input='{"hook_event_name":"PostToolUse","tool_name":"Read"}',
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_user_counts_concurrent_calls_without_losing_updates(tmp_path) -> None:
    # Given separate successful calls arriving at the same local database.
    from concurrent.futures import ThreadPoolExecutor

    def collect_one(index) -> dict:
        return run_cli(
            tmp_path,
            "record",
            event={
                "hook_event_name": "PostToolUse",
                "session_id": "concurrent",
                "tool_name": "Skill",
                "tool_use_id": f"tool-{index}",
                "tool_input": {"skill": "sample:check"},
            },
        )

    # When independent CLI processes collect them concurrently, then no increments disappear.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect_one, range(12)))
    assert all(result["recorded"] for result in results)
    assert run_cli(tmp_path, "usage", "--json")["items"] == [{"name": "sample:check", "count": 12}]
