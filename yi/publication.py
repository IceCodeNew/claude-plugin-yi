"""Publish one prepared artifact operation with rollback for caught I/O failures."""

import json
import shutil
import tempfile
from pathlib import Path


def publish(root: Path, outputs: dict[str, bytes], removed: set[str], executables: set[str]) -> bool:
    """Stage complete files and backups before replacing any live artifact."""
    changed = {
        name: content
        for name, content in outputs.items()
        if not unchanged(root / name, content, 0o755 if name in executables else 0o644)
    }
    if not changed and not removed:
        return False
    staging = Path(tempfile.mkdtemp(prefix=".yi-publish-", dir=root))
    retain = False
    try:
        backups, prepared = stage_files(root, staging, changed, removed, executables)
        published = []
        try:
            for name in sorted(prepared, key=lambda value: (value.startswith("manifests/"), value)):
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                prepared[name].replace(destination)
                published.append(name)
            for name in sorted(removed):
                (root / name).unlink()
                published.append(name)
        except OSError as error:
            failed = rollback(root, backups, published)
            if failed:
                retain = True
                msg = f"{error}. Rollback incomplete for {', '.join(failed)}. Recovery files retained in {staging}."
                raise OSError(msg) from error
            raise
    finally:
        if not retain:
            shutil.rmtree(staging)
    return True


def stage_files(
    root: Path, staging: Path, changed: dict[str, bytes], removed: set[str], executables: set[str]
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Complete new files and an indexed backup before live publication."""
    backups = {}
    prepared = {}
    for index, name in enumerate(sorted(changed.keys() | removed)):
        destination = root / name
        if destination.exists():
            backup = staging / f"old-{index}"
            shutil.copy2(destination, backup)
            backups[name] = backup
        if name in changed:
            prepared[name] = staging / f"new-{index}"
            prepared[name].write_bytes(changed[name])
            prepared[name].chmod(0o755 if name in executables else 0o644)
    recovery = {name: backups[name].name if name in backups else None for name in sorted(changed.keys() | removed)}
    (staging / "recovery.json").write_text(json.dumps(recovery, indent=2) + "\n", encoding="utf-8")
    return backups, prepared


def rollback(root: Path, backups: dict[str, Path], published: list[str]) -> list[str]:
    """Restore every possible live path and identify failures without discarding backups."""
    failed = []
    for name in reversed(published):
        try:
            if name in backups:
                backups[name].replace(root / name)
            else:
                (root / name).unlink()
        except OSError:
            failed.append(name)
    return failed


def unchanged(path: Path, content: bytes, mode: int) -> bool:
    """Skip files whose bytes and final permissions already match the plan."""
    return path.is_file() and path.read_bytes() == content and path.stat().st_mode & 0o777 == mode
