"""Plan target files and report unsupported plugin behavior."""

import importlib
import re
from pathlib import Path

from yi.catalog import checked_source, discover, source_manifest
from yi.frontmatter import read_yaml
from yi.safety import reject_sensitive
from yi.target_config import agent_files, hook_files, mcp_files

SKILL_ROOTS = {
    "ampcode": ".config/amp/skills",
    "codex": ".agents/skills",
    "opencode-v2": ".config/opencode/skills",
    "pi": ".pi/agent/skills",
}


def preview(source: Path, target: str, selected: list[str] | None = None) -> tuple[dict, dict[str, bytes]]:
    """Build a conversion plan without changing the filesystem."""
    source = checked_source(source)
    manifest = source_manifest(source)
    plugin = manifest["name"]
    if not isinstance(plugin, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin):
        msg = "Plugin name must use lowercase letters, digits, and hyphens."
        raise ValueError(msg)
    files = {}
    owners = {}
    executables = []
    components = []
    inventory = discover(source)
    unknown = set(selected or []) - {item["name"] for item in inventory}
    if unknown:
        msg = "Unknown selected components: " + ", ".join(sorted(unknown))
        raise ValueError(msg)
    for item in inventory:
        if selected and item["name"] not in selected:
            continue
        path = Path(item["path"])
        if item["kind"] != "skill":
            converted = command_file(path, target, plugin)
            if converted is None:
                components.append(
                    {**item, "status": "blocked", "reason": "Command semantics require target-harness review."}
                )
            else:
                name, content = converted
                files[name] = content
                owners[name] = item["name"]
                components.append(
                    {**item, "status": "unverified", "reason": "Native prompt prepared; verify target behavior."}
                )
            continue
        destination = Path(target) / "home" / SKILL_ROOTS[target] / f"{plugin}-{path.parent.name}"
        converted_skill = convert_skill(path, destination.name)
        if converted_skill is None:
            components.append(
                {
                    **item,
                    "status": "blocked",
                    "reason": "Skill metadata or plugin-root references require target adaptation.",
                }
            )
            continue
        resources, executable_resources = skill_files(path.parent, destination)
        resources[str(destination / "SKILL.md")] = converted_skill
        files.update(resources)
        owners.update(dict.fromkeys(resources, item["name"]))
        executables.extend(executable_resources)
        components.append({**item, "status": "unverified", "reason": "Files prepared; validate target skill behavior."})
    if not selected and not manifest.get("standalone"):
        components.extend(plugin_blockers(manifest))
        for kind, converter in (("mcp", mcp_files), ("agents", agent_files), ("hooks", hook_files)):
            outputs, diagnostics = converter(source, manifest, target)
            files.update(outputs)
            owners.update(dict.fromkeys(outputs, f"{plugin}:{kind}"))
            components.extend(diagnostics)
    return {
        "plugin": plugin,
        "selection": selected,
        "target": target,
        "files": sorted(files),
        "owners": owners,
        "executables": sorted(executables),
        "components": components,
        "complete": False,
    }, files


def command_file(path: Path, target: str, plugin: str) -> tuple[str, bytes] | None:
    """Translate plain Markdown commands without execution semantics."""
    roots = {"opencode-v2": ".config/opencode/commands", "pi": ".pi/agent/prompts"}
    if target not in roots:
        return None
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = content.decode("utf-8")
    metadata = {}
    if text.startswith("---\n"):
        header, separator, _body = text[4:].partition("\n---\n")
        if not separator:
            return None
        metadata = read_yaml(header)
    if not isinstance(metadata, dict) or set(metadata) - {"description"}:
        return None
    if any(token in text for token in ("$", "!`", "${CLAUDE_PLUGIN_ROOT}", "@")):
        return None
    destination = Path(target) / "home" / roots[target] / f"{plugin}-{path.stem}.md"
    return str(destination), text.encode()


def skill_files(source: Path, destination: Path) -> tuple[dict[str, bytes], list[str]]:
    """Collect regular skill resources without following links."""
    files = {}
    executables = []
    for resource in sorted(source.rglob("*")):
        if resource.is_symlink():
            msg = f"Source symlinks require review: {resource}"
            raise ValueError(msg)
        if resource.is_file():
            relative = resource.relative_to(source)
            if resource.name.startswith(".env") or ".git" in relative.parts:
                msg = f"Sensitive source resource requires review: {resource}"
                raise ValueError(msg)
            content = resource.read_bytes()
            reject_sensitive(resource, content)
            name = str(destination / relative)
            if relative != Path("SKILL.md"):
                files[name] = content
            if resource.stat().st_mode & 0o111:
                executables.append(name)
    return files, executables


def convert_skill(path: Path, name: str) -> bytes | None:
    """Validate and namespace portable skill content in one pass."""
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = content.decode("utf-8")
    if "${CLAUDE_PLUGIN_ROOT}" in text or "!`" in text or not text.startswith("---\n"):
        return None
    header, separator, body = text[4:].partition("\n---\n")
    if not separator:
        return None
    metadata = read_yaml(header)
    if not isinstance(metadata, dict) or set(metadata) - {
        "name",
        "description",
        "license",
        "metadata",
        "compatibility",
    }:
        return None
    metadata = {**metadata, "name": name}
    parser = importlib.import_module("yaml")
    return ("---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body).encode()


def plugin_blockers(manifest: dict) -> list[dict]:
    """Report unsupported whole-plugin capabilities without omission."""
    components = []
    known = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "skills",
        "commands",
        "agents",
        "hooks",
        "mcpServers",
    }
    components.extend(
        {"kind": key, "status": "blocked", "reason": "Unknown manifest capability requires target-harness review."}
        for key in sorted(set(manifest) - known)
    )
    return components
