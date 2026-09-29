"""Local configuration for the managed artifact directory."""

import json
from pathlib import Path


def configure(directory: Path, output: Path | None = None) -> dict[str, str]:
    """Read configuration or persist an explicitly selected output root."""
    path = directory / "config.json"
    if output is not None:
        values = {"output_root": str(output.expanduser().absolute())}
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(values) + "\n", encoding="utf-8")
        return values
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"output_root": str(directory / "artifacts")}
