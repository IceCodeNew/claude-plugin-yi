"""Local output and explicit target model configuration."""

import json
from pathlib import Path


def configure(
    directory: Path, output: Path | None = None, *, target: str | None = None, model_maps: list[str] | None = None
) -> dict:
    """Read configuration or persist explicit user selections."""
    path = directory / "config.json"
    values: dict = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"output_root": str(directory / "artifacts")}
    )
    validate(values)
    if model_maps:
        if target is None:
            msg = "Model mapping requires --target."
            raise ValueError(msg)
        mappings = dict(values.get("model_mappings", {}).get(target, {}))
        for assignment in model_maps:
            alias, separator, model = assignment.partition("=")
            if not separator or not alias.strip() or not model.strip():
                msg = "Model mapping must use SOURCE_ALIAS=TARGET_MODEL."
                raise ValueError(msg)
            mappings[alias.strip()] = model.strip()
        values["model_mappings"] = {**values.get("model_mappings", {}), target: mappings}
    if output is not None:
        values["output_root"] = str(output.expanduser().absolute())
    if output is not None or model_maps:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(values) + "\n", encoding="utf-8")
    return values


def validate(values: object) -> None:
    """Validate saved configuration before consumers use nested fields."""
    if not isinstance(values, dict) or not isinstance(values.get("output_root"), str):
        msg = "Configuration requires an output_root string."
        raise TypeError(msg)
    mappings = values.get("model_mappings", {})
    if not isinstance(mappings, dict):
        msg = "Configuration model_mappings must be an object."
        raise TypeError(msg)
    for target, entries in mappings.items():
        if not isinstance(target, str) or not isinstance(entries, dict):
            msg = "Each target model mapping must be an object."
            raise TypeError(msg)
        if not all(
            isinstance(alias, str) and alias.strip() and isinstance(model, str) and model.strip()
            for alias, model in entries.items()
        ):
            msg = "Model aliases and target IDs must be nonempty strings."
            raise ValueError(msg)
