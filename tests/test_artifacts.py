import json
import os
import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "scripts/yi.py"


def test_user_generates_one_git_commit_and_skips_unchanged_output(tmp_path) -> None:
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
    # Then the second run adds no commit and the generated skill is present.
    for result in results:
        assert result.returncode == 0, result.stderr
    assert json.loads(results[0].stdout)["committed"] is True
    assert json.loads(results[1].stdout)["committed"] is False
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
    report = {"plugin": "demo", "target": "pi", "components": [], "selection": ["demo:one"]}
    # When the second component is added, then the first remains owned and regenerable.
    apply(root, report, {"pi/home/one.txt": b"one"})
    apply(root, {**report, "selection": ["demo:two"]}, {"pi/home/two.txt": b"two"})
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
