# yi

Local Claude Code invocation statistics and migration to Amp Code, Codex, OpenCode v2, and Pi.

## Commands

- `/yi:usage`: show invocation counts in descending order.
- `/yi:migrate`: select components and targets, then inspect migration artifacts in a Git-managed isolated HOME.

The counter includes manual and automatic invocations. Migration reports identify components that require further work in the target harness.

## Local use

The plugin runtime requires Python 3.11 or later. Load this checkout with Claude Code:

```sh
claude --plugin-dir /absolute/path/to/claude-plugin-yi
```

The collector stores counts under `$XDG_DATA_HOME/yi`, or `~/.local/share/yi` when that variable is unset. It records component names and execution identities, not prompt arguments. Historical import is explicit and supports identifiable command expansions and successful skill receipts. Built-in commands without observable events are not counted.

## Migration artifacts

Choose an output root in `/yi:migrate`. yi initializes one Git repository there and places each target HOME in a separate directory. Generation does not stage files, create commits, sign, or push. Inspect the diff and manage commits with normal Git tools; identity and signing setup do not block file generation.

Inspect generated files and component diagnostics before installation. Configuration isolation does not provide an operating-system sandbox. Do not execute untrusted scripts or MCP servers merely to check copied files.

Select plugin components, standalone skill directories, or standalone Markdown commands. Portable skills retain their resources. OpenCode v2 and Pi accept Markdown command/template conversions. Codex receives explicit-only skills invoked as `$<name>`; removed custom-prompt paths are not generated. OpenCode and Pi templates retain `$ARGUMENTS` and positional `$1` through `$9`. Codex parameter substitution requires adaptation and is reported as blocked. Amp receives command-palette modules; `$ARGUMENTS` uses a human input dialog, while positional arguments remain blocked. Codex and OpenCode v2 accept supported agent definitions and disabled MCP declarations. Codex command-hook declarations remain untrusted and require protocol review before use. Unsupported fields and target capabilities receive per-component blockers. Configure explicit agent model mappings in the migration flow. Unmapped source model requirements remain blocked.

Use the migration flow's native check to discover generated skills with the installed target CLI. Checks use disposable configuration roots and do not execute migrated hooks, extensions, or MCP servers. Amp authentication requires explicit approval. Native discovery does not verify behavior; a blocked or unverified report is not a complete migration.

Migration requires PyYAML. When it is absent, run the helper through `uv run --no-project --with 'pyyaml>=6,<7' python`. The collector and usage display use Python's standard library. Development dependency resolution uses the index declared in `pyproject.toml`.

## Upstream native packages

Use `--native-package` to stage an existing target-native package without activation. yi preserves its relative layout outside discovery roots. Register it only after review. Do not activate both its original skills and duplicate converted skills. Preserve license notices and keep credentials outside artifacts.

## Development

Use Python 3.11 or later and uv.

```sh
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
prek run --all-files
```
