import json

from tests.test_usage import run_cli


def test_user_imports_history_without_duplicate_counts(tmp_path) -> None:
    # Given a successful historical skill result and a manually expanded command.
    rows = [
        {
            "sessionId": "s",
            "type": "user",
            "toolUseResult": {"success": True, "commandName": "demo:check"},
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]},
        },
        {
            "sessionId": "s",
            "type": "user",
            "promptId": "p",
            "message": {
                "content": "<command-message>demo:build</command-message>\n<command-name>/demo:build</command-name>"
            },
        },
    ]
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    # When the same history is imported twice.
    first = run_cli(tmp_path, "history", "--from", str(source), "--json")
    second = run_cli(tmp_path, "history", "--from", str(source), "--json")
    # Then two calls remain and the second import reports duplicates.
    assert first["imported"] == 2
    assert second["duplicates"] == 2
    assert sum(item["count"] for item in run_cli(tmp_path, "usage", "--json")["items"]) == 2


def test_user_reports_invalid_history_without_storing_conversation_text(tmp_path) -> None:
    # Given malformed JSON, an unrelated message, and a failed skill result.
    source = tmp_path / "history.jsonl"
    source.write_text(
        "\n".join(
            [
                "{invalid",
                json.dumps({"type": "assistant", "message": {"content": "private text"}}),
                json.dumps(
                    {"type": "user", "sessionId": "s", "toolUseResult": {"success": False}, "message": {"content": []}}
                ),
            ]
        ),
        encoding="utf-8",
    )
    # When imported, then bad records are counted and unrelated content is ignored.
    result = run_cli(tmp_path, "history", "--from", str(source), "--json")
    assert result == {"imported": 0, "duplicates": 0, "ignored": 2, "rejected": 1}
    assert run_cli(tmp_path, "usage", "--json")["items"] == []


def test_user_non_skill_tool_receipts_are_ignored(tmp_path) -> None:
    # Given ordinary tool results whose receipt is not a skill launch object.
    source = tmp_path / "history.jsonl"
    rows = [
        {
            "type": "user",
            "sessionId": "s",
            "toolUseResult": value,
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]},
        }
        for value in (None, "plain receipt", [])
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    # When imported, unrelated receipts are ignored rather than rejected as malformed skill calls.
    result = run_cli(tmp_path, "history", "--from", str(source), "--json")
    assert result["ignored"] == 3
    assert result["rejected"] == 0
    assert result["imported"] == 0


def test_user_history_invalid_utf8_rejects_only_affected_row(tmp_path) -> None:
    # Given one invalid encoded row followed by a valid successful skill receipt.
    source = tmp_path / "history.jsonl"
    row = {
        "sessionId": "s",
        "type": "user",
        "toolUseResult": {"success": True, "commandName": "demo:check"},
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]},
    }
    source.write_bytes(b"\xff\n" + json.dumps(row).encode() + b"\n")
    # When imported, later calls remain observable and the corrupt row is reported.
    result = run_cli(tmp_path, "history", "--from", str(source), "--json")
    assert result["imported"] == 1
    assert result["rejected"] == 1
