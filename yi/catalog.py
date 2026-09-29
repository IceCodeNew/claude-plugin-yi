"""Discover local plugin components without executing their content."""

import json
from pathlib import Path


def discover(source: Path) -> list[dict[str, str]]:
    """List conventional plugin skills and legacy commands."""
    source = source.resolve()
    manifest = json.loads((source / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    plugin = manifest["name"]
    items = []
    for kind, folder, pattern in (("skill", "skills", "*/SKILL.md"), ("command", "commands", "**/*.md")):
        for base in component_roots(source, folder, manifest.get(folder, [])):
            paths = [base] if base.is_file() else sorted(base.glob(pattern))
            for path in paths:
                if any(parent.is_symlink() for parent in (path, *path.parents) if parent != source):
                    msg = f"Source symlink requires review: {path}"
                    raise ValueError(msg)
                name = path.parent.name if kind == "skill" else path.stem
                item = {"name": f"{plugin}:{name}", "kind": kind, "plugin": plugin, "path": str(path)}
                if item not in items:
                    items.append(item)
    names = [item["name"] for item in items]
    if len(names) != len(set(names)):
        msg = "Ambiguous component names; select distinct source definitions before migration."
        raise ValueError(msg)
    return items


def component_roots(source: Path, folder: str, declared: str | list[str]) -> list[Path]:
    """Resolve declared paths while enforcing plugin containment."""
    extras = [declared] if isinstance(declared, str) else declared
    roots = [source / folder]
    for relative in extras:
        path = source / relative
        if not path.resolve().is_relative_to(source):
            msg = f"Declared component path escapes plugin: {relative}"
            raise ValueError(msg)
        roots.append(path)
    return roots


def installed(root: Path) -> list[dict[str, str]]:
    """Discover indexed plugin installations without loading plugin code."""
    index = root / "plugins/installed_plugins.json"
    if not index.exists():
        return []
    data = json.loads(index.read_text(encoding="utf-8"))
    items = []
    seen = set()
    for entries in data["plugins"].values():
        for entry in entries:
            source = Path(entry["installPath"])
            if source not in seen:
                manifest = json.loads((source / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
                items.append({"name": manifest["name"], "kind": "plugin", "source": str(source), "path": str(source)})
                items.extend({**item, "source": str(source)} for item in discover(source))
                seen.add(source)
    return items
