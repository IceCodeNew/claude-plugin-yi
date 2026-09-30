"""Discover local plugin components without executing their content."""

import hashlib
import json
import re
from pathlib import Path


def discover(source: Path, manifest: dict | None = None) -> list[dict[str, str]]:
    """List conventional plugin skills and legacy commands."""
    source = checked_source(source)
    manifest = source_manifest(source) if manifest is None else manifest
    plugin = manifest["name"]
    if manifest.get("standalone"):
        path = source / "SKILL.md" if source.is_dir() else source
        return [
            {
                "name": path.parent.name if path.name == "SKILL.md" else path.stem,
                "kind": "skill" if path.name == "SKILL.md" else "command",
                "plugin": plugin,
                "path": str(path),
                "source": str(source),
            }
        ]
    items = []
    for kind, folder, pattern in (("skill", "skills", "*/SKILL.md"), ("command", "commands", "**/*.md")):
        for base in component_roots(source, folder, manifest.get(folder, [])):
            if kind == "skill" and (base / "SKILL.md").is_file():
                paths = [base / "SKILL.md"]
            else:
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
    data = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {"plugins": {}}
    items = standalone_items(root)
    seen = set()
    for index_name, entries in data["plugins"].items():
        for entry in entries:
            source = Path(entry["installPath"])
            if source in seen:
                continue
            seen.add(source)
            items.extend(installed_entry(source, index_name, root))
    return items


def source_manifest(source: Path) -> dict:
    """Describe a plugin or a directly selected standalone resource."""
    manifest = source / ".claude-plugin/plugin.json"
    if manifest.is_file():
        return json.loads(manifest.read_text(encoding="utf-8"))
    skill = source / "SKILL.md" if source.is_dir() else source
    if not skill.is_file() or skill.suffix != ".md":
        msg = f"Source is not a plugin, skill directory, or Markdown command: {source}"
        raise ValueError(msg)
    name = skill.parent.name if skill.name == "SKILL.md" else skill.stem
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "item"
    digest = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:10]
    return {"name": f"standalone-{slug}-{digest}", "standalone": True}


def standalone_items(root: Path) -> list[dict[str, str]]:
    """Discover user or project resources independent of installed plugins."""
    sources = [path.parent for path in sorted((root / "skills").glob("*/SKILL.md"))]
    sources.extend(sorted((root / "commands").rglob("*.md")))
    items = []
    for source in sources:
        try:
            items.extend(discover(source))
        except (ValueError, OSError) as error:
            items.append(
                {
                    "name": source.name if source.is_dir() else source.stem,
                    "kind": "skill" if source.is_dir() else "command",
                    "source": str(source),
                    "path": str(source),
                    "status": "unresolved",
                    "reason": str(error),
                }
            )
    return items


def checked_source(source: Path) -> Path:
    """Resolve explicit source paths only after rejecting symlink components."""
    source = source.expanduser().absolute()
    if source.is_symlink():
        msg = f"Source symlink requires review: {source}"
        raise ValueError(msg)
    return source.resolve()


def rank(items: list[dict], counts: dict[str, int], plugins: dict[str, int]) -> list[dict]:
    """Join observable counts without losing source identity or zero-use items."""
    result = [
        {**item, "count": (plugins if item["kind"] == "plugin" else counts).get(item["name"], 0)} for item in items
    ]
    return sorted(result, key=lambda item: (-item["count"], item["name"], item["path"]))


def installed_entry(source: Path, index_name: str, claude_root: Path | None = None) -> list[dict[str, str]]:
    """Keep one invalid installation from hiding unrelated catalog entries."""
    unresolved = {"name": index_name.split("@", 1)[0], "kind": "plugin", "source": str(source), "path": str(source)}
    try:
        path = source / ".claude-plugin/plugin.json"
        manifest = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else marketplace_manifest(claude_root, index_name)
        )
        name = manifest["name"]
        if not isinstance(name, str) or not name:
            return [{**unresolved, "status": "unresolved", "reason": "Plugin name must be a nonempty string."}]
        if not source.is_dir():
            return [{**unresolved, "status": "unresolved", "reason": "Installed source directory is missing."}]
        components = discover(source, manifest)
    except (ValueError, OSError, KeyError, TypeError) as error:
        return [{**unresolved, "status": "unresolved", "reason": str(error)}]
    return [{**unresolved, "name": name}, *({**item, "source": str(source)} for item in components)]


def marketplace_manifest(claude_root: Path | None, identifier: str) -> dict:
    """Resolve metadata only from the explicitly registered marketplace."""
    if claude_root is None or "@" not in identifier:
        msg = "Installed source has no manifest or marketplace registration."
        raise ValueError(msg)
    name, market = identifier.rsplit("@", 1)
    registry = json.loads((claude_root / "plugins/known_marketplaces.json").read_text(encoding="utf-8"))
    root = Path(registry[market]["installLocation"])
    document = json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("plugins"), list):
        msg = "Marketplace must contain a plugins array."
        raise TypeError(msg)
    if not all(isinstance(entry, dict) for entry in document["plugins"]):
        msg = "Marketplace plugin entries must be objects."
        raise ValueError(msg)
    matches = [entry for entry in document["plugins"] if entry.get("name") == name]
    if len(matches) != 1:
        msg = f"Marketplace plugin identity is missing or ambiguous: {identifier}"
        raise ValueError(msg)
    installation_fields = {"source", "strict", "category", "tags"}
    return {key: value for key, value in matches[0].items() if key not in installation_fields}


def registered_manifest(source: Path, claude_root: Path | None) -> dict:
    """Resolve a source through an explicit installation registry when needed."""
    if (source / ".claude-plugin/plugin.json").is_file() or claude_root is None:
        return source_manifest(source)
    index = json.loads((claude_root / "plugins/installed_plugins.json").read_text(encoding="utf-8"))
    matches = [
        name
        for name, entries in index["plugins"].items()
        if any(Path(entry["installPath"]).resolve() == source.resolve() for entry in entries)
    ]
    if not matches:
        return source_manifest(source)
    if len(matches) != 1:
        msg = "Source has ambiguous marketplace registrations."
        raise ValueError(msg)
    return marketplace_manifest(claude_root, matches[0])
