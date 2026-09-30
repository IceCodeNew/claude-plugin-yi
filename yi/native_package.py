"""Stage upstream native packages without activation or source execution."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from yi.catalog import checked_source, source_manifest
from yi.safety import reject_sensitive

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


def preview(source: Path, target: str) -> tuple[dict, dict[str, bytes]]:
    """Preserve a native package layout outside the target's discovery paths."""
    source = checked_source(source)
    manifest = source_manifest(source)
    name = manifest["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        msg = "Native package name must use lowercase letters, digits, and hyphens."
        raise ValueError(msg)
    entrypoints = native_entrypoints(source, target)
    validate_declared_mcp(source)
    destination = Path(target) / "home/.local/share/yi/packages" / name
    files = {}
    executable = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if EXCLUDED.intersection(relative.parts) or path.suffix in {".pyc", ".log"}:
            continue
        if path.is_symlink():
            msg = f"Native package contains a symlink: {relative}"
            raise ValueError(msg)
        if path.is_dir():
            continue
        if not path.is_file():
            msg = f"Native package resource is not a regular file: {relative}"
            raise ValueError(msg)
        if (
            path.name.startswith(".env")
            or (".claude" in relative.parts and (path.name.startswith("settings") or "credential" in path.name))
            or path.name == ".npmrc"
        ):
            msg = f"Native package contains local configuration: {relative}"
            raise ValueError(msg)
        content = path.read_bytes()
        reject_sensitive(path, content)
        if path.name == ".mcp.json":
            reject_mcp_credentials(json.loads(content))
        output = str(destination / relative)
        files[output] = content
        if path.stat().st_mode & 0o111:
            executable.append(output)
    verify_staged_entries(destination, entrypoints, files)
    verify_declared_resources(source, destination, target, files)
    owner = f"{name}:native-package"
    report = {
        "plugin": name,
        "target": target,
        "selection": None,
        "files": sorted(files),
        "owners": dict.fromkeys(files, owner),
        "executables": executable,
        "complete": False,
        "activation": "not-registered",
        "native_entrypoints": entrypoints,
        "components": [
            {
                "name": owner,
                "kind": "native-package",
                "status": "unverified",
                "reason": "Upstream native package staged. Review and register it explicitly before execution.",
            }
        ],
    }
    return report, files


def native_entrypoints(source: Path, target: str) -> list[str]:
    """Require an upstream declaration and verify its local resource paths."""
    if target == "codex":
        path = source / ".codex-plugin/plugin.json"
        if path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                msg = "Native Codex manifest must be an object."
                raise TypeError(msg)
            for key in ("skills", "hooks", "mcpServers", "agents", "commands"):
                value = manifest.get(key)
                entries = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                validate_entries(source, entries)
            return [".codex-plugin/plugin.json"]
    path = source / "package.json"
    if path.is_file():
        package = read_object(path)
        if target == "pi" and isinstance(package.get("pi"), dict):
            entries = package["pi"].get("extensions", [])
            for key in ("skills", "prompts", "themes"):
                validate_entries(source, package["pi"].get(key, []))
        elif target == "opencode-v2" and isinstance(package.get("main"), str):
            entries = [package["main"]]
        else:
            entries = []
        if entries and isinstance(entries, list):
            validate_entries(source, entries)
            return entries
    msg = f"No upstream native package declaration for {target}."
    raise ValueError(msg)


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


def verify_staged_entries(destination: Path, entrypoints: list[str], files: dict[str, bytes]) -> None:
    """Require each runtime entrypoint to survive the copy policy."""
    for entry in entrypoints:
        if str(destination / Path(entry)) not in files:
            msg = f"Native entrypoint was excluded from the staged package: {entry}"
            raise ValueError(msg)


def verify_declared_resources(source: Path, destination: Path, target: str, files: dict[str, bytes]) -> None:
    """Check native resource declarations against the final staged inventory."""
    path = source / (".codex-plugin/plugin.json" if target == "codex" else "package.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    declarations = document if target == "codex" else document.get("pi", {}) if target == "pi" else {}
    for key in ("skills", "hooks", "mcpServers", "agents", "commands", "prompts", "themes"):
        value = declarations.get(key)
        entries = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        for entry in entries:
            staged = str(destination / entry)
            if not any(name == staged or name.startswith(staged.rstrip("/") + "/") for name in files):
                msg = f"Native declared resource was excluded: {entry}"
                raise ValueError(msg)


def reject_mcp_credentials(value: object) -> None:
    """Reject resolved MCP credential values while preserving environment references."""
    if not isinstance(value, dict):
        msg = "Native MCP configuration must be an object."
        raise TypeError(msg)
    servers = value.get("mcpServers", value)
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


def validate_declared_mcp(source: Path) -> None:
    """Check MCP resources declared by either source or target manifests."""
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        path = source / relative
        if not path.is_file():
            continue
        document = read_object(path)
        value = document.get("mcpServers")
        if isinstance(value, dict):
            reject_mcp_credentials(value)
        elif isinstance(value, str):
            validate_entries(source, [value])
            reject_mcp_credentials(read_object(source / value))
        elif isinstance(value, list):
            validate_entries(source, value)
            for entry in value:
                reject_mcp_credentials(read_object(source / entry))


def reject_url_credentials(url: object) -> None:
    """Reject URL forms that can disclose authentication or opaque query values."""
    if isinstance(url, str):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            msg = "Native MCP URL may contain an embedded credential. Remove it before staging."
            raise ValueError(msg)
