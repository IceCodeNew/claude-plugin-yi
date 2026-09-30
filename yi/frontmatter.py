"""Lazy frontmatter parsing shared by migration capabilities."""

import importlib
from types import ModuleType


def yaml_parser() -> ModuleType:
    """Load the optional parser or report its installation requirement."""
    try:
        parser = importlib.import_module("yaml")
    except ModuleNotFoundError as error:
        msg = "Migration requires PyYAML. Run the helper with uv run --with pyyaml."
        raise ValueError(msg) from error
    return parser


def read_yaml(text: str) -> object:
    """Parse migration frontmatter without affecting collector startup."""
    parser = yaml_parser()
    try:
        return parser.safe_load(text)
    except parser.YAMLError:
        return None
