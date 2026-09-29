"""Command-line interface for yi."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from yi import config, usage
from yi.targets import SKILL_ROOTS


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
    migration = commands.add_parser("migrate", help="Prepare isolated migration artifacts.")
    migration.add_argument("--source", type=Path, action="append", required=True)
    migration.add_argument("--target", choices=tuple(SKILL_ROOTS), action="append", required=True)
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
    checker.add_argument(
        "--accept-changes", action="store_true", help="Accept explicitly reviewed edits to tracked resources."
    )
    checker.add_argument("--native", action="store_true", help="Run an explicit isolated native discovery check.")
    checker.add_argument("--target", choices=tuple(SKILL_ROOTS))
    checker.add_argument(
        "--allow-auth", action="store_true", help="Allow Amp to use existing authentication for discovery."
    )
    checker.add_argument("--executable", help="Use a specific native CLI executable.")
    installer = commands.add_parser("install", help="Preview or explicitly install generated artifacts.")
    installer.add_argument("--output", type=Path, required=True)
    installer.add_argument("--target", choices=tuple(SKILL_ROOTS), required=True)
    installer.add_argument("--destination", type=Path, required=True)
    installer.add_argument("--apply", action="store_true")
    installer.add_argument("--accept-unverified", action="store_true")
    installer.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Run the requested local operation."""
    args = parse_args()
    try:
        return dispatch(args)
    except (ValueError, OSError) as error:
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
    if args.command == "migrate":
        return run_migration(args)
    if args.command == "install":
        from yi import install  # noqa: PLC0415 - Load optional capability only when selected.

        result = install.install(
            args.output, args.target, args.destination, apply=args.apply, accept_unverified=args.accept_unverified
        )
        sys.stdout.write(json.dumps(result) + "\n")
    elif args.command == "check":
        run_check(args)
    elif args.command == "config":
        sys.stdout.write(json.dumps(config.configure(args.data_dir, args.output)) + "\n")
    elif args.command == "catalog":
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


def run_migration(args: argparse.Namespace) -> int:
    """Prepare all selected units before changing artifact files."""
    from yi import adapters, artifacts  # noqa: PLC0415 - Isolate migration startup from hooks.

    output = args.output or Path(config.configure(args.data_dir)["output_root"])
    selections = selections_by_source(args.source, args.item)
    plans = [
        adapters.preview(source, target, selected)
        for source, selected in selections
        for target in dict.fromkeys(args.target)
    ]
    failed = False
    for report, files in plans:
        if failed:
            report["status"] = "not-attempted"
        elif not args.dry_run:
            try:
                report["changed"] = artifacts.apply(output, report, files)
            except (ValueError, OSError) as error:
                report["status"] = "failed"
                report["error"] = str(error)
                failed = True
    result = plans[0][0] if len(plans) == 1 else {"plans": [report for report, _ in plans]}
    sys.stdout.write(json.dumps(result) + "\n")
    return int(failed)


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
    from yi import catalog  # noqa: PLC0415 - Source discovery belongs to migration.

    sources = list(dict.fromkeys(catalog.checked_source(source) for source in sources))
    plugin_names = [catalog.source_manifest(source)["name"] for source in sources]
    if len(plugin_names) != len(set(plugin_names)):
        msg = "Ambiguous plugin installations; select one source for each plugin name."
        raise ValueError(msg)
    if not selected:
        return [(source, None) for source in sources]
    inventories = [(source, {item["name"] for item in catalog.discover(source)}) for source in sources]
    for name in selected:
        if sum(name in names for _, names in inventories) > 1:
            msg = f"Ambiguous selected component: {name}. Choose one source explicitly."
            raise ValueError(msg)
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


def run_check(args: argparse.Namespace) -> None:
    """Separate integrity reporting from optional native discovery."""
    from yi import checks  # noqa: PLC0415 - Integrity checks are opt-in.

    if args.native and not args.target:
        msg = "Native checking requires --target."
        raise ValueError(msg)
    if args.accept_changes:
        checks.accept_changes(args.output)
    report = checks.inspect(args.output)
    if args.native:
        from yi import native  # noqa: PLC0415 - Native process/network dependencies are explicit.

        report["native"] = native.check(args.output, args.target, args.executable, allow_auth=args.allow_auth)
    sys.stdout.write(json.dumps(report) + "\n")
