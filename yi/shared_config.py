"""Rebuild shared target configuration from per-plugin contributions."""

import hashlib
import json
import tomllib
from pathlib import Path

SHARED = {
    "opencode-v2/home/.config/opencode/opencode.json",
    "codex/home/.codex/config.toml",
    "codex/home/.codex/hooks.json",
}


def prepare(root: Path, report: dict, files: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Render per-plugin contributions under one target-level physical owner."""
    merged = dict(files)
    metadata = {}
    manifests = {}
    for path in (root / "manifests").glob(f"{report['target']}-*.json"):
        if path.is_symlink():
            msg = "Shared manifest must not be a symlink."
            raise ValueError(msg)
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("activation") != "not-registered":
            manifests[path] = document
    contributions = {name: content.decode() for name, content in files.items() if name in SHARED}
    prior = next(
        (
            data
            for data in manifests.values()
            if data.get("plugin") == report["plugin"] and data.get("target") == report["target"]
        ),
        {},
    )
    if report.get("selection"):
        contributions = {**prior.get("configuration", {}), **contributions}
    report["configuration"] = contributions
    owner_path = f"manifests/{report['target']}--shared.json"
    target_owner = manifests.get(
        root / owner_path, {"target": report["target"], "hashes": {}, "components": [], "executables": []}
    )
    affected = set(contributions) | set(prior.get("configuration", {}))
    for name in sorted(affected):
        contribution = contributions.get(name)
        participants = [data for data in manifests.values() if data.get("target") == report["target"]]
        existing = root / name
        owners = [data for data in participants if name in data.get("hashes", {})]
        if existing.exists():
            digest = hashlib.sha256(existing.read_bytes()).hexdigest()
            if not owners or any(data["hashes"][name] != digest for data in owners):
                msg = f"Shared configuration is unowned or modified: {name}"
                raise ValueError(msg)
        pieces = [
            (data["plugin"], data.get("configuration", {}).get(name))
            for data in participants
            if data.get("plugin") and data.get("plugin") != report["plugin"]
        ]
        pieces.append((report["plugin"], contribution))
        merged[name] = combine(name, [content for _, content in sorted(pieces) if content])
        digest = hashlib.sha256(merged[name]).hexdigest()
        target_owner["hashes"][name] = digest
    if affected:
        metadata[owner_path] = encode(target_owner)
        metadata.update(migrate_legacy_owners(root, manifests, affected))
    return merged, metadata


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
    result = {}
    for text in pieces:
        document = json.loads(text)
        if "mcp" in document:
            servers = result.setdefault("mcp", {}).setdefault("servers", {})
            if set(servers) & set(document["mcp"]["servers"]):
                msg = "Shared MCP server name collision. Rename one source server."
                raise ValueError(msg)
            servers.update(document["mcp"]["servers"])
        for event, handlers in document.get("hooks", {}).items():
            result.setdefault("hooks", {}).setdefault(event, []).extend(handlers)
    return (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()


def encode(document: dict) -> bytes:
    """Serialize ownership metadata deterministically."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def migrate_legacy_owners(root: Path, manifests: dict, affected: set[str]) -> dict[str, bytes]:
    """Remove legacy shared-file claims after validating the aggregate content."""
    metadata = {}
    for path, data in manifests.items():
        if not data.get("plugin"):
            continue
        old_shared = set(data.get("hashes", {})) & affected
        if old_shared:
            for key in ("hashes", "owners"):
                data[key] = {name: value for name, value in data.get(key, {}).items() if name not in affected}
            for key in ("files", "executables"):
                data[key] = [name for name in data.get(key, []) if name not in affected]
            metadata[str(path.relative_to(root))] = encode(data)
    return metadata
