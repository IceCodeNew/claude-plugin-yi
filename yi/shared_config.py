"""Rebuild shared target configuration from per-plugin contributions."""

import hashlib
import json
from pathlib import Path

SHARED = {
    "opencode-v2/home/.config/opencode/opencode.json",
    "codex/home/.codex/config.toml",
    "codex/home/.codex/hooks.json",
}


def prepare(root: Path, report: dict, files: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Merge only managed shared files and update all participating manifests."""
    merged = dict(files)
    metadata = {}
    manifests = {}
    for path in (root / "manifests").glob("*.json"):
        if path.is_symlink():
            msg = "Shared manifest must not be a symlink."
            raise ValueError(msg)
        manifests[path] = json.loads(path.read_text(encoding="utf-8"))
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
            if data.get("plugin") != report["plugin"]
        ]
        pieces.append((report["plugin"], contribution))
        merged[name] = combine(name, [content for _, content in sorted(pieces) if content])
        digest = hashlib.sha256(merged[name]).hexdigest()
        for path, data in manifests.items():
            if data.get("plugin") != report["plugin"] and name in data.get("hashes", {}):
                data["hashes"][name] = digest
                metadata[str(path.relative_to(root))] = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    return merged, metadata


def combine(name: str, pieces: list[str]) -> bytes:
    """Combine namespaced tables or hook groups without executing their content."""
    if name.endswith(".toml"):
        return ("\n".join(pieces)).encode()
    result = {}
    for text in pieces:
        document = json.loads(text)
        if "mcp" in document:
            result.setdefault("mcp", {}).setdefault("servers", {}).update(document["mcp"]["servers"])
        for event, handlers in document.get("hooks", {}).items():
            result.setdefault("hooks", {}).setdefault(event, []).extend(handlers)
    return (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
