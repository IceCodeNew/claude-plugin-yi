"""Lazy frontmatter parsing shared by migration capabilities."""

import importlib


def read_yaml(text: str) -> object:
    """Load the migration-only parser without affecting hook startup."""
    try:
        parser = importlib.import_module("yaml")
    except ModuleNotFoundError as error:
        msg = "Migration requires PyYAML. Run the helper with uv run --with pyyaml."
        raise ValueError(msg) from error
    try:
        return parser.safe_load(text)
    except parser.YAMLError:
        return None
