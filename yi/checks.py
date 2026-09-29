"""Inspect generated artifacts without executing migrated content."""

import hashlib
import json
from pathlib import Path


def inspect(root: Path) -> dict:
    """Compare artifact hashes and retain unresolved component diagnostics."""
    root = root.resolve()
    manifests = sorted((root / "manifests").glob("*.json"))
    if not manifests:
        msg = f"No migration manifests found in {root}."
        raise ValueError(msg)
    changed = []
    components = []
    for manifest in manifests:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        components.extend(data["components"])
        for relative, expected in data["hashes"].items():
            path = root / relative
            if not path.resolve().is_relative_to(root):
                msg = f"Manifest path escapes artifact root: {relative}"
                raise ValueError(msg)
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
                or bool(path.stat().st_mode & 0o111) != (relative in data.get("executables", []))
            ):
                changed.append(relative)
    return {"intact": not changed, "changed": sorted(changed), "components": components, "runtime_verified": False}


def accept_changes(root: Path) -> None:
    """Accept explicitly reviewed existing resources without asserting behavior correctness."""
    from yi.artifacts import validate_paths  # noqa: PLC0415 - Keep integrity-only startup lightweight.
    from yi.safety import reject_sensitive  # noqa: PLC0415 - Validate edited bytes before accepting ownership.
    from yi.shared_config import SHARED  # noqa: PLC0415 - Shared fragments need explicit regeneration.

    root = root.resolve()
    updates = {}
    for manifest in sorted((root / "manifests").glob("*.json")):
        validate_paths(root, [str(manifest.relative_to(root))])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        hashes = {}
        reviewed = set(data.get("reviewed_files", []))
        executable = []
        for relative in data["hashes"]:
            validate_paths(root, [relative])
            path = root / relative
            if not path.is_file():
                msg = f"Reviewed artifact is missing: {relative}. Restore it before accepting changes."
                raise ValueError(msg)
            content = path.read_bytes()
            reject_sensitive(path, content)
            digest = hashlib.sha256(content).hexdigest()
            if relative in SHARED and digest != data["hashes"][relative]:
                msg = f"Shared configuration requires source-fragment regeneration: {relative}"
                raise ValueError(msg)
            if digest != data["hashes"][relative] or bool(path.stat().st_mode & 0o111) != (
                relative in data.get("executables", [])
            ):
                reviewed.add(relative)
            hashes[relative] = digest
            if path.stat().st_mode & 0o111:
                executable.append(relative)
        data.update(hashes=hashes, files=sorted(hashes), executables=sorted(executable), complete=False)
        data["reviewed_files"] = sorted(reviewed)
        updates[manifest] = json.dumps(data, indent=2, sort_keys=True) + "\n"
    for manifest, text in updates.items():
        manifest.write_text(text, encoding="utf-8")
