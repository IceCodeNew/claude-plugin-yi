"""Command-line interface for yi."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from yi import adapters, artifacts, catalog, checks, config, history, install, usage


def parse_args() -> argparse.Namespace:
    """Parse private helper operations."""
    parser = argparse.ArgumentParser(description="Local invocation counts and harness migration.")
    default_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "yi"
    parser.add_argument("--data-dir", type=Path, default=default_data)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("record", help="Record one hook event from standard input.")
    collector.add_argument("--hook", action="store_true", help="Report collector failures without blocking Claude.")
    stats = commands.add_parser("usage", help="Show invocation counts in descending order.")
    stats.add_argument("--group", choices=("component", "plugin"), default="component")
    stats.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    importer = commands.add_parser("history", help="Import explicitly selected local session files.")
    importer.add_argument("--from", dest="source", type=Path, required=True)
    importer.add_argument("--json", action="store_true")
    sources = commands.add_parser("catalog", help="Inspect local source components without execution.")
    source_choice = sources.add_mutually_exclusive_group(required=True)
    source_choice.add_argument("--source", type=Path)
    source_choice.add_argument("--claude-dir", type=Path)
    sources.add_argument("--json", action="store_true")
    migration = commands.add_parser("migrate", help="Prepare isolated migration artifacts.")
    migration.add_argument("--source", type=Path, action="append", required=True)
    migration.add_argument("--target", choices=tuple(adapters.SKILL_ROOTS), action="append", required=True)
    migration.add_argument("--item", action="append", help="Select a complete component ID; repeat for multiple items.")
    migration.add_argument("--output", type=Path)
    migration.add_argument("--dry-run", action="store_true")
    migration.add_argument("--json", action="store_true")
    settings = commands.add_parser("config", help="Read or set the private artifact-root configuration.")
    settings.add_argument("--output", type=Path)
    settings.add_argument("--json", action="store_true")
    checker = commands.add_parser("check", help="Inspect generated artifact integrity without execution.")
    checker.add_argument("--output", type=Path, required=True)
    checker.add_argument("--json", action="store_true")
    installer = commands.add_parser("install", help="Preview or explicitly install generated artifacts.")
    installer.add_argument("--output", type=Path, required=True)
    installer.add_argument("--target", choices=tuple(adapters.SKILL_ROOTS), required=True)
    installer.add_argument("--destination", type=Path, required=True)
    installer.add_argument("--apply", action="store_true")
    installer.add_argument("--accept-unverified", action="store_true")
    installer.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Run the requested local operation."""
    args = parse_args()
    if args.command == "record":
        return collect(args)
    if args.command == "migrate":
        run_migration(args)
    elif args.command == "install":
        result = install.install(
            args.output, args.target, args.destination, apply=args.apply, accept_unverified=args.accept_unverified
        )
        sys.stdout.write(json.dumps(result) + "\n")
    elif args.command == "check":
        sys.stdout.write(json.dumps(checks.inspect(args.output)) + "\n")
    elif args.command == "config":
        sys.stdout.write(json.dumps(config.configure(args.data_dir, args.output)) + "\n")
    elif args.command == "catalog":
        sys.stdout.write(
            json.dumps({"items": catalog.discover(args.source) if args.source else catalog.installed(args.claude_dir)})
            + "\n"
        )
    elif args.command == "history":
        sys.stdout.write(json.dumps(history.import_history(args.data_dir, args.source)) + "\n")
    else:
        items = usage.ranked(args.data_dir, args.group)
        if args.json:
            sys.stdout.write(json.dumps({"items": items}) + "\n")
        else:
            for item in items:
                sys.stdout.write(f"{item['count']:>8}  {item['name']}\n")
    return 0


def run_migration(args: argparse.Namespace) -> None:
    """Prepare all selected units before changing artifact files."""
    output = args.output or Path(config.configure(args.data_dir)["output_root"])
    selections = selections_by_source(args.source, args.item)
    plans = [
        adapters.preview(source, target, selected)
        for source, selected in selections
        for target in dict.fromkeys(args.target)
    ]
    for report, files in plans:
        if not args.dry_run:
            report["committed"] = artifacts.apply(output, report, files)
    result = plans[0][0] if len(plans) == 1 else {"plans": [report for report, _ in plans]}
    sys.stdout.write(json.dumps(result) + "\n")


def collect(args: argparse.Namespace) -> int:
    """Keep collection failures nonblocking in hook mode."""
    try:
        recorded = usage.record(args.data_dir, json.load(sys.stdin))
    except (ValueError, TypeError, OSError, sqlite3.Error):
        sys.stderr.write("yi: collection failed; check event identity and local database access.\n")
        return 0 if args.hook else 1
    if not args.hook:
        sys.stdout.write(json.dumps({"recorded": recorded}) + "\n")
    return 0


def selections_by_source(sources: list[Path], selected: list[str] | None) -> list[tuple[Path, list[str] | None]]:
    """Validate global selection once, then partition it by source."""
    sources = list(dict.fromkeys(source.resolve() for source in sources))
    plugin_names = [
        json.loads((source / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["name"] for source in sources
    ]
    if len(plugin_names) != len(set(plugin_names)):
        msg = "Ambiguous plugin installations; select one source for each plugin name."
        raise ValueError(msg)
    if not selected:
        return [(source, None) for source in sources]
    inventories = [(source, {item["name"] for item in catalog.discover(source)}) for source in sources]
    available = set().union(*(names for _, names in inventories))
    missing = set(selected) - available
    if missing:
        msg = "Unknown selected components: " + ", ".join(sorted(missing))
        raise ValueError(msg)
    return [
        (source, [name for name in selected if name in names])
        for source, names in inventories
        if names.intersection(selected)
    ]
