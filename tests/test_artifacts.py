import json
import os
import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "scripts/yi.py"


def test_user_generates_without_staging_and_skips_unchanged_output(tmp_path) -> None:
    # Given a skill-only plugin and explicit local Git identity for an isolated test repository.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check text\n---\nCheck text.\n", encoding="utf-8"
    )
    root = tmp_path / "output"
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    args = [
        sys.executable,
        str(ENTRY),
        "migrate",
        "--source",
        str(source),
        "--target",
        "codex",
        "--output",
        str(root),
        "--json",
    ]
    # When the user generates the same plugin twice.
    results = [subprocess.run(args, env=env, capture_output=True, text=True, check=False) for _ in range(2)]  # noqa: S603 - Fixed local CLI.
    # Then repeated generation is unchanged and Git staging remains the user's responsibility.
    for result in results:
        assert result.returncode == 0, result.stderr
    assert json.loads(results[0].stdout)["changed"] is True
    assert json.loads(results[1].stdout)["changed"] is False
    assert (root / ".git").is_dir()
    assert (root / "codex/home/.agents/skills/demo-check/SKILL.md").is_file()


def test_user_cannot_replace_an_unowned_manifest(tmp_path) -> None:
    # Given an unrelated clean repository with a file at yi's manifest path.
    import pytest

    from yi.artifacts import apply, git

    root = tmp_path / "output"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "manifests").mkdir()
    manifest = root / "manifests/codex-demo.json"
    manifest.write_text('{"hashes":{},"note":"keep me"}', encoding="utf-8")
    git(root, "add", "manifests/codex-demo.json")
    git(root, "commit", "-m", "fixture: preserve unrelated file")
    # When yi attempts to claim that repository, then it refuses without replacing content.
    with pytest.raises(ValueError, match="owned"):
        apply(root, {"plugin": "demo", "target": "codex"}, {})
    assert "keep me" in manifest.read_text(encoding="utf-8")


def test_user_cannot_overwrite_committed_manual_artifact_edits(tmp_path) -> None:
    # Given an owned artifact whose content was manually changed and committed.
    import hashlib

    import pytest

    from yi.artifacts import apply, git

    root = tmp_path / "output"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    (root / "manifests").mkdir()
    (root / "manifests/codex-demo.json").write_text(
        json.dumps(
            {
                "hashes": {"codex/home/check.txt": hashlib.sha256(b"generated").hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    (root / "codex/home").mkdir(parents=True)
    (root / "codex/home/check.txt").write_bytes(b"manual revision")
    git(root, "add", "--", ".yi-artifacts.json", "manifests/codex-demo.json", "codex/home/check.txt")
    git(root, "commit", "-m", "fixture: retain user revision")
    # When regenerated, then even clean committed edits are protected.
    with pytest.raises(ValueError, match="modified"):
        apply(root, {"plugin": "demo", "target": "codex"}, {"codex/home/check.txt": b"generated"})
    assert (root / "codex/home/check.txt").read_bytes() == b"manual revision"


def test_user_preserves_previous_component_ownership(tmp_path) -> None:
    from yi.artifacts import apply, git

    # Given one owned artifact repository with two separately selected components.
    root = tmp_path / "output"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize artifact owner")
    report = {
        "plugin": "demo",
        "target": "pi",
        "components": [],
        "selection": ["demo:one"],
        "owners": {"pi/home/one.txt": "demo:one"},
    }
    # When the second component is added, then the first remains owned and regenerable.
    apply(root, report, {"pi/home/one.txt": b"one"})
    apply(
        root,
        {**report, "selection": ["demo:two"], "owners": {"pi/home/two.txt": "demo:two"}},
        {"pi/home/two.txt": b"two"},
    )
    manifest = json.loads((root / "manifests/pi-demo.json").read_text(encoding="utf-8"))
    assert set(manifest["hashes"]) == {"pi/home/one.txt", "pi/home/two.txt"}
    apply(root, report, {"pi/home/one.txt": b"one"})


def test_user_cannot_write_manifest_through_symlink(tmp_path) -> None:
    import pytest

    from yi.artifacts import apply, git

    # Given a clean owned repository with a linked manifest directory.
    root = tmp_path / "output"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    (root / "manifests").symlink_to(outside, target_is_directory=True)
    git(root, "add", "--", ".yi-artifacts.json", "manifests")
    git(root, "commit", "-m", "fixture: linked manifests")
    # When migration starts, then the outside directory remains untouched.
    with pytest.raises(ValueError, match="symlink"):
        apply(root, {"plugin": "demo", "target": "pi"}, {})
    assert list(outside.iterdir()) == []


def test_user_can_generate_without_git_identity(tmp_path) -> None:
    # Given an isolated Git environment with no identity.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.useConfigOnly",
            "GIT_CONFIG_VALUE_0": "true",
        }
    )
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    output = tmp_path / "output"
    # When generating without identity, files remain usable in an initialized output repository.
    result = subprocess.run(  # noqa: S603 - Fixed local CLI with isolated Git configuration.
        [sys.executable, str(ENTRY), "migrate", "--source", str(source), "--target", "pi", "--output", str(output)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (output / ".git").is_dir()
    assert (output / ".yi-artifacts.json").is_file()


def test_user_partial_regeneration_removes_stale_selected_resources(tmp_path) -> None:
    from yi.artifacts import apply, git

    # Given two owned components and a resource removed from the first component.
    root = tmp_path / "output"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize")
    report = {
        "plugin": "demo",
        "target": "pi",
        "selection": ["demo:one"],
        "components": [],
        "owners": {"pi/home/one.txt": "demo:one", "pi/home/old.txt": "demo:one"},
    }
    apply(root, report, {"pi/home/one.txt": b"one", "pi/home/old.txt": b"old"})
    apply(
        root,
        {**report, "selection": ["demo:two"], "owners": {"pi/home/two.txt": "demo:two"}},
        {"pi/home/two.txt": b"two"},
    )
    # When one is regenerated, then stale files disappear while two remains owned.
    apply(root, {**report, "owners": {"pi/home/one.txt": "demo:one"}}, {"pi/home/one.txt": b"one"})
    manifest = json.loads((root / "manifests/pi-demo.json").read_text(encoding="utf-8"))
    assert not (root / "pi/home/old.txt").exists()
    assert set(manifest["hashes"]) == {"pi/home/one.txt", "pi/home/two.txt"}
    assert set(manifest["files"]) == set(manifest["hashes"])


def test_user_generated_skill_survives_check_and_install(tmp_path) -> None:
    from yi.adapters import preview
    from yi.artifacts import apply, git
    from yi.checks import inspect
    from yi.install import install

    # Given a portable skill with an executable resource and an owned artifact repository.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nRun ./run.sh.\n", encoding="utf-8")
    (skill / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
    (skill / "run.sh").chmod(0o755)
    root = tmp_path / "artifacts"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize")
    # When real generation feeds integrity checking and installation.
    report, files = preview(source, "pi", ["demo:check"])
    apply(root, report, files)
    assert inspect(root)["intact"] is True
    home = tmp_path / "home"
    install(root, "pi", home, apply=True, accept_unverified=True)
    # Then namespaced content and executable mode survive the complete pipeline.
    installed = home / ".pi/agent/skills/demo-check"
    assert b"name: demo-check" in (installed / "SKILL.md").read_bytes()
    assert (installed / "run.sh").read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert (installed / "run.sh").stat().st_mode & 0o111


def test_user_combines_opencode_mcp_from_two_plugins(tmp_path) -> None:
    from yi.adapters import preview
    from yi.artifacts import apply, git

    root = tmp_path / "artifacts"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize")
    # Given two plugins defining independently named disabled MCP servers.
    for name in ("alpha", "beta"):
        source = tmp_path / name
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (source / ".mcp.json").write_text('{"mcpServers":{"docs":{"command":"fixture-server"}}}', encoding="utf-8")
        report, files = preview(source, "opencode-v2")
        apply(root, report, files)
    # Then one target config contains both server declarations.
    config = json.loads((root / "opencode-v2/home/.config/opencode/opencode.json").read_text(encoding="utf-8"))
    assert set(config["mcp"]["servers"]) == {"alpha-docs", "beta-docs"}


def test_user_removes_one_shared_mcp_contribution_without_removing_other_plugins(tmp_path) -> None:
    from yi.adapters import preview
    from yi.artifacts import apply, git
    from yi.checks import inspect

    root = tmp_path / "artifacts"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize")
    for name in ("alpha", "beta"):
        source = tmp_path / name
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (source / ".mcp.json").write_text('{"mcpServers":{"docs":{"command":"fixture-server"}}}', encoding="utf-8")
        apply(root, *preview(source, "opencode-v2"))
    # When alpha no longer declares a server, beta's shared configuration remains valid.
    (tmp_path / "alpha/.mcp.json").unlink()
    apply(root, *preview(tmp_path / "alpha", "opencode-v2"))
    config = json.loads((root / "opencode-v2/home/.config/opencode/opencode.json").read_text(encoding="utf-8"))
    assert set(config["mcp"]["servers"]) == {"beta-docs"}
    assert inspect(root)["intact"] is True
    skill = tmp_path / "alpha/skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8")
    apply(root, *preview(tmp_path / "alpha", "opencode-v2", ["alpha:check"]))
    assert inspect(root)["intact"] is True


def test_user_output_root_can_have_a_platform_symlink_ancestor(tmp_path) -> None:
    from yi.artifacts import validate_paths

    # Given a platform-style directory alias above the selected root.
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    root = alias / "artifacts"
    root.mkdir()
    # When an internal regular path is checked, ancestor aliases do not count as output escapes.
    validate_paths(root, ["pi/home/skill.md"])


def test_user_batch_failure_emits_json_and_marks_remaining_units_unattempted(tmp_path) -> None:
    # Given a nonempty unowned artifact root and two valid plugins.
    sources = []
    for name in ("alpha", "beta"):
        source = tmp_path / name
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        sources.extend(["--source", str(source)])
    output = tmp_path / "output"
    output.mkdir()
    (output / "keep.txt").write_text("Unowned.", encoding="utf-8")
    # When the first unit fails, the machine-readable report still covers both units.
    result = subprocess.run(  # noqa: S603 - Fixed local CLI with isolated source and output files.
        [sys.executable, str(ENTRY), "migrate", *sources, "--target", "pi", "--output", str(output), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["plans"][0]["status"] == "failed"
    assert report["plans"][1]["status"] == "not-attempted"
    assert not (output / ".git").exists()


def test_user_reviewed_edits_are_not_overwritten_by_regeneration(tmp_path) -> None:
    import pytest

    from yi.artifacts import apply, git
    from yi.checks import accept_changes

    root = tmp_path / "output"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / ".yi-artifacts.json").write_text('{"owner":"yi","schema":1}', encoding="utf-8")
    git(root, "add", ".yi-artifacts.json")
    git(root, "commit", "-m", "fixture: initialize")
    report = {"plugin": "demo", "target": "pi", "components": [], "owners": {"pi/home/notes.txt": "demo:check"}}
    apply(root, report, {"pi/home/notes.txt": b"generated"})
    (root / "pi/home/notes.txt").write_bytes(b"human revision")
    accept_changes(root)
    git(root, "add", "pi/home/notes.txt", "manifests/pi-demo.json")
    git(root, "commit", "-m", "fixture: accept reviewed change")
    with pytest.raises(ValueError, match="reviewed"):
        apply(root, report, {"pi/home/notes.txt": b"generated"})
    assert (root / "pi/home/notes.txt").read_bytes() == b"human revision"


def test_user_shared_configuration_has_one_owner_without_rewriting_other_plugins(tmp_path) -> None:
    from yi.adapters import preview
    from yi.artifacts import apply
    from yi.checks import inspect

    root = tmp_path / "output"
    for name in ("alpha", "beta"):
        source = tmp_path / name
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (source / ".mcp.json").write_text('{"mcpServers":{"docs":{"command":"fixture"}}}', encoding="utf-8")
    apply(root, *preview(tmp_path / "alpha", "opencode-v2"))
    alpha = (root / "manifests/opencode-v2-alpha.json").read_bytes()
    apply(root, *preview(tmp_path / "beta", "opencode-v2"))
    assert (root / "manifests/opencode-v2-alpha.json").read_bytes() == alpha
    assert inspect(root)["intact"] is True
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in (root / "manifests").glob("*.json")]
    path = "opencode-v2/home/.config/opencode/opencode.json"
    assert sum(path in item["hashes"] for item in manifests) == 1


def test_user_skill_generation_ignores_unrelated_corrupt_target_manifest(tmp_path) -> None:
    from yi.artifacts import apply

    root = tmp_path / "output"
    apply(root, {"plugin": "demo", "target": "pi", "components": [], "owners": {}}, {})
    (root / "manifests/codex-broken.json").write_text("invalid json", encoding="utf-8")
    assert apply(
        root,
        {"plugin": "demo", "target": "pi", "components": [], "owners": {"pi/home/notes.txt": "demo:notes"}},
        {"pi/home/notes.txt": b"notes"},
    )


def test_user_reviewed_executable_change_is_protected(tmp_path) -> None:
    import pytest

    from yi.artifacts import apply
    from yi.checks import accept_changes

    root = tmp_path / "output"
    report = {
        "plugin": "demo",
        "target": "pi",
        "components": [],
        "owners": {"pi/home/run.sh": "demo:run"},
        "executables": ["pi/home/run.sh"],
    }
    apply(root, report, {"pi/home/run.sh": b"exit 0\n"})
    (root / "pi/home/run.sh").chmod(0o644)
    accept_changes(root)
    with pytest.raises(ValueError, match="reviewed"):
        apply(root, report, {"pi/home/run.sh": b"exit 0\n"})
    assert not (root / "pi/home/run.sh").stat().st_mode & 0o111


def test_user_parent_file_conflict_does_not_partially_update_artifacts(tmp_path) -> None:
    import pytest

    from yi.artifacts import apply

    # Given a generated file and an unowned file where a future directory is needed.
    root = tmp_path / "output"
    report = {"plugin": "demo", "target": "pi", "components": [], "owners": {"pi/home/a": "demo:a"}}
    apply(root, report, {"pi/home/a": b"original"})
    (root / "pi/home/new").write_bytes(b"unowned")
    # When a plan updates a and creates new/child, preflight rejects the parent conflict before any write.
    with pytest.raises(ValueError, match="directory"):
        apply(root, report, {"pi/home/a": b"changed", "pi/home/new/child": b"child"})
    assert (root / "pi/home/a").read_bytes() == b"original"


def test_user_unaccepted_execution_mode_change_is_not_overwritten(tmp_path) -> None:
    import pytest

    from yi.artifacts import apply
    from yi.checks import inspect

    # Given a generated executable changed manually without acceptance.
    root = tmp_path / "output"
    report = {
        "plugin": "demo",
        "target": "pi",
        "components": [],
        "owners": {"pi/home/run.sh": "demo:run"},
        "executables": ["pi/home/run.sh"],
    }
    apply(root, report, {"pi/home/run.sh": b"exit 0\n"})
    (root / "pi/home/run.sh").chmod(0o600)
    # When inspected and regenerated, report the edit and preserve the user's permission choice.
    assert inspect(root)["intact"] is False
    with pytest.raises(ValueError, match="modified"):
        apply(root, report, {"pi/home/run.sh": b"exit 0\n"})
    assert (root / "pi/home/run.sh").stat().st_mode & 0o777 == 0o600
