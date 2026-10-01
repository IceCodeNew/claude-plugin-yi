"""Preview and explicitly install owned files without starting services."""

import hashlib
from pathlib import Path

from yi.artifacts import validate_paths
from yi.manifest import read_manifest


def install(root: Path, target: str, destination: Path, *, apply: bool, accept_unverified: bool) -> dict:
    """Copy intact artifacts only after checking every destination conflict."""
    root = root.resolve()
    destination = destination.expanduser().absolute()
    validate_paths(destination, ["."])
    prefix = Path(target) / "home"
    files = {}
    executable = set()
    unresolved = []
    for manifest in sorted((root / "manifests").glob(f"{target}-*.json")):
        data = read_manifest(manifest)
        unresolved.extend(item for item in data["components"] if item["status"] != "converted")
        for relative, expected in data["hashes"].items():
            source = root / relative
            local = Path(relative).relative_to(prefix)
            output = destination / local
            validate_paths(destination, [str(local)])
            validate_paths(root, [relative])
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected or bool(source.stat().st_mode & 0o111) != (
                relative in data.get("executables", [])
            ):
                msg = f"Artifact changed since generation: {relative}"
                raise ValueError(msg)
            if output.exists() and output.read_bytes() != content:
                msg = f"Installation would overwrite a different file: {output}"
                raise ValueError(msg)
            files[output] = content
            if relative in data.get("executables", []):
                executable.add(output)
    validate_existing_modes(executable)
    require_files(files, target)
    if apply and unresolved and not accept_unverified:
        msg = "Unresolved components require explicit partial-install acceptance."
        raise ValueError(msg)
    if apply:
        write_new_files(files, executable)
    return {"applied": apply, "files": [str(path) for path in files], "unresolved": unresolved}


def require_files(files: dict[Path, bytes], target: str) -> None:
    """Reject an empty installation before reporting application."""
    if not files:
        msg = f"No installable artifacts found for {target}."
        raise ValueError(msg)


def write_new_files(files: dict[Path, bytes], executable: set[Path]) -> None:
    """Create new files without changing identical existing destinations."""
    created = []
    try:
        for output, content in files.items():
            if output.exists():
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                created.append(output)
                stream.write(content)
            output.chmod(0o755 if output in executable else 0o644)
    except OSError as error:
        remaining = []
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                remaining.append(str(path))
        if remaining:
            msg = f"{error}. Incomplete new installation files require cleanup: {', '.join(remaining)}"
            raise OSError(msg) from error
        raise


def validate_existing_modes(executable: set[Path]) -> None:
    """Report executable conflicts without changing destination permissions."""
    for path in executable:
        if path.exists() and not path.stat().st_mode & 0o111:
            msg = f"Existing destination lacks executable permission: {path}. Resolve the conflict before installation."
            raise ValueError(msg)
