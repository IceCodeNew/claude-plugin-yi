import hashlib
import json

from tests.test_usage import run_cli


def test_user_detects_modified_migration_artifacts(tmp_path) -> None:
    # Given a generated manifest and a file modified after generation.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    (root / "codex/home").mkdir(parents=True)
    resource = root / "codex/home/check.txt"
    resource.write_bytes(b"modified")
    (root / "manifests/codex-demo.json").write_text(
        json.dumps(
            {
                "plugin": "demo",
                "target": "codex",
                "components": [],
                "hashes": {"codex/home/check.txt": hashlib.sha256(b"original").hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    # When inspection runs, then the changed file is reported without rewriting it.
    result = run_cli(tmp_path, "check", "--output", str(root), "--json")
    assert result["intact"] is False
    assert result["changed"] == ["codex/home/check.txt"]
    assert resource.read_bytes() == b"modified"


def test_user_can_accept_reviewed_artifact_changes_without_claiming_behavior(tmp_path) -> None:
    # Given a reviewed resource edited by the target harness.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    (root / "pi/home").mkdir(parents=True)
    (root / "pi/home/notes.txt").write_bytes(b"reviewed")
    (root / "manifests/pi-demo.json").write_text(
        json.dumps(
            {
                "target": "pi",
                "plugin": "demo",
                "components": [{"name": "demo:check", "status": "unverified"}],
                "hashes": {"pi/home/notes.txt": hashlib.sha256(b"old").hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(tmp_path, "check", "--output", str(root), "--accept-changes", "--json")
    assert result["intact"] is True
    assert result["runtime_verified"] is False


def test_user_cannot_accept_shared_configuration_execution_mode(tmp_path) -> None:
    import pytest

    from yi.checks import accept_changes

    # Given a rendered shared configuration with an unreviewed executable bit.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    path = root / "codex/home/.codex/config.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# config\n")
    path.chmod(0o755)
    manifest = root / "manifests/codex--shared.json"
    original = json.dumps(
        {
            "hashes": {"codex/home/.codex/config.toml": hashlib.sha256(path.read_bytes()).hexdigest()},
            "executables": [],
            "components": [],
        }
    )
    manifest.write_text(original, encoding="utf-8")
    # When acceptance is requested, shared generated metadata stays protected.
    with pytest.raises(ValueError, match="Shared configuration"):
        accept_changes(root)
    assert manifest.read_text(encoding="utf-8") == original
