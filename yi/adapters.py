"""Plan target files and report unsupported plugin behavior."""

import json
import posixpath
import re
import shlex
from pathlib import Path

from yi.catalog import checked_source, discover, source_manifest
from yi.frontmatter import read_yaml, yaml_parser
from yi.safety import reject_local_configuration, reject_sensitive
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
    inventory = [{**item, "source": str(source)} for item in discover(source, manifest)]
    unknown = set(selected or []) - {item["name"] for item in inventory}
    if unknown:
        msg = "Unknown selected components: " + ", ".join(sorted(unknown))
        raise ValueError(msg)
    files, owners, executables, components = plan_prompts(inventory, target, selected)
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


def plan_prompts(inventory: list[dict], target: str, selected: list[str] | None) -> tuple[dict, dict, list, list]:
    """Resolve skills before command dependencies, then check actual output collisions."""
    files, owners, executables, components = {}, {}, [], []
    for kind in ("skill", "command"):
        for item in inventory:
            if item["kind"] != kind or (selected and item["name"] not in selected):
                continue
            resources, executable_resources, diagnostics = portable_component(
                item, target, item["plugin"], Path(item["source"])
            )
            if kind == "command":
                candidate = {**files, **resources}
                candidate_owners = {**owners, **dict.fromkeys(resources, item["name"])}
                diagnostics.extend(
                    command_skill_links(
                        candidate,
                        candidate_owners,
                        [
                            {
                                **entry,
                                "available": owners.get(skill_entry(entry, target)) == entry["name"]
                                and skill_entry(entry, target) in files,
                            }
                            for entry in inventory
                            if entry["kind"] == "skill"
                        ]
                        + [item],
                        target,
                        selected,
                    )
                )
                resources = {name: value for name, value in candidate.items() if candidate_owners[name] == item["name"]}
            collisions = sorted(files.keys() & resources.keys())
            if collisions:
                msg = f"Native output collision for {item['name']}: {', '.join(collisions)}. Select one source."
                raise ValueError(msg)
            files.update(resources)
            owners.update(dict.fromkeys(resources, item["name"]))
            executables.extend(executable_resources)
            components.extend(diagnostics)
        if kind == "skill":
            components.extend(rewrite_skill_links(files, owners, inventory, target, selected))
    return files, owners, executables, components


def skill_entry(item: dict, target: str) -> str:
    """Locate the native entry for a catalogued source skill."""
    return str(
        Path(target) / "home" / SKILL_ROOTS[target] / f"{item['plugin']}-{Path(item['path']).parent.name}" / "SKILL.md"
    )


class PromptBlockerError(ValueError):
    """An unsupported source requirement that blocks only its component."""


def portable_component(item: dict, target: str, plugin: str, source: Path) -> tuple[dict, list, list]:
    """Plan one prompt and preserve independent components when it is unsupported."""
    path = Path(item["path"])
    executables = []
    try:
        text, documents = example_documents(source, item, target)
        if item["kind"] == "command":
            resources = command_file(path, target, plugin, text=text)
            reason = "Native prompt prepared without source preapproval. Verify target behavior."
            if target == "codex" and "_" in path.stem:
                reason += f" Invoke as {plugin}-{path.stem.replace('_', '-')} in Codex."
        else:
            destination = Path(target) / "home" / SKILL_ROOTS[target] / f"{plugin}-{path.parent.name}"
            converted = convert_skill(path, destination.name, target, text=text)
            resources, executables = skill_files(path.parent, destination)
            resources[str(destination / "SKILL.md")] = converted
            resources.update(skill_policy(path, destination, target, text=text))
            reason = "No source preapproval is transferred. Target permissions apply. Verify behavior."
    except PromptBlockerError as error:
        return (
            {},
            [],
            [{**item, "status": "blocked", "reason": f"{path}: {error} Adapt this component before migration."}],
        )
    resources.update(documents)
    diagnostics = runtime_dependencies(item, resources)
    diagnostics.append({**item, "status": "unverified", "reason": reason})
    return resources, executables, diagnostics


def decode_prompt(content: bytes) -> str:
    """Report invalid prompt encoding as one component's adaptation requirement."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        msg = "Prompt documents require valid UTF-8. Re-encode this source before migration."
        raise PromptBlockerError(msg) from error


def example_documents(source: Path, item: dict, target: str) -> tuple[str, dict[str, bytes]]:
    """Relocate the exact inert examples directory pointer without exporting runtime code."""
    path = Path(item["path"])
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = decode_prompt(content).replace("\r\n", "\n")
    pointer = "`${CLAUDE_PLUGIN_ROOT}/examples/`"
    if pointer not in text:
        return text, {}
    folder = source / "examples"
    if folder.is_symlink() or not folder.is_dir():
        msg = "document-resource: examples must be a regular contained directory."
        raise PromptBlockerError(msg)
    native = Path(".local/share/yi/resources") / item["plugin"] / item["kind"] / path.parent.name
    if item["kind"] == "command":
        native = native.parent / path.stem
    native /= "examples"
    files = {}
    for resource in sorted(folder.iterdir()):
        if (
            resource.is_symlink()
            or not resource.is_file()
            or resource.suffix not in {".md", ".txt"}
            or resource.stat().st_mode & 0o111
        ):
            msg = f"document-resource: inert regular Markdown/text required: {resource}"
            raise PromptBlockerError(msg)
        reject_local_configuration(resource.relative_to(source))
        data = resource.read_bytes()
        reject_sensitive(resource, data)
        decode_prompt(data)
        files[str(Path(target) / "home" / native / resource.name)] = data
    if not files:
        msg = "document-resource: examples directory is empty."
        raise PromptBlockerError(msg)
    text = documentary_pointer(text, pointer, f"`~/{native}/`")
    text += "\nResolve ~ against target HOME, not project CWD, before reading example paths. Examples stay inactive.\n"
    return text, files


def documentary_pointer(text: str, pointer: str, replacement: str) -> str:
    """Rewrite exact prose references but leave executable-language fences blocked."""
    lines = []
    fence = None
    documentary = False
    for original in text.splitlines(keepends=True):
        line = original
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})([^\n]*)", line)
        if marker and fence is None:
            fence = marker[1]
            documentary = marker[2].strip().lower() in {"", "text", "markdown", "md"}
        elif marker and fence and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
            fence = None
        elif fence is None or documentary:
            line = line.replace(pointer, replacement)
        lines.append(line)
    return "".join(lines)


def prompt_document(path: Path, kind: str, *, text: str | None = None) -> tuple[dict, str]:
    """Parse prompt frontmatter and report unsupported source requirements."""
    content = path.read_bytes()
    reject_sensitive(path, content)
    text = (decode_prompt(content) if text is None else text).replace("\r\n", "\n")
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


def command_syntax(text: str, target: str) -> None:
    """Reject command-only attachment and unmapped substitution semantics."""
    literal_checked = literal_shell_text(text)
    if target in {"pi", "opencode-v2"} and len(re.findall(r"\$[1-9]", literal_checked)) != len(
        re.findall(r"\$[1-9]", text)
    ):
        msg = "literal-template-collision: native argument expansion changes literal shell $1 fields."
        raise PromptBlockerError(msg)
    without_arguments = re.sub(r"\$(?:ARGUMENTS\b|[1-9](?![0-9]))", "", literal_checked)
    if re.search(r"(?<![A-Za-z0-9])@(?:[./~]|[A-Za-z0-9_-]+/)", text):
        msg = "attachment-reference: @path requires target attachment semantics."
        raise PromptBlockerError(msg)
    if re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*", without_arguments):
        msg = "unmapped-variable: named $variables require target template review."
        raise PromptBlockerError(msg)
    if (target == "ampcode" and re.search(r"\$[1-9]", literal_checked)) or (
        target == "codex" and re.search(r"\$(?:ARGUMENTS\b|[1-9])", literal_checked)
    ):
        msg = "native-arguments-unavailable: positional or $ARGUMENTS substitution is not supported."
        raise PromptBlockerError(msg)


def literal_shell_text(text: str) -> str:
    """Mask proven recipe-local references for validation, never for generated output."""
    return re.sub(
        r"(?m)^[ \t]*(`{3,})(?:bash|sh|shell)[ \t]*\n(.*?)^[ \t]*`{3,}[ \t]*$",
        mask_shell_recipe,
        text,
        flags=re.DOTALL,
    )


def shell_tokens(line: str) -> list[str] | None:
    """Tokenize recipe lines without expanding or executing shell content."""
    lexer = shlex.shlex(line, posix=False, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def mask_shell_recipe(match: re.Match[str]) -> str:
    """Keep unknown placeholders visible while recognizing bounded shell syntax."""
    recipe = match[2]
    parsed = [shell_tokens(line) for line in recipe.splitlines()]
    if None in parsed:
        return match[0]
    tokens = [words for words in parsed if words is not None]
    if any("<<" in word for words in tokens for word in words):
        return match[0]
    assigned = set()
    for line, words in zip(recipe.splitlines(), tokens, strict=True):
        assignment = re.fullmatch(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if assignment and (len(words) == 1 or (re.fullmatch(r"\$\([^()]*\)", assignment[2]) is not None)):
            assigned.add(assignment[1])
        for index, word in enumerate(words):
            if word == "while" and (index == 0 or words[index - 1] in {"|", ";", "&&", "||"}):
                tail = words[index + 1 :]
                read = re.fullmatch(r"read (?:-r )?([A-Za-z_][A-Za-z0-9_]*)(?: ;.*)?", " ".join(tail))
                if read:
                    assigned.add(read[1])
    assigned = {name for name in assigned if not name.startswith("ARGUMENTS")}
    recipe = re.sub(
        r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
        lambda variable: "literal" if (variable[1] or variable[2]) in assigned else variable[0],
        recipe,
    )
    return "".join(
        mask_awk_line(line, words) for line, words in zip(recipe.splitlines(keepends=True), tokens, strict=True)
    )


def mask_awk_line(line: str, words: list[str]) -> str:
    """Mask only one unambiguous single-quoted awk program on a recipe line."""
    for index, word in enumerate(words[:-1]):
        if word == "awk" and (index == 0 or words[index - 1] in {"|", ";", "&&", "||"}):
            program = words[index + 1]
            if program.startswith("'") and program.endswith("'") and line.count(program) == 1:
                line = line.replace(program, re.sub(r"\$[1-9](?![0-9])", "literal", program))
    return line


def command_file(path: Path, target: str, plugin: str, *, text: str | None = None) -> dict[str, bytes]:
    """Translate plain Markdown commands without execution semantics."""
    metadata, body = prompt_document(path, "command", text=text)
    metadata = {
        key: value for key, value in metadata.items() if key not in {"allowed-tools", "disable-model-invocation"}
    }
    parser = yaml_parser()
    text = "---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body
    command_syntax(text, target)
    return render_command(path, target, plugin, metadata, body)


def render_command(path: Path, target: str, plugin: str, metadata: dict, body: str) -> dict[str, bytes]:
    """Render a validated prompt body at the target's native entry point."""
    roots = {"opencode-v2": ".config/opencode/commands", "pi": ".pi/agent/prompts"}
    parser = yaml_parser()
    text = "---\n" + parser.safe_dump(metadata, sort_keys=False) + "---\n" + body
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
            reject_local_configuration(relative)
            content = resource.read_bytes()
            reject_sensitive(resource, content)
            name = str(destination / relative)
            if relative != Path("SKILL.md"):
                files[name] = content
            if resource.stat().st_mode & 0o111:
                executables.append(name)
    return files, executables


def convert_skill(path: Path, name: str, target: str = "pi", *, text: str | None = None) -> bytes:
    """Validate and namespace portable skill content in one pass."""
    metadata, body = prompt_document(path, "skill", text=text)
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
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        description = f"Run {name} explicitly."
    header = {"name": identifier, "description": description}
    return {
        f"{destination}/SKILL.md": ("---\n" + parser.safe_dump(header) + "---\n" + body).encode(),
        f"{destination}/agents/openai.yaml": b"policy:\n  allow_implicit_invocation: false\n",
    }


def skill_policy(path: Path, destination: Path, target: str, *, text: str | None = None) -> dict[str, bytes]:
    """Emit target sidecars for explicit-only source skills."""
    if target != "codex":
        return {}
    metadata, _body = prompt_document(path, "skill", text=text)
    if metadata.get("disable-model-invocation") is True:
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


def resource_text(content: bytes) -> str | None:
    """Leave opaque or NUL-bearing resources outside textual link relocation."""
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def sibling_resource_links(files: dict, owners: dict, inventory: list[dict], target: str) -> dict:
    """Resolve literal relative references against actual source skill resource trees."""
    skills = {item["name"]: item for item in inventory if item["kind"] == "skill"}
    references = {}
    pattern = re.compile(r"(?:(?<![A-Za-z0-9_./-])|(?<=\)/))(?:\.\./)+[A-Za-z0-9_-]+/[A-Za-z0-9_./+-]+")
    for name, content in files.items():
        owner = owners.get(name)
        if owner not in skills:
            continue
        item = skills[owner]
        destination = Path(skill_entry(item, target)).parent
        if not Path(name).is_relative_to(destination):
            continue
        relative = Path(name).relative_to(destination)
        source_file = Path(item["path"]).parent / relative
        text = resource_text(content)
        if text is None:
            continue
        for match in pattern.finditer(text):
            literal = match[0].rstrip(".")
            original = source_file.parent / literal
            if any(parent.is_symlink() for parent in (original, *original.parents)):
                msg = f"Source resource symlink requires review: {original}"
                raise ValueError(msg)
            resource = original.resolve()
            for dependency, sibling in skills.items():
                sibling_root = Path(sibling["path"]).parent.resolve()
                if dependency == owner or not resource.is_relative_to(sibling_root):
                    continue
                relocated = Path(skill_entry(sibling, target)).parent / resource.relative_to(sibling_root)
                replacement = posixpath.relpath(str(relocated), str(Path(name).parent))
                if literal.endswith("/"):
                    replacement += "/"
                references.setdefault(owner, []).append(
                    (
                        name,
                        match.start(),
                        match.start() + len(literal),
                        replacement,
                        dependency,
                        str(relocated),
                        match.end() == len(text) or text[match.end()] in " \t\r\n\"'`),;:*]}",
                    )
                )
    return references


def generated_resource(files: dict, owners: dict, path: str, owner: str) -> bool:
    """Require a concrete generated file or nonempty directory with source ownership."""
    return any(
        owners.get(name) == owner and (name == path or name.startswith(path.rstrip("/") + "/")) for name in files
    )


def rewrite_skill_links(
    files: dict[str, bytes],
    owners: dict[str, str],
    inventory: list[dict],
    target: str,
    selected: list[str] | None = None,
) -> list[dict]:
    """Rewrite source-owned sibling resources and isolate unavailable dependencies."""
    skills = {item["name"]: item for item in inventory if item["kind"] == "skill"}
    references = sibling_resource_links(files, owners, inventory, target)
    diagnostics = []
    while True:
        available = {
            name
            for name, item in skills.items()
            if owners.get(skill_entry(item, target)) == name and skill_entry(item, target) in files
        }
        unavailable = {
            name: {
                ref[4]
                for ref in refs
                if not ref[6] or ref[4] not in available or not generated_resource(files, owners, ref[5], ref[4])
            }
            for name, refs in references.items()
            if name in available
        }
        unavailable = {name: missing for name, missing in unavailable.items() if missing}
        if not unavailable:
            break
        for name, missing in unavailable.items():
            if selected and missing - set(selected):
                msg = "Missing skill dependencies: " + ", ".join(sorted(missing))
                raise ValueError(msg)
            diagnostics.append(
                {
                    "name": name,
                    "kind": "skill-dependency",
                    "status": "blocked",
                    "reason": "Selected skill has incompatible dependencies: " + ", ".join(sorted(missing)),
                }
            )
            for resource in list(files):
                if owners.get(resource) == name:
                    del files[resource]
    diagnostics.extend(relocate_sibling_resources(files, references))
    return diagnostics


def relocate_sibling_resources(files: dict, references: dict) -> list[dict]:
    """Replace original relative-reference spans once and persist generated resource edges."""
    diagnostics = []
    for owner, refs in references.items():
        active = [ref for ref in refs if ref[0] in files]
        for name in sorted({ref[0] for ref in active}):
            text = files[name].decode("utf-8")
            for _, start, end, replacement, _, _, _ in sorted(
                (ref for ref in active if ref[0] == name), key=lambda ref: ref[1], reverse=True
            ):
                text = text[:start] + replacement + text[end:]
            files[name] = text.encode()
        if active:
            dependencies = {
                path for ref in active for path in files if path == ref[5] or path.startswith(ref[5].rstrip("/") + "/")
            }
            diagnostics.append(
                {
                    "name": owner,
                    "kind": "skill-dependency",
                    "status": "unverified",
                    "reason": "Sibling resources relocated. Verify runtime behavior.",
                    "dependencies": sorted(dependencies),
                }
            )
    return diagnostics


def command_skill_links(
    files: dict[str, bytes],
    owners: dict[str, str],
    inventory: list[dict],
    target: str,
    selected: list[str] | None,
) -> list[dict]:
    """Resolve explicit command load directives to selected source-owned skills."""
    skills = {item["name"]: item for item in inventory if item["kind"] == "skill"}
    diagnostics = []
    for item in inventory:
        outputs = [path for path, owner in owners.items() if owner == item["name"] and path in files]
        if item["kind"] != "command" or not outputs:
            continue
        text, _documents = example_documents(Path(item["source"]), item, target)
        metadata, body = prompt_document(Path(item["path"]), "command", text=text)
        matches = load_skill_directives(body)
        local = [match for match in matches if match[1].split(":", 1)[0] == item["plugin"]]
        metadata = {
            key: value for key, value in metadata.items() if key not in {"allowed-tools", "disable-model-invocation"}
        }
        missing_selection = {
            match[1] for match in local if match[1] in skills and selected and match[1] not in selected
        }
        if missing_selection:
            msg = "Missing skill dependencies: " + ", ".join(sorted(missing_selection)) + ". Select them explicitly."
            raise ValueError(msg)
        replacements = {}
        resolved = set()
        unavailable = set()
        for match in local:
            dependency = match[1]
            skill = skills.get(dependency)
            native = (
                Path(SKILL_ROOTS[target]) / f"{skill['plugin']}-{Path(skill['path']).parent.name}" / "SKILL.md"
                if skill
                else None
            )
            output = str(Path(target) / "home" / native) if native else ""
            if not skill or not skill.get("available"):
                unavailable.add(dependency)
                continue
            resolved.add(output)
            replacements[match.start()] = (
                match.end(),
                f"Read and follow ~/{native}. Resolve ~ against target HOME, not project CWD, before reading",
            )
        if unavailable:
            diagnostics.append(
                {
                    **item,
                    "kind": "command-dependency",
                    "status": "blocked",
                    "reason": "Required skills unavailable: " + ", ".join(sorted(unavailable)),
                }
            )
            for path in outputs:
                files.pop(path, None)
            continue
        for start, (end, replacement) in sorted(replacements.items(), reverse=True):
            body = body[:start] + replacement + body[end:]
        if replacements:
            files.update(render_command(Path(item["path"]), target, item["plugin"], metadata, body))
            diagnostics.append(
                {
                    **item,
                    "kind": "command-dependency",
                    "status": "unverified",
                    "reason": "Required native skills prepared. Verify command behavior.",
                    "dependencies": sorted(resolved),
                }
            )
    return diagnostics


def load_skill_directives(body: str) -> list[re.Match[str]]:
    """Find exact load directives in prose, excluding Markdown fenced examples."""
    directive = re.compile(
        r"(?i)^\*{0,2}(?:FIRST:[ \t]*)?Load[ \t]+(?:the[ \t]+)?"
        r"\*{0,2}([a-z0-9-]+:[a-z0-9-]+)\*{0,2}[ \t]+skill\b(?:[ \t]+first)?\*{0,2}"
        r"(?:[ \t]+using the Skill tool)?"
    )
    matches = []
    fence = None
    offset = 0
    for line in body.splitlines(keepends=True):
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end() :].strip():
                fence = None
        elif fence is None:
            match = directive.match(line)
            if match:
                # Match offsets are measured in the complete body for safe span replacement.
                complete = re.compile(directive.pattern, directive.flags | re.MULTILINE).match(body, offset)
                if complete:
                    matches.append(complete)
        offset += len(line)
    return matches


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
    blocked_names = {
        item["name"]
        for item in components
        if item.get("kind") in {"skill-dependency", "command-dependency"} and item.get("status") == "blocked"
    }
    components = [
        item
        for item in components
        if not (item["kind"] in {"skill", "command"} and item["name"] in blocked_names)
        and not (item["kind"] == "runtime-dependency" and item.get("path") not in active)
    ]
    return owners, executables, components


def valid_skill_name(name: str) -> bool:
    """Accept relocatable target skill identifiers without truncating collisions."""
    return len(name) <= MAX_SKILL_NAME_LENGTH and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None
