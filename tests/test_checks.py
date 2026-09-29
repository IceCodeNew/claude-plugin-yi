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


def test_user_native_checks_prompt_only_pi_output(tmp_path) -> None:
    import os

    import pytest

    from yi.native import check

    executable = os.environ.get("YI_TEST_PI")
    if not executable:
        pytest.skip("Set YI_TEST_PI for native prompt discovery.")
    prompts = tmp_path / "pi/home/.pi/agent/prompts"
    prompts.mkdir(parents=True)
    (prompts / "yi-prompt-check.md").write_text("---\ndescription: Check\n---\nReply CHECK.\n", encoding="utf-8")
    result = check(tmp_path, "pi", executable)
    assert result["status"] == "discovered"
    assert "yi-prompt-check" in result["found"]
    assert result["cli_version"]


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


def test_user_invalid_native_options_do_not_accept_changed_files(tmp_path) -> None:
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given an edited artifact and an incomplete native-check request.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    (root / "pi/home").mkdir(parents=True)
    (root / "pi/home/a").write_bytes(b"edited")
    manifest = root / "manifests/pi-demo.json"
    original = json.dumps({"components": [], "hashes": {"pi/home/a": hashlib.sha256(b"old").hexdigest()}})
    manifest.write_text(original, encoding="utf-8")
    # When --target is missing, validation must precede accepting hashes.
    result = subprocess.run(  # noqa: S603 - Fixed CLI and task-owned fixture.
        [sys.executable, str(ENTRY), "check", "--output", str(root), "--native", "--accept-changes", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert manifest.read_text(encoding="utf-8") == original
    assert "Traceback" not in result.stderr


def test_user_native_resources_allow_platform_ancestor_alias(tmp_path) -> None:
    from yi.native import copy_discovery_resources

    # Given a target HOME beneath an ancestor alias.
    actual = tmp_path / "actual"
    skill = actual / "home/.pi/agent/skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Check.", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    # When isolated discovery copies resources, aliases outside the HOME are allowed.
    destination = tmp_path / "probe"
    copy_discovery_resources(alias / "home", destination, "pi")
    assert (destination / ".pi/agent/skills/check/SKILL.md").read_text(encoding="utf-8") == "Check."


def test_user_native_password_rejects_incomplete_startup_line(tmp_path) -> None:
    import subprocess
    import sys

    import pytest

    from yi.native_opencode import server_password

    # Given a real process that exits before terminating its password line.
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('server password incomplete')"],
        stdout=subprocess.PIPE,
    ) as process:
        # When startup ends, a partial password must not be used for authentication.
        with pytest.raises(ValueError, match="startup"):
            server_password(process)
        process.wait(timeout=5)


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
