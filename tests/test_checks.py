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


def test_user_native_check_reports_missing_cli_without_claiming_success(tmp_path) -> None:
    # Given an intact generated skill and an unavailable explicitly selected CLI.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    (root / "codex/home").mkdir(parents=True)
    (root / "codex/home/check.txt").write_bytes(b"check")
    (root / "manifests/codex-demo.json").write_text(
        json.dumps(
            {
                "plugin": "demo",
                "target": "codex",
                "components": [],
                "hashes": {"codex/home/check.txt": hashlib.sha256(b"check").hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    # When native validation is requested, then an absent binary remains explicitly unverified.
    result = run_cli(
        tmp_path,
        "check",
        "--output",
        str(root),
        "--native",
        "--target",
        "codex",
        "--executable",
        str(tmp_path / "missing-codex"),
        "--json",
    )
    assert result["runtime_verified"] is False
    assert result["native"]["status"] == "unavailable"


def test_user_native_checks_pi_skill_with_real_cli(tmp_path) -> None:
    import os
    import shutil

    import pytest

    from yi.native import check

    executable = os.environ.get("YI_TEST_PI") or shutil.which("pi")
    if executable is None or "/shims/" in executable:
        pytest.skip("Pi CLI is not installed on this runner.")
    # Given a generated skill in an isolated target HOME.
    skill = tmp_path / "pi/home/.pi/agent/skills/yi-native-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: yi-native-check\ndescription: Inspect only\n---\nDo nothing.\n", encoding="utf-8"
    )
    # When the real CLI lists resources, then yi confirms discovery without a model task.
    result = check(tmp_path, "pi", executable)
    assert result["status"] == "discovered"
    assert "yi-native-check" in result["found"]


def test_user_native_checks_codex_skill_with_real_cli(tmp_path) -> None:
    import shutil

    import pytest

    from yi.native import check

    executable = shutil.which("codex")
    if executable is None or "/shims/" in executable:
        pytest.skip("A direct Codex executable is not installed.")
    # Given a generated skill in an isolated Codex HOME.
    skill = tmp_path / "codex/home/.agents/skills/yi-native-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: yi-native-check\ndescription: Inspect only\n---\nDo nothing.\n", encoding="utf-8"
    )
    # When app-server performs discovery, no model turn is required.
    result = check(tmp_path, "codex", executable)
    assert result["status"] == "discovered"
    assert "yi-native-check" in result["found"]


def test_user_native_checks_opencode_skill_with_real_cli(tmp_path) -> None:
    import os

    import pytest

    from yi.native import check

    executable = os.environ.get("YI_TEST_OPENCODE")
    if not executable:
        pytest.skip("Set YI_TEST_OPENCODE for the native OpenCode smoke test.")
    # Given an isolated generated skill, wait for resource initialization rather than only server startup.
    skill = tmp_path / "opencode-v2/home/.config/opencode/skills/yi-native-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: yi-native-check\ndescription: Inspect only\n---\nDo nothing.\n", encoding="utf-8"
    )
    result = check(tmp_path, "opencode-v2", executable)
    assert result["status"] == "discovered"


def test_user_amp_check_requires_explicit_auth_access(tmp_path) -> None:
    import os

    import pytest

    from yi.native import check

    executable = os.environ.get("YI_TEST_AMP")
    if not executable:
        pytest.skip("Set YI_TEST_AMP for the native Amp smoke test.")
    # Given Amp discovery may access account metadata, default checks must not reuse credentials.
    result = check(tmp_path, "ampcode", executable)
    assert result["status"] == "authorization-required"
