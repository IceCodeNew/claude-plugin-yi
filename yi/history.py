"""Import supported local session records through the live collector."""

import json
import re
from contextlib import closing
from pathlib import Path

from yi.usage import connect, record_event


def import_history(directory: Path, source: Path) -> dict[str, int]:
    """Import identifiable calls, preserving live event identities."""
    counts = {"imported": 0, "duplicates": 0, "ignored": 0, "rejected": 0}
    paths = sorted(source.rglob("*.jsonl")) if source.is_dir() else [source]
    with closing(connect(directory)) as connection:
        for path in paths:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = event_from_row(json.loads(line))
                        if event is None:
                            counts["ignored"] += 1
                        else:
                            counts["imported" if record_event(connection, event) else "duplicates"] += 1
                    except (ValueError, TypeError, AttributeError):
                        counts["rejected"] += 1
    return counts


def event_from_row(row: dict) -> dict | None:
    """Recognize successful skill receipts or command expansion records."""
    if row.get("type") != "user" or row.get("isMeta"):
        return None
    content = row.get("message", {}).get("content")
    receipt = row.get("toolUseResult", {})
    if isinstance(content, list) and receipt.get("success") is True and receipt.get("commandName"):
        results = [block for block in content if block.get("type") == "tool_result"]
        if len(results) != 1 or results[0].get("is_error"):
            return None
        return {
            "hook_event_name": "PostToolUse",
            "session_id": row.get("sessionId"),
            "tool_name": "Skill",
            "tool_use_id": results[0].get("tool_use_id"),
            "tool_input": {"skill": receipt["commandName"]},
        }
    if isinstance(content, str) and content.startswith("<command-message>"):
        match = re.search(r"<command-name>/([^<\s]+)</command-name>", content)
        if match:
            return {
                "hook_event_name": "UserPromptExpansion",
                "expansion_type": "slash_command",
                "session_id": row.get("sessionId"),
                "prompt_id": row.get("promptId"),
                "command_name": match[1],
            }
    return None
