"""Write owned artifacts into one Git repository."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from yi import shared_config


def git(root: Path, *arguments: str) -> str:
    """Run Git without a shell and retain actionable failures."""
    executable = shutil.which("git")
    if executable is None:
        msg = "Git is required to manage migration artifacts."
        raise ValueError(msg)
    result = subprocess.run(  # noqa: S603 - Git arguments are separate tokens; no shell is used.
        [executable, "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def apply(root: Path, report: dict, files: dict[str, bytes]) -> bool:
    """Generate one source-target unit without staging or committing user files."""
    root = root.expanduser().absolute()
    fresh = prepare_repository(root)
    manifest_path = f"manifests/{report['target']}-{report['plugin']}.json"
    validate_paths(root, [manifest_path])
    previous = root / manifest_path
    prior = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {}
    if prior.get("reviewed_changes"):
        prior["reviewed_files"] = sorted(
            set(prior.get("reviewed_files", [])) | (set(prior.get("hashes", {})) - shared_config.SHARED)
        )
    owned = prior.get("hashes", {})
    verify_owned(root, owned, set(prior.get("executables", [])))
    files, shared_metadata = shared_config.prepare(root, report, files)
    validate_new_files(root, {name: value for name, value in files.items() if name not in shared_config.SHARED}, owned)
    prior = {
        **prior,
        "hashes": {name: value for name, value in prior.get("hashes", {}).items() if name not in shared_config.SHARED},
        "owners": {name: value for name, value in prior.get("owners", {}).items() if name not in shared_config.SHARED},
    }
    own_files = {name: content for name, content in files.items() if name not in shared_config.SHARED}
    own_report = {
        **report,
        "owners": {name: value for name, value in report.get("owners", {}).items() if name not in shared_config.SHARED},
    }
    payload = merge_manifest(prior, own_report, own_files)
    removed = set(owned) - set(payload["hashes"]) - shared_config.SHARED
    executable = set(payload["executables"])
    protect_reviewed(prior, files, removed, executable)
    outputs = {
        **shared_metadata,
        **files,
        manifest_path: (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
    }
    if fresh:
        outputs[".yi-artifacts.json"] = b'{"owner":"yi","schema":1}\n'
        outputs[".gitignore"] = b"**/.cache/\n**/auth.json\n**/auth.jsonc\n**/credentials.json\n**/.env\n**/*.log\n"
    validate_paths(root, list(outputs))
    changed = bool(removed)
    for relative, content in outputs.items():
        destination = root / relative
        mode = 0o755 if relative in executable else 0o644
        if destination.is_file() and destination.read_bytes() == content and destination.stat().st_mode & 0o777 == mode:
            continue
        changed = True
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(mode)
    for relative in removed:
        (root / relative).unlink()
    return changed


def prepare_repository(root: Path) -> bool:
    """Validate ownership and Git state before artifact writes."""
    if root.is_symlink():
        msg = "Artifact root must not be a symlink."
        raise ValueError(msg)
    if root.exists() and not (root / ".git").is_dir() and any(root.iterdir()):
        msg = "Artifact root must be empty or an existing yi repository."
        raise ValueError(msg)
    root.mkdir(parents=True, exist_ok=True)
    fresh = not (root / ".git").exists()
    if fresh:
        git(root, "init", "--initial-branch=main")
    elif not (root / ".yi-artifacts.json").is_file():
        msg = "Existing repository is not owned by yi."
        raise ValueError(msg)
    return fresh


def verify_owned(root: Path, hashes: dict[str, str], executable: set[str]) -> None:
    """Protect user edits even when those edits are already committed."""
    for relative, expected in hashes.items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            msg = f"Owned artifact escapes root: {relative}"
            raise ValueError(msg)
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            or bool(path.stat().st_mode & 0o111) != (relative in executable)
        ):
            msg = f"Owned artifact was modified: {relative}. Review it before regeneration."
            raise ValueError(msg)


def validate_paths(root: Path, paths: list[str]) -> None:
    """Validate every output, including metadata, before any write."""
    for relative in paths:
        path = root / relative
        for parent in path.parents:
            if parent == root:
                break
            if parent.exists() and not parent.is_dir():
                msg = f"Artifact parent is not a directory: {parent}"
                raise ValueError(msg)
        if not path.resolve().is_relative_to(root.resolve()) or any(
            parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)
        ):
            msg = f"Artifact path escapes root or follows a symlink: {relative}"
            raise ValueError(msg)


def validate_new_files(root: Path, files: dict[str, bytes], owned: dict[str, str]) -> None:
    """Reject collisions before replacing owned artifact files."""
    for relative in files:
        destination = root / relative
        if destination.exists() and relative not in owned:
            msg = f"Artifact path is not owned by this migration: {relative}"
            raise ValueError(msg)


def merge_manifest(prior: dict, report: dict, files: dict[str, bytes]) -> dict:
    """Replace selected ownership sets and derive the cumulative inventory."""
    selected = set(report.get("selection") or [])
    prior_owners = prior.get("owners", {})
    if selected and set(prior.get("hashes", {})) - set(prior_owners):
        msg = "Existing manifest lacks component ownership; regenerate the whole plugin."
        raise ValueError(msg)
    retained = {name for name, owner in prior_owners.items() if selected and owner not in selected}
    hashes = {name: digest for name, digest in prior.get("hashes", {}).items() if name in retained}
    hashes.update({name: hashlib.sha256(content).hexdigest() for name, content in files.items()})
    owners = {name: owner for name, owner in prior_owners.items() if name in retained}
    owners.update(report.get("owners", {}))
    executable = set(prior.get("executables", [])) & retained | set(report.get("executables", []))
    components = [item for item in prior.get("components", []) if selected and item.get("name") not in selected]
    components.extend(report.get("components", []))
    return {
        "plugin": report["plugin"],
        "target": report["target"],
        "hashes": hashes,
        "files": sorted(hashes),
        "owners": owners,
        "executables": sorted(executable),
        "components": components,
        "reviewed_files": prior.get("reviewed_files", []),
        "configuration": report.get("configuration", {}),
        "complete": False,
    }


def protect_reviewed(prior: dict, files: dict[str, bytes], removed: set[str], executable: set[str]) -> None:
    """Keep accepted human edits installable without making them generator-owned."""
    for name in prior.get("reviewed_files", []):
        if name in removed or (
            name in files
            and (
                hashlib.sha256(files[name]).hexdigest() != prior["hashes"][name]
                or (name in executable) != (name in prior.get("executables", []))
            )
        ):
            msg = f"Artifact contains reviewed changes: {name}. Reconcile the source before regeneration."
            raise ValueError(msg)
