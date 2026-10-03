"""Compose inert target configuration fragments without artifact publication."""

import json
import tomllib

from yi.safety import require_object

SHARED = {
    "opencode-v2/home/.config/opencode/opencode.json",
    "codex/home/.codex/config.toml",
    "codex/home/.codex/hooks.json",
}


def combine(name: str, pieces: list[str]) -> bytes:
    """Combine namespaced tables or hook groups without executing their content."""
    if name.endswith(".toml"):
        content = "\n".join(pieces)
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as error:
            msg = "Shared TOML configuration has a name collision or invalid syntax."
            raise ValueError(msg) from error
        return content.encode()
    result: dict = {}
    for text in pieces:
        document = require_object(json.loads(text), "Shared configuration")
        if "mcp" in document:
            servers = result.setdefault("mcp", {}).setdefault("servers", {})
            if set(servers) & set(configuration_servers(document)):
                msg = "Shared MCP server name collision. Rename one source server."
                raise ValueError(msg)
            servers.update(configuration_servers(document))
        plugins = document.get("plugins", [])
        if not isinstance(plugins, list) or not all(isinstance(plugin, str) for plugin in plugins):
            msg = "Shared native plugins must be an array of paths."
            raise TypeError(msg)
        if plugins:
            result["plugins"] = sorted(set(result.get("plugins", [])) | set(plugins))
        for event, handlers in require_object(document.get("hooks", {}), "Shared hook events").items():
            result.setdefault("hooks", {}).setdefault(event, []).extend(handlers)
    return (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()


def configuration_servers(document: dict) -> dict:
    """Validate shared server collections before combining their contributions."""
    mcp = require_object(document["mcp"], "Shared MCP configuration")
    return require_object(mcp["servers"], "Shared MCP servers")
