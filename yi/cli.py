"""Command-line interface for yi."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from yi import usage


def parse_args() -> argparse.Namespace:
    """Parse private helper operations."""
    parser = argparse.ArgumentParser(description="Local invocation counts and harness migration.")
    xdg = Path(os.environ.get("XDG_DATA_HOME", ""))
    default_data = (xdg if xdg.is_absolute() else Path.home() / ".local/share") / "yi"
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
    return parser.parse_args()


def main() -> int:
    """Run the requested local operation."""
    args = parse_args()
    try:
        return dispatch(args)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        report = {
            "status": "failed",
            "stage": "preview" if args.command == "migrate" else args.command,
            "error": str(error),
        }
        sys.stdout.write(json.dumps(report) + "\n")
        return 1


def dispatch(args: argparse.Namespace) -> int:
    """Run one operation after argument parsing."""
    if args.command == "record":
        return collect(args)
    if args.command == "catalog":
        from yi import catalog  # noqa: PLC0415 - Collection must not load source discovery.

        items = catalog.discover(args.source) if args.source else catalog.installed(args.claude_dir)
        counts = {item["name"]: item["count"] for item in usage.ranked(args.data_dir, "component")}
        plugins = {item["name"]: item["count"] for item in usage.ranked(args.data_dir, "plugin")}
        ranked_items = catalog.rank(items, counts, plugins)
        sys.stdout.write(json.dumps({"items": ranked_items}) + "\n")
    elif args.command == "history":
        from yi import history  # noqa: PLC0415 - Collection does not need transcript import.

        sys.stdout.write(json.dumps(history.import_history(args.data_dir, args.source)) + "\n")
    else:
        items = usage.ranked(args.data_dir, args.group)
        if args.json:
            sys.stdout.write(json.dumps({"items": items}) + "\n")
        else:
            for item in items:
                sys.stdout.write(f"{item['count']:>8}  {item['name']}\n")
    return 0


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
