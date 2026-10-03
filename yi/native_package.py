"""Validate source runtime resources before inert hook conversion."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

EXCLUDED = {
    ".git",
    ".in_use",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    ".remember",
    ".aws",
    ".ssh",
    "sessions",
}


def validate_entries(source: Path, entries: list[str]) -> None:
    """Check declared local package resources before preserving their references."""
    if not isinstance(entries, list):
        msg = "Native resource declarations must be path arrays."
        raise TypeError(msg)
    for entry in entries:
        if (
            not isinstance(entry, str)
            or Path(entry).is_absolute()
            or EXCLUDED.intersection(Path(entry).parts)
            or not (source / entry).resolve().is_relative_to(source)
        ):
            msg = "Native resource must remain inside its package."
            raise ValueError(msg)
        if not (source / entry).exists():
            msg = f"Native resource is missing: {entry}"
            raise ValueError(msg)


def reject_mcp_credentials(value: object, *, inline: bool = False) -> None:
    """Reject resolved MCP credential values while preserving environment references."""
    if not isinstance(value, dict):
        msg = "Native MCP configuration must be an object."
        raise TypeError(msg)
    servers = value if inline else value.get("mcpServers", value)
    if not isinstance(servers, dict):
        msg = "Native MCP servers must be an object."
        raise TypeError(msg)
    for server in servers.values():
        if not isinstance(server, dict):
            continue
        reject_url_credentials(server.get("url"))
        for field in ("env", "headers"):
            entries = server.get(field, {})
            if not isinstance(entries, dict):
                msg = f"Native MCP {field} must be an object."
                raise TypeError(msg)
            for reference in entries.values():
                if not isinstance(reference, str) or not re.fullmatch(
                    r"(?:Bearer )?\$\{[A-Za-z_][A-Za-z0-9_]*(?::-)?\}", reference
                ):
                    msg = "Native MCP contains a resolved credential or environment value."
                    raise ValueError(msg)


def read_object(path: Path) -> dict:
    """Read a native declaration without accepting scalar or array documents."""
    if path.is_symlink() or not path.is_file():
        msg = f"Native declaration must be a regular file: {path.name}"
        raise ValueError(msg)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"Native declaration must be an object: {path.name}"
        raise TypeError(msg)
    return document


def validate_declared_mcp(source: Path, manifest: dict | None = None) -> None:
    """Check on-disk and effective marketplace MCP declarations before packaging."""
    documents = [manifest] if manifest is not None else []
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        path = source / relative
        if path.exists():
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                msg = "Native MCP manifest must not follow a symlink."
                raise ValueError(msg)
            documents.append(read_object(path))
    for document in documents:
        value = document.get("mcpServers")
        if isinstance(value, dict):
            reject_mcp_credentials(value, inline=True)
        elif isinstance(value, (str, list)):
            entries = [value] if isinstance(value, str) else value
            validate_entries(source, entries)
            for entry in entries:
                path = source / entry
                if any(parent.is_symlink() for parent in (path, *path.parents)):
                    msg = "Native MCP declaration must not follow a symlink."
                    raise ValueError(msg)
                reject_mcp_credentials(read_object(path))


def reject_url_credentials(url: object) -> None:
    """Reject URL forms that can disclose authentication or opaque query values."""
    if isinstance(url, str):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            msg = "Native MCP URL may contain an embedded credential. Remove it before staging."
            raise ValueError(msg)
