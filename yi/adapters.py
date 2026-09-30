"""Plan target files and report unsupported plugin behavior."""

import importlib
import json
import re
from pathlib import Path

from yi.catalog import checked_source, discover, source_manifest
from yi.frontmatter import read_yaml
from yi.safety import reject_sensitive
from yi.target_config import agent_files, hook_files, mcp_files
from yi.targets import SKILL_ROOTS


def preview(
    source: Path, target: str, selected: list[str] | None = None, *, manifest: dict | None = None
) -> tuple[dict, dict[str, bytes]]:
    """Build a conversion plan without changing the filesystem."""
    source = checked_source(source)
    manifest = source_manifest(source) if manifest is None else manifest
    plugin = manifest["name"]
    if not isinstance(plugin, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin):
        msg = "Plugin name must use lowercase letters, digits, and hyphens."
        raise ValueError(msg)
    files = {}
    owners = {}
    executables = []
    components = []
    inventory = discover(source, manifest)
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
                files.update(converted)
                owners.update(dict.fromkeys(converted, item["name"]))
                components.append(
                    {
                        **item,
                        "status": "unverified",
                        "reason": "Native prompt prepared without source preapproval. Verify target behavior.",
                    }
                )
            continue
        destination = Path(target) / "home" / SKILL_ROOTS[target] / f"{plugin}-{path.parent.name}"
        converted_skill = convert_skill(path, destination.name, target)
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
        resources.update(skill_policy(path, destination, target))
        files.update(resources)
        owners.update(dict.fromkeys(resources, item["name"]))
        executables.extend(executable_resources)
        components.extend(runtime_dependencies(item, resources))
        components.append(
            {
                **item,
                "status": "unverified",
                "reason": "No source preapproval is transferred. Target permissions apply. Verify behavior.",
            }
        )
    rewrite_skill_links(files, inventory, plugin, target)
    if not selected and not manifest.get("standalone"):
        components.extend(plugin_blockers(manifest, target))
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


def command_file(path: Path, target: str, plugin: str) -> dict[str, bytes] | None:
    """Translate plain Markdown commands without execution semantics."""
    roots = {
        "opencode-v2": ".config/opencode/commands",
        "pi": ".pi/agent/prompts",
        "codex": ".agents/skills",
        "ampcode": ".config/amp/plugins",
    }
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = content.decode("utf-8")
    metadata = {}
    body = text
    if text.startswith("---\n"):
        header, separator, body = text[4:].partition("\n---\n")
        if not separator:
            return None
        metadata = read_yaml(header)
    if (
        not isinstance(metadata, dict)
        or set(metadata)
        - {
            "description",
            "argument-hint",
            "allowed-tools",
            "disable-model-invocation",
        }
        or not isinstance(metadata.get("disable-model-invocation", False), bool)
    ):
        return None
    metadata = {
        key: value for key, value in metadata.items() if key not in {"allowed-tools", "disable-model-invocation"}
    }
    parser = importlib.import_module("yaml")
    text = "---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body
    without_arguments = re.sub(r"\$(?:ARGUMENTS\b|[1-9](?![0-9]))", "", text)
    if (
        any(token in text for token in ("!`", "CLAUDE_PLUGIN_ROOT"))
        or re.search(r"(?<![A-Za-z0-9])@(?:[./~]|[A-Za-z0-9_-]+/)", text)
        or re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*", without_arguments)
    ) or (
        (target == "ampcode" and re.search(r"\$[1-9]", body))
        or (target == "codex" and re.search(r"\$(?:ARGUMENTS\b|[1-9])", body))
    ):
        return None
    if target == "ampcode":
        name, content = amp_command(plugin, path.stem, metadata, body)
        return {name: content}
    if target == "codex":
        return codex_command(plugin, path.stem, metadata, body)
    destination = Path(target) / "home" / roots[target] / f"{plugin}-{path.stem}.md"
    return {str(destination): text.encode()}


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


def convert_skill(path: Path, name: str, target: str = "pi") -> bytes | None:
    """Validate and namespace portable skill content in one pass."""
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = content.decode("utf-8")
    if "CLAUDE_PLUGIN_ROOT" in text or "!`" in text or not text.startswith("---\n"):
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
        "version",
        "allowed-tools",
        "disable-model-invocation",
        "argument-hint",
    }:
        return None
    manual = metadata.get("disable-model-invocation", False)
    if (
        not isinstance(manual, bool)
        or (manual and target == "ampcode")
        or not isinstance(metadata.get("metadata", {}), dict)
    ):
        return None
    metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"allowed-tools", "argument-hint", "disable-model-invocation"}
    }
    if manual and target == "pi":
        metadata["disable-model-invocation"] = True
    if manual and target == "opencode-v2":
        descriptive = metadata.get("metadata", {})
        if isinstance(descriptive, dict):
            metadata["metadata"] = {**descriptive, "opencode/autoinvoke": False}
    metadata["name"] = name
    if "version" in metadata:
        descriptive = metadata.get("metadata", {})
        if not isinstance(descriptive, dict):
            return None
        metadata["metadata"] = {**descriptive, "source-version": str(metadata.pop("version"))}
    parser = importlib.import_module("yaml")
    return ("---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body).encode()


def plugin_blockers(manifest: dict, target: str) -> list[dict]:
    """Report unsupported whole-plugin capabilities without omission."""
    components = []
    if "lspServers" in manifest:
        reason = (
            (
                "OpenCode 2.0.19 accepts LSP configuration but its LSP runtime is not implemented. "
                "Use a target release with verified runtime support."
            )
            if target == "opencode-v2"
            else (
                "No supported native LSP registration is verified for this target. "
                "An editor integration or reviewed extension is required."
            )
        )
        components.append({"kind": "lspServers", "status": "blocked", "reason": reason})
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
        "lspServers",
    }
    components.extend(
        {"kind": key, "status": "blocked", "reason": "Unknown manifest capability requires target-harness review."}
        for key in sorted(set(manifest) - known)
    )
    return components


def amp_command(plugin: str, name: str, metadata: dict, body: str) -> tuple[str, bytes]:
    """Wrap a prompt in Amp's palette API, using a dialog for raw arguments."""
    options = {"title": f"{plugin}: {name}", "category": plugin, "description": metadata.get("description", name)}
    lines = [
        "export default function (amp) {",
        f"  amp.registerCommand({json.dumps(plugin + '.' + name)}, {json.dumps(options)}, async (ctx) => {{",
        "    if (!ctx.thread) { await ctx.ui.notify('Start a thread before using this command.'); return; }",
    ]
    if "$ARGUMENTS" in body:
        dialog = {"title": "Arguments", "helpText": metadata.get("argument-hint", ""), "requireHuman": True}
        lines.extend(
            [
                f"    const args = await ctx.ui.input({json.dumps(dialog)});",
                "    if (args === undefined) return;",
                f"    const content = {json.dumps(body)}.replaceAll('$ARGUMENTS', () => args);",
            ]
        )
    else:
        lines.append(f"    const content = {json.dumps(body)};")
    lines.extend(["    await ctx.thread.appendUserMessage({type: 'user-message', content});", "  });", "}"])
    return f"ampcode/home/.config/amp/plugins/{plugin}-{name}.js", ("\n".join(lines) + "\n").encode()


def codex_command(plugin: str, name: str, metadata: dict, body: str) -> dict[str, bytes]:
    """Replace removed custom prompts with explicitly invoked native skills."""
    identifier = f"{plugin}-{name}"
    destination = f"codex/home/.agents/skills/{identifier}"
    parser = importlib.import_module("yaml")
    header = {"name": identifier, "description": metadata.get("description", f"Run {name} explicitly.")}
    return {
        f"{destination}/SKILL.md": ("---\n" + parser.safe_dump(header) + "---\n" + body).encode(),
        f"{destination}/agents/openai.yaml": b"policy:\n  allow_implicit_invocation: false\n",
    }


def skill_policy(path: Path, destination: Path, target: str) -> dict[str, bytes]:
    """Emit target sidecars for explicit-only source skills."""
    if target != "codex":
        return {}
    header = path.read_text(encoding="utf-8")[4:].partition("\n---\n")[0]
    metadata = read_yaml(header)
    if isinstance(metadata, dict) and metadata.get("disable-model-invocation") is True:
        sidecar = path.parent / "agents/openai.yaml"
        existing = read_yaml(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        if not isinstance(existing, dict):
            msg = "Codex skill sidecar must contain a mapping."
            raise TypeError(msg)
        policy = existing.get("policy", {})
        if not isinstance(policy, dict):
            msg = "Codex skill policy must contain a mapping."
            raise TypeError(msg)
        document = {**existing, "policy": {**policy, "allow_implicit_invocation": False}}
        parser = importlib.import_module("yaml")
        return {str(destination / "agents/openai.yaml"): parser.safe_dump(document, sort_keys=False).encode()}
    return {}


def rewrite_skill_links(files: dict[str, bytes], inventory: list[dict], plugin: str, target: str) -> None:
    """Resolve known sibling skill links against the generated inventory."""
    siblings = {Path(item["path"]).parent.name for item in inventory if item["kind"] == "skill"}
    root = Path(target) / "home" / SKILL_ROOTS[target]
    entries = {str(root / f"{plugin}-{sibling}" / "SKILL.md") for sibling in siblings}
    for name, content in files.items():
        if name not in entries:
            continue
        text = content.decode("utf-8")

        def replace(match: re.Match) -> str:
            sibling = match.group(1)
            if sibling not in siblings:
                return match.group(0)
            destination = root / f"{plugin}-{sibling}" / "SKILL.md"
            if str(destination) not in files:
                msg = f"Missing skill dependency: {plugin}:{sibling}. Include it in the migration."
                raise ValueError(msg)
            return f"../{plugin}-{sibling}/SKILL.md"

        text = re.sub(r"\.\./([^/\s]+)/SKILL\.md", replace, text)
        files[name] = text.encode()


def runtime_dependencies(item: dict, resources: dict[str, bytes]) -> list[dict]:
    """Expose recognizable host-specific executable references without executing code."""
    dependencies = []
    for name, content in resources.items():
        if Path(name).suffix not in {".py", ".sh", ".js", ".mjs", ".ts"}:
            continue
        text = content.decode("utf-8", errors="replace")
        if re.search(r"[\"']claude[\"']\s*,\s*[\"']-p[\"']|\bclaude\s+-p\b", text):
            dependencies.append(
                {
                    "name": item["name"],
                    "kind": "runtime-dependency",
                    "path": name,
                    "status": "blocked",
                    "reason": "Bundled code invokes Claude Code. Adapt this runtime before execution.",
                }
            )
    return dependencies
