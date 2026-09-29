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
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                changed.append(relative)
    return {"intact": not changed, "changed": sorted(changed), "components": components, "runtime_verified": False}
