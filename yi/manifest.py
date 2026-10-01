"""Read and validate persisted artifact ownership metadata."""

import json
from pathlib import Path

from yi.safety import require_object


def read_manifest(path: Path) -> dict:
    """Validate persisted containers before generation, inspection, or installation."""
    if path.is_symlink() or not path.is_file():
        msg = f"Artifact manifest must be a regular file: {path.name}"
        raise ValueError(msg)
    document = require_object(json.loads(path.read_text(encoding="utf-8")), "Artifact manifest")
    for field in ("hashes", "owners", "configuration"):
        entries = require_object(document.get(field, {}), f"Manifest {field}")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in entries.items()):
            msg = f"Manifest {field} requires string keys and values."
            raise TypeError(msg)
    for field in ("executables", "reviewed_files", "files"):
        entries = document.get(field, [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
            msg = f"Manifest {field} requires an array of paths."
            raise TypeError(msg)
    components = document.get("components", [])
    if not isinstance(components, list) or not all(isinstance(entry, dict) for entry in components):
        msg = "Manifest components requires an array of objects."
        raise TypeError(msg)
    return document
