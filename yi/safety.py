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


def require_object(value: object, label: str) -> dict:
    """Reject malformed external object containers before lookup."""
    if not isinstance(value, dict):
        msg = f"{label} must be an object."
        raise TypeError(msg)
    return value
