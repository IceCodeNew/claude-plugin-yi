# yi

Local Claude Code invocation statistics and migration to Amp Code, Codex, OpenCode v2, and Pi.

## Commands

- `/yi:usage`: show invocation counts in descending order.
- `/yi:migrate`: select components and targets, then inspect migration artifacts in a Git-managed isolated HOME.

The counter includes manual and automatic invocations. Migration reports identify components that require further work in the target harness.

## Local use

Load this checkout with Claude Code:

```sh
claude --plugin-dir /absolute/path/to/claude-plugin-yi
```

The collector stores counts under `$XDG_DATA_HOME/yi`, or `~/.local/share/yi` when that variable is unset. It records component names and execution identities, not prompt arguments. Historical import is explicit and supports identifiable command expansions and successful skill receipts. Built-in commands without observable events are not counted.

## Migration artifacts

Choose an output root in `/yi:migrate`. yi initializes one Git repository there and places each target HOME in a separate directory. Each plugin-target unit has its own commit. Git uses the local user's identity and signing configuration. Resolve identity or signing errors before migration; yi does not create signing keys.

Inspect generated files and component diagnostics before installation. Configuration isolation does not provide an operating-system sandbox. Do not execute untrusted scripts or MCP servers merely to check copied files.

The converter prepares portable skill directories and plain Markdown commands for supported destinations. Claude-specific metadata, hooks, agents, MCP declarations, and unknown capabilities can require target-harness assistance. A blocked or unverified report is not a complete migration. Native discovery and behavioral verification remain distinct from file integrity checks.

## Development

Use Python 3.11 or later and uv.

```sh
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
prek run --all-files
```
