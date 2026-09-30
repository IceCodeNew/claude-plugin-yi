"""Translate declarative target configuration without starting services."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from yi.frontmatter import read_yaml
from yi.safety import reject_sensitive


def mcp_files(source: Path, manifest: dict, target: str) -> tuple[dict[str, bytes], list[dict]]:
    """Convert supported MCP transports into disabled target declarations."""
    path = source / ".mcp.json"
    declared = manifest.get("mcpServers")
    if isinstance(declared, str):
        path = source / declared
    if isinstance(declared, dict):
        servers = declared
    elif path.is_file():
        if not path.resolve().is_relative_to(source) or path.is_symlink():
            msg = "MCP configuration must remain inside the source plugin."
            raise ValueError(msg)
        data = json.loads(path.read_text(encoding="utf-8"))
        data = configuration_object(data, "MCP document")
        servers = data.get("mcpServers", data)
    else:
        return {}, []
    servers = configuration_object(servers, "MCP servers")
    reject_sensitive(Path("mcp-config.json"), json.dumps(servers).encode())
    converted = {}
    diagnostics = []
    for name, server in servers.items():
        item = {"name": f"{manifest['name']}:mcp:{name}", "kind": "mcp"}
        value = convert_server(server, target)
        if value is None:
            diagnostics.append(
                {
                    **item,
                    "status": "blocked",
                    "reason": mcp_blocker(server, target),
                }
            )
        else:
            converted[f"{manifest['name']}-{name}"] = value
            diagnostics.append(
                {
                    **item,
                    "status": "unverified",
                    "reason": "Server declaration converted and disabled; review paths and enable explicitly.",
                }
            )
    if not converted:
        return {}, diagnostics
    if target == "opencode-v2":
        content = json.dumps({"mcp": {"servers": converted}}, indent=2) + "\n"
        location = ".config/opencode/opencode.json"
    else:
        content = "\n".join(toml_server(name, value) for name, value in converted.items())
        location = ".codex/config.toml"
    return {f"{target}/home/{location}": content.encode()}, diagnostics


def convert_server(server: dict, target: str) -> dict | None:
    """Translate only verified transports and reject embedded credentials."""
    if target not in {"codex", "opencode-v2"} or not isinstance(server, dict):
        return None
    if (
        set(server) - {"type", "command", "args", "url", "env", "headers"}
        or server.get("env")
        or ("url" in server and not safe_url(server["url"]))
    ):
        return None
    transport = server.get("type", "stdio" if "command" in server else "http")
    if transport == "stdio" and isinstance(server.get("command"), str):
        args = server.get("args", [])
        if server.get("headers") or not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return None
        return (
            {"command": server["command"], "args": args, "enabled": False}
            if target == "codex"
            else {"type": "local", "command": [server["command"], *args], "disabled": True}
        )
    if transport == "http" and isinstance(server.get("url"), str) and server["url"].startswith("https://"):
        return remote_server(server, target)
    return None


def toml_server(name: str, value: dict) -> str:
    """Serialize the supported MCP table and environment-header mappings."""
    table = f"mcp_servers.{json.dumps(name, ensure_ascii=False)}"
    lines = [f"[{table}]"]
    lines.extend(
        f"{key} = {json.dumps(item, ensure_ascii=False)}" for key, item in value.items() if not isinstance(item, dict)
    )
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f"[{table}.{key}]")
            lines.extend(f"{json.dumps(header)} = {json.dumps(reference)}" for header, reference in item.items())
    return "\n".join(lines) + "\n"


def agent_files(source: Path, manifest: dict, target: str) -> tuple[dict[str, bytes], list[dict]]:
    """Convert plain agent definitions without silently changing permissions."""
    import importlib  # noqa: PLC0415 - Keep migration dependencies out of collector startup.

    from yi.catalog import component_roots  # noqa: PLC0415 - Local capability dependency.

    files = {}
    diagnostics = []
    parser = importlib.import_module("yaml")
    seen = set()
    for base in component_roots(source, "agents", manifest.get("agents", [])):
        paths = [base] if base.is_file() else sorted(base.rglob("*.md"))
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                msg = "Agent path escapes plugin or follows a symlink."
                raise ValueError(msg)
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            content = path.read_bytes()
            reject_sensitive(path, content)
            text = content.decode("utf-8")
            header, separator, body = text.removeprefix("---\n").partition("\n---\n")
            metadata = read_yaml(header) if separator else {}
            name = f"{manifest['name']}-{path.stem}"
            item = {"name": f"{manifest['name']}:agent:{path.stem}", "kind": "agent", "path": str(path)}
            if (
                target not in {"codex", "opencode-v2"}
                or not isinstance(metadata, dict)
                or set(metadata) - {"name", "description"}
                or not metadata.get("description")
                or not separator
            ):
                diagnostics.append(
                    {
                        **item,
                        "status": "blocked",
                        "reason": "Agent tools, model, or target semantics require adaptation.",
                    }
                )
                continue
            if target == "codex":
                definition = {"name": name, "description": metadata.get("description"), "developer_instructions": body}
                content = (
                    "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in definition.items())
                    + "\n"
                )
                location = f".codex/agents/{name}.toml"
            else:
                definition = {"description": metadata.get("description"), "mode": "subagent"}
                content = "---\n" + parser.safe_dump(definition) + "---\n" + body
                location = f".config/opencode/agents/{name}.md"
            destination = f"{target}/home/{location}"
            if destination in files:
                msg = f"Agent destination collision: {destination}. Rename one source agent."
                raise ValueError(msg)
            files[destination] = content.encode()
            diagnostics.append(
                {
                    **item,
                    "status": "unverified",
                    "reason": "Native agent definition prepared; verify invocation and permissions.",
                }
            )
    return files, diagnostics


def hook_files(source: Path, manifest: dict, target: str) -> tuple[dict[str, bytes], list[dict]]:
    """Translate supported Codex hook declarations without granting trust."""
    path = source / "hooks/hooks.json"
    declared = manifest.get("hooks")
    if isinstance(declared, str):
        path = source / declared
    if isinstance(declared, dict):
        document = declared
    elif path.exists():
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            msg = "Hook configuration must remain inside the plugin."
            raise ValueError(msg)
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        return {}, []
    document = configuration_object(document, "Hook document")
    events = configuration_object(document.get("hooks", {}), "Hook events")
    reject_sensitive(Path("hook-config.json"), json.dumps(document).encode())
    supported = {
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "Interrupt",
    }
    result = {}
    diagnostics = []
    for event, groups in events.items():
        item = {"kind": "hooks", "name": f"{manifest['name']}:hooks:{event}"}
        if target != "codex" or event not in supported or not groups or not compatible_hooks(groups):
            diagnostics.append(
                {**item, "status": "blocked", "reason": "Target event or handler protocol requires a reviewed adapter."}
            )
        else:
            result[event] = groups
            diagnostics.append(
                {
                    **item,
                    "status": "unverified",
                    "reason": "Untrusted declaration; verify input, output, and blocking behavior before enabling.",
                }
            )
    files = (
        {"codex/home/.codex/hooks.json": (json.dumps({"hooks": result}, indent=2) + "\n").encode()} if result else {}
    )
    return files, diagnostics


def compatible_hooks(groups: list) -> bool:
    """Accept only the shared declarative subset; do not imply semantic equivalence."""
    if not isinstance(groups, list):
        return False
    for group in groups:
        if (
            not isinstance(group, dict)
            or set(group) - {"matcher", "hooks"}
            or not isinstance(group.get("hooks", []), list)
        ):
            return False
        handlers = group.get("hooks", [])
        for handler in handlers:
            if not isinstance(handler, dict) or set(handler) - {"type", "command", "timeout", "async", "statusMessage"}:
                return False
            if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
                return False
            if "CLAUDE_PLUGIN_ROOT" in handler["command"]:
                return False
    return True


def safe_url(value: object) -> bool:
    """Reject credential-bearing remote addresses instead of persisting them."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    reviewed_query = value == "https://mcp.context7.com/mcp?client=claude-code-plugin"
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not any((parsed.username, parsed.password, parsed.fragment))
        and (not parsed.query or reviewed_query)
    )


def mcp_blocker(server: object, target: str) -> str:
    """Explain the failed compatibility boundary without exposing source values."""
    target_limits = {
        "pi": (
            "Pi native MCP support is not verified for the tested release. "
            "Use a reviewed extension or compatible release."
        ),
        "ampcode": (
            "Amp has no verified disabled-server export setting. Review its MCP activation policy before adaptation."
        ),
    }
    if target in target_limits:
        return target_limits[target]
    if not isinstance(server, dict):
        return "MCP server definition must be an object."
    unsupported = set(server) - {"type", "command", "args", "url", "env", "headers"}
    if unsupported:
        return "Unsupported MCP fields: " + ", ".join(sorted(unsupported))
    credential_fields = [field for field in ("env", "headers") if server.get(field)]
    if credential_fields:
        return "MCP fields require explicit credential mapping: " + ", ".join(credential_fields)
    if "url" in server and not safe_url(server.get("url")):
        return "MCP url must use HTTPS without embedded credentials, query parameters, or fragments."
    return (
        "MCP requires a supported stdio command with string args or an HTTP url. Review the transport and field types."
    )


def configuration_object(value: object, label: str) -> dict:
    """Reject invalid external container types before attribute access."""
    if not isinstance(value, dict):
        msg = f"{label} must be a JSON object."
        raise TypeError(msg)
    return value


def remote_server(server: dict, target: str) -> dict | None:
    """Translate only environment-backed HTTP headers without resolving values."""
    headers = server.get("headers", {})
    if not isinstance(headers, dict):
        return None
    output = (
        {"url": server["url"], "enabled": False}
        if target == "codex"
        else {"type": "remote", "url": server["url"], "disabled": True}
    )
    if target == "opencode-v2" and headers:
        output["oauth"] = False
    mapped_headers = {}
    for name, value in headers.items():
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"(Bearer )?\$\{([A-Za-z_][A-Za-z0-9_]*)(:-)?\}", value)
        if not match:
            return None
        prefix, variable, optional = match.groups()
        if target == "codex":
            if prefix:
                if name.lower() != "authorization" or optional:
                    return None
                output["bearer_token_env_var"] = variable
            else:
                mapped_headers[name] = variable
        else:
            mapped_headers[name] = (prefix or "") + "{env:" + variable + "}"
    if mapped_headers:
        output["env_http_headers" if target == "codex" else "headers"] = mapped_headers
    return output
