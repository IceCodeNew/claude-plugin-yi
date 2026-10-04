"""Collect inert, source-relative hook assets without loading source code."""

import json
import os
import stat
from pathlib import Path

from yi.safety import reject_local_configuration, reject_sensitive

_RUNTIME_EXCLUDED = {
    ".cache",
    "cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    "venv",
    "logs",
    "log",
    "coverage",
    "htmlcov",
    ".nyc_output",
    ".coverage",
}
_LOCAL_SETTINGS = {"settings.local.json", "settings.local.jsonc", "local.settings.json", ".envrc"}


def collect_hook_runtime(source: Path, destination: Path) -> tuple[dict[str, bytes], list[str]]:
    """Return sanitized package bytes and executable output paths, without writing.

    Preserve the full relative tree, including sibling imports and licenses.
    Skip dependency, cache, and local runtime trees; reject links, special files,
    credentials and local settings before reading any retained resource. The
    executable list uses the same destination-prefixed paths as the byte map.
    """
    # Import at the call boundary: native staging owns this exclusion policy.
    from yi.native_package import EXCLUDED, reject_mcp_credentials, validate_declared_mcp  # noqa: PLC0415

    source = source.absolute()
    if any(path.is_symlink() for path in (source, *source.parents)) or not source.is_dir():
        msg = "Hook runtime source must be a directory without symlinks."
        raise ValueError(msg)
    retained = _retained_resources(source, EXCLUDED | _RUNTIME_EXCLUDED)
    validate_declared_mcp(source)
    files = {}
    executables = []
    for path, relative, mode in retained:
        content = path.read_bytes()
        reject_sensitive(relative, content)
        if path.name == ".mcp.json":
            reject_mcp_credentials(json.loads(content))
        output = str(destination / relative)
        files[output] = content
        if mode & 0o111:
            executables.append(output)
    return files, executables


def _retained_resources(source: Path, excluded: set[str]) -> list[tuple[Path, Path, int]]:
    """Validate the inventory before reading any retained resource bytes."""
    retained = []
    for directory, folders, names in os.walk(source, followlinks=False, onerror=_reject_walk_error):
        parent = Path(directory)
        for name in sorted(folders + names):
            path = parent / name
            relative = path.relative_to(source)
            if _excluded(relative, excluded):
                if name in folders:
                    folders.remove(name)
                continue
            reject_local_configuration(relative)
            reject_sensitive(relative, b"")
            if path.name.lower() in _LOCAL_SETTINGS:
                msg = f"Local settings require removal before hook migration: {relative}"
                raise ValueError(msg)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                msg = f"Hook runtime contains a symlink: {relative}"
                raise ValueError(msg)
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                msg = f"Hook runtime resource is not a regular file: {relative}"
                raise ValueError(msg)
            retained.append((path, relative, mode))
    return sorted(retained)


def _reject_walk_error(error: OSError) -> None:
    """Fail closed when a complete sanitized inventory cannot be established."""
    msg = "Hook runtime source tree could not be inspected completely."
    raise ValueError(msg) from error


def _excluded(relative: Path, excluded: set[str]) -> bool:
    """Skip local runtime trees without traversing or reading their contents."""
    lowered = {part.lower() for part in relative.parts}
    return bool(
        excluded.intersection(lowered)
        or relative.suffix.lower() in {".pyc", ".pyo", ".log"}
        or relative.name.lower().startswith(".coverage.")
        or (".claude" in lowered and "hooks" in lowered)
    )
