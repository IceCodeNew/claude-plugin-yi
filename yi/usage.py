"""Persist invocation identities without prompt content."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def record(directory: Path, event: dict) -> bool:
    """Store one observable invocation and return whether it is new."""
    with closing(connect(directory)) as connection:
        return record_event(connection, event)


def record_event(connection: sqlite3.Connection, event: dict) -> bool:
    """Insert a validated event using an existing connection."""
    if not isinstance(event, dict):
        msg = "Hook input must be an object."
        raise TypeError(msg)
    session = event.get("session_id")
    kind = event.get("hook_event_name")
    if kind == "PostToolUse" and event.get("tool_name") == "Skill":
        identity = event.get("tool_use_id")
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            msg = "Skill input must be an object."
            raise ValueError(msg)
        name = tool_input.get("skill")
    elif kind == "UserPromptExpansion" and event.get("expansion_type") == "slash_command":
        identity = event.get("prompt_id")
        name = event.get("command_name")
    else:
        return False
    if (
        not isinstance(name, str)
        or not name
        or not all(isinstance(value, str) and value for value in (session, identity))
    ):
        msg = "Invocation requires a session, an event identity, and a component name."
        raise ValueError(msg)
    key = json.dumps([session, kind, identity, name])
    plugin = name.split(":", 1)[0] if ":" in name else None
    with connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO invocations (id, name, plugin) VALUES (?, ?, ?)",
            (key, name, plugin),
        )
        return cursor.rowcount == 1


def connect(directory: Path) -> sqlite3.Connection:
    """Open the private database and initialize its schema."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "usage.sqlite3"
    path.touch(mode=0o600, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, name TEXT NOT NULL, plugin TEXT)")
    return connection


def ranked(directory: Path, group: str) -> list[dict]:
    """Return counts in descending order with stable name ties."""
    query = (
        "SELECT plugin, COUNT(*) FROM invocations WHERE plugin IS NOT NULL GROUP BY plugin ORDER BY 2 DESC, 1"
        if group == "plugin"
        else "SELECT name, COUNT(*) FROM invocations GROUP BY name ORDER BY 2 DESC, 1"
    )
    with closing(connect(directory)) as connection:
        return [{"name": name, "count": count} for name, count in connection.execute(query)]
