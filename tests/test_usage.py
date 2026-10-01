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


def test_user_collection_starts_when_optional_migration_module_is_unavailable(tmp_path) -> None:
    import shutil

    # Given a checkout whose optional migration module cannot import.
    root = tmp_path / "checkout"
    shutil.copytree(ENTRY.parents[1] / "yi", root / "yi", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "scripts").mkdir()
    shutil.copyfile(ENTRY, root / "scripts/yi.py")
    (root / "yi/native.py").write_text('raise RuntimeError("optional native runtime unavailable")\n', encoding="utf-8")
    # When collecting an event, optional capability startup cannot break the counter.
    result = subprocess.run(  # noqa: S603 - Task-owned checkout and synthetic input.
        [sys.executable, str(root / "scripts/yi.py"), "--data-dir", str(tmp_path / "data"), "record", "--hook"],
        input='{"hook_event_name":"PostToolUse","tool_name":"Read"}',
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_user_corrupt_database_returns_structured_query_failure(tmp_path) -> None:
    # Given a local usage database that is not SQLite data.
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "usage.sqlite3").write_bytes(b"corrupt fixture")
    # When queried, report a controlled failure instead of a traceback.
    result = subprocess.run(  # noqa: S603 - Fixed helper and isolated corrupt database.
        [sys.executable, str(ENTRY), "--data-dir", str(directory), "usage", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Traceback" not in result.stderr


def test_user_unsupported_python_does_not_block_collection(tmp_path) -> None:
    import os

    import pytest

    executable = os.environ.get("YI_TEST_PYTHON310")
    if not executable:
        pytest.skip("Set YI_TEST_PYTHON310 to test the real unsupported interpreter.")
    # Given Python 3.10, failure must occur before imports of Python 3.11-only modules.
    args = [executable, str(ENTRY), "--data-dir", str(tmp_path / "data")]
    # When invoked as a hook, continue without collecting; an ordinary command must fail.
    hook = subprocess.run([*args, "record", "--hook"], input="{}", capture_output=True, text=True, check=False)  # noqa: S603 - Actual pinned interpreter and fixed helper.
    command = subprocess.run([*args, "usage", "--json"], capture_output=True, text=True, check=False)  # noqa: S603 - Actual pinned interpreter and fixed helper.
    assert hook.returncode == 0
    assert command.returncode != 0
    assert "Python 3.11" in hook.stderr
    assert "Python 3.11" in command.stderr
    assert "Traceback" not in hook.stderr + command.stderr
    assert not (tmp_path / "data").exists()
