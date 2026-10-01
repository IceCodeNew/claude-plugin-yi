"""Sensitive content checks shared by migration inputs."""

import re
from pathlib import Path


def reject_sensitive(path: Path, content: bytes) -> None:
    """Reject known credential resources before creating exportable bytes."""
    names = {"credentials.json", "auth.json", "auth.jsonc", ".netrc", ".pypirc", "id_rsa", "id_ed25519"}
    secret_header = re.search(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", content)
    if path.name.lower() in names or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or secret_header:
        msg = f"Sensitive resource requires removal or explicit redaction: {path.name}"
        raise ValueError(msg)


def reject_local_configuration(path: Path) -> None:
    """Reject local state and configuration paths without inspecting their values."""
    if any(
        part.lower() in {".git", ".aws", ".ssh", ".npmrc", ".pypirc", ".netrc"} or part.lower().startswith(".env")
        for part in path.parts
    ) or (".claude" in path.parts and (path.name.startswith("settings") or "credential" in path.name)):
        msg = f"Sensitive local configuration requires removal or redaction: {path}"
        raise ValueError(msg)


def require_object(value: object, label: str) -> dict:
    """Reject malformed external object containers before lookup."""
    if not isinstance(value, dict):
        msg = f"{label} must be an object."
        raise TypeError(msg)
    return value
