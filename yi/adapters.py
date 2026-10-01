"""Plan target files and report unsupported plugin behavior."""

import json
import re
from pathlib import Path

from yi.catalog import checked_source, discover, source_manifest
from yi.frontmatter import read_yaml, yaml_parser
from yi.safety import reject_sensitive
from yi.target_config import agent_files, hook_files, mcp_files
from yi.targets import SKILL_ROOTS

MAX_SKILL_NAME_LENGTH = 64


def preview(
    source: Path,
    target: str,
    selected: list[str] | None = None,
    *,
    manifest: dict | None = None,
    model_mapping: dict[str, str] | None = None,
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
        resources, executable_resources, diagnostics = portable_component(item, target, plugin)
        collisions = sorted(files.keys() & resources.keys())
        if collisions:
            msg = f"Native output collision for {item['name']}: {', '.join(collisions)}. Select one source."
            raise ValueError(msg)
        files.update(resources)
        owners.update(dict.fromkeys(resources, item["name"]))
        executables.extend(executable_resources)
        components.extend(diagnostics)
    components.extend(rewrite_skill_links(files, inventory, plugin, target, selected))
    owners, executables, components = retained_resources(files, owners, executables, components)
    if not selected and not manifest.get("standalone"):
        components.extend(plugin_blockers(manifest, target))
        for kind, converter in (("mcp", mcp_files), ("agents", agent_files), ("hooks", hook_files)):
            outputs, diagnostics = (
                agent_files(source, manifest, target, model_mapping=model_mapping)
                if kind == "agents"
                else converter(source, manifest, target)
            )
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


class PromptBlockerError(ValueError):
    """An unsupported source requirement that blocks only its component."""


def portable_component(item: dict, target: str, plugin: str) -> tuple[dict, list, list]:
    """Plan one prompt and preserve independent components when it is unsupported."""
    path = Path(item["path"])
    executables = []
    try:
        if item["kind"] == "command":
            resources = command_file(path, target, plugin)
            reason = "Native prompt prepared without source preapproval. Verify target behavior."
            if target == "codex" and "_" in path.stem:
                reason += f" Invoke as {plugin}-{path.stem.replace('_', '-')} in Codex."
        else:
            destination = Path(target) / "home" / SKILL_ROOTS[target] / f"{plugin}-{path.parent.name}"
            converted = convert_skill(path, destination.name, target)
            resources, executables = skill_files(path.parent, destination)
            resources[str(destination / "SKILL.md")] = converted
            resources.update(skill_policy(path, destination, target))
            reason = "No source preapproval is transferred. Target permissions apply. Verify behavior."
    except PromptBlockerError as error:
        return (
            {},
            [],
            [{**item, "status": "blocked", "reason": f"{path}: {error} Adapt this component before migration."}],
        )
    diagnostics = runtime_dependencies(item, resources)
    diagnostics.append({**item, "status": "unverified", "reason": reason})
    return resources, executables, diagnostics


def prompt_document(path: Path, kind: str) -> tuple[dict, str]:
    """Parse prompt frontmatter and report unsupported source requirements."""
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = content.decode("utf-8")
    context_syntax(text)
    metadata = {}
    body = text
    if text.startswith("---\n"):
        header, separator, body = text[4:].partition("\n---\n")
        if not separator:
            msg = "Invalid frontmatter: closing delimiter is missing."
            raise PromptBlockerError(msg)
        metadata = read_yaml(header)
    elif kind == "skill":
        msg = "Skill frontmatter is missing."
        raise PromptBlockerError(msg)
    return validate_prompt_metadata(metadata, kind), body


def validate_prompt_metadata(metadata: object, kind: str) -> dict:
    """Check the fields that the target prompt conversion can preserve."""
    if not isinstance(metadata, dict):
        msg = "Frontmatter must be a mapping."
        raise PromptBlockerError(msg)
    allowed = {"description", "argument-hint", "allowed-tools", "disable-model-invocation"}
    allowed |= {"name", "license", "metadata", "compatibility", "version"} if kind == "skill" else set()
    if any(not isinstance(key, str) for key in metadata):
        msg = "frontmatter-keys: field names must be strings."
        raise PromptBlockerError(msg)
    unsupported = set(metadata) - allowed
    if unsupported:
        msg = f"Unsupported {kind} fields: " + ", ".join(sorted(unsupported))
        raise PromptBlockerError(msg)
    if not isinstance(metadata.get("disable-model-invocation", False), bool):
        msg = "disable-model-invocation must be a boolean."
        raise PromptBlockerError(msg)
    if kind == "skill":
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            msg = "description must be a nonempty string."
            raise PromptBlockerError(msg)
        if not isinstance(metadata.get("metadata", {}), dict):
            msg = "metadata must be a mapping."
            raise PromptBlockerError(msg)
    return metadata


def context_syntax(text: str) -> None:
    """Keep source context execution and runtime-root references blocked."""
    if "!`" in text:
        msg = "inline-context-execution: !` is not evaluated by this converter."
        raise PromptBlockerError(msg)
    if "CLAUDE_PLUGIN_ROOT" in text:
        msg = "plugin-root-reference: CLAUDE_PLUGIN_ROOT requires resource or runtime relocation."
        raise PromptBlockerError(msg)


def command_syntax(text: str, body: str, target: str) -> None:
    """Reject command-only attachment and unmapped substitution semantics."""
    without_arguments = re.sub(r"\$(?:ARGUMENTS\b|[1-9](?![0-9]))", "", text)
    if re.search(r"(?<![A-Za-z0-9])@(?:[./~]|[A-Za-z0-9_-]+/)", text):
        msg = "attachment-reference: @path requires target attachment semantics."
        raise PromptBlockerError(msg)
    if re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*", without_arguments):
        msg = "unmapped-variable: named $variables require target template review."
        raise PromptBlockerError(msg)
    if (target == "ampcode" and re.search(r"\$[1-9]", body)) or (
        target == "codex" and re.search(r"\$(?:ARGUMENTS\b|[1-9])", body)
    ):
        msg = "native-arguments-unavailable: positional or $ARGUMENTS substitution is not supported."
        raise PromptBlockerError(msg)


def command_file(path: Path, target: str, plugin: str) -> dict[str, bytes]:
    """Translate plain Markdown commands without execution semantics."""
    roots = {
        "opencode-v2": ".config/opencode/commands",
        "pi": ".pi/agent/prompts",
        "codex": ".agents/skills",
        "ampcode": ".config/amp/plugins",
    }
    metadata, body = prompt_document(path, "command")
    metadata = {
        key: value for key, value in metadata.items() if key not in {"allowed-tools", "disable-model-invocation"}
    }
    parser = yaml_parser()
    text = "---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body
    command_syntax(text, body, target)
    if target == "ampcode":
        name, content = amp_command(plugin, path.stem, metadata, body)
        return {name: content}
    if target == "codex":
        alias = path.stem.replace("_", "-")
        if not valid_skill_name(f"{plugin}-{alias}"):
            msg = f"invalid-native-identifier: {plugin}-{alias} does not meet Codex skill name rules."
            raise PromptBlockerError(msg)
        return codex_command(plugin, alias, metadata, body)
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


def convert_skill(path: Path, name: str, target: str = "pi") -> bytes:
    """Validate and namespace portable skill content in one pass."""
    metadata, body = prompt_document(path, "skill")
    if not valid_skill_name(name):
        msg = f"invalid-native-identifier: {name} does not meet native skill name rules."
        raise PromptBlockerError(msg)
    manual = metadata.get("disable-model-invocation", False)
    if manual and target == "ampcode":
        msg = "manual-invocation-policy-unavailable: Amp has no verified explicit-only skill policy."
        raise PromptBlockerError(msg)
    metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"allowed-tools", "argument-hint", "disable-model-invocation"}
    }
    if manual and target == "pi":
        metadata["disable-model-invocation"] = True
    if manual and target == "opencode-v2":
        metadata["metadata"] = {**metadata.get("metadata", {}), "opencode/autoinvoke": False}
    metadata["name"] = name
    if "version" in metadata:
        descriptive = metadata.get("metadata", {})
        metadata["metadata"] = {**descriptive, "source-version": str(metadata.pop("version"))}
    parser = yaml_parser()
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
    parser = yaml_parser()
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
        parser = yaml_parser()
        return {str(destination / "agents/openai.yaml"): parser.safe_dump(document, sort_keys=False).encode()}
    return {}


def rewrite_skill_links(
    files: dict[str, bytes], inventory: list[dict], plugin: str, target: str, selected: list[str] | None = None
) -> list[dict]:
    """Rewrite available sibling links and isolate blocked dependency components."""
    siblings = {Path(item["path"]).parent.name for item in inventory if item["kind"] == "skill"}
    root = Path(target) / "home" / SKILL_ROOTS[target]
    entries = {str(root / f"{plugin}-{sibling}" / "SKILL.md"): sibling for sibling in sorted(siblings)}
    dependencies = {}
    for name in (entry for entry in entries if entry in files):
        text = files[name].decode("utf-8")
        dependencies[name] = set(re.findall(r"\.\./([^/\s]+)/SKILL\.md", text)) & siblings
    diagnostics = []
    while True:
        unavailable = {
            name: refs
            for name, refs in dependencies.items()
            if name in files and any(str(root / f"{plugin}-{ref}" / "SKILL.md") not in files for ref in refs)
        }
        if not unavailable:
            break
        for name, refs in unavailable.items():
            missing = {ref for ref in refs if str(root / f"{plugin}-{ref}" / "SKILL.md") not in files}
            if selected and any(f"{plugin}:{ref}" not in selected for ref in missing):
                msg = "Missing skill dependencies: " + ", ".join(f"{plugin}:{ref}" for ref in sorted(missing))
                raise ValueError(msg)
            diagnostics.append(
                {
                    "name": f"{plugin}:{entries[name]}",
                    "kind": "skill-dependency",
                    "status": "blocked",
                    "reason": "Selected skill has incompatible dependencies: " + ", ".join(sorted(missing)),
                }
            )
            directory = str(Path(name).parent) + "/"
            for resource in list(files):
                if resource.startswith(directory):
                    del files[resource]
    for name in (entry for entry in dependencies if entry in files):
        files[name] = re.sub(
            r"\.\./([^/\s]+)/SKILL\.md",
            lambda match: f"../{plugin}-{match[1]}/SKILL.md" if match[1] in siblings else match[0],
            files[name].decode("utf-8"),
        ).encode()
    return diagnostics


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


def retained_resources(files: dict, owners: dict, executables: list, components: list) -> tuple[dict, list, list]:
    """Keep metadata aligned after dependency-blocked files are removed."""
    active = set(files)
    owners = {name: owner for name, owner in owners.items() if name in active}
    executables = [name for name in executables if name in active]
    blocked_names = {item["name"] for item in components if item.get("kind") == "skill-dependency"}
    components = [
        item
        for item in components
        if not (item["kind"] == "skill" and item["name"] in blocked_names)
        and not (item["kind"] == "runtime-dependency" and item.get("path") not in active)
    ]
    return owners, executables, components


def valid_skill_name(name: str) -> bool:
    """Accept relocatable target skill identifiers without truncating collisions."""
    return len(name) <= MAX_SKILL_NAME_LENGTH and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None
