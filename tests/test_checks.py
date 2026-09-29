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
