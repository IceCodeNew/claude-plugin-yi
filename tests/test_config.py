from tests.test_usage import run_cli


def test_user_saves_and_reads_output_root(tmp_path) -> None:
    # Given a chosen output root, when saved, then later operations resolve the same path.
    root = tmp_path / "artifacts"
    saved = run_cli(tmp_path, "config", "--output", str(root), "--json")
    loaded = run_cli(tmp_path, "config", "--json")
    assert saved == loaded == {"output_root": str(root)}
    assert not root.exists()


def test_user_empty_xdg_data_home_falls_back_to_home(tmp_path) -> None:
    import json
    import os
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given an empty XDG variable and isolated HOME, default state must not use the current directory.
    result = subprocess.run(  # noqa: S603 - Fixed local helper and isolated environment.
        [sys.executable, str(ENTRY), "config", "--json"],
        env={**os.environ, "HOME": str(tmp_path), "XDG_DATA_HOME": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["output_root"] == str(tmp_path / ".local/share/yi/artifacts")


def test_user_model_mapping_survives_output_root_updates(tmp_path) -> None:
    # Given an explicitly selected model for one target and source alias.
    mapped = run_cli(
        tmp_path, "config", "--target", "opencode-v2", "--model-map", "opus=anthropic/claude-opus-5-5", "--json"
    )
    assert mapped["model_mappings"]["opencode-v2"]["opus"] == "anthropic/claude-opus-5-5"
    # When the output root changes, the model decision remains intact.
    result = run_cli(tmp_path, "config", "--output", str(tmp_path / "artifacts"), "--json")
    assert result["model_mappings"] == mapped["model_mappings"]


def test_user_invalid_model_mapping_does_not_change_saved_configuration(tmp_path) -> None:
    import json
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a saved output root and an invalid explicit model mapping.
    run_cli(tmp_path, "config", "--output", str(tmp_path / "output"), "--json")
    path = tmp_path / "data/config.json"
    original = path.read_bytes()
    # When validation fails, no partial configuration is persisted.
    result = subprocess.run(  # noqa: S603 - Fixed local CLI and isolated invalid configuration.
        [
            sys.executable,
            str(ENTRY),
            "--data-dir",
            str(tmp_path / "data"),
            "config",
            "--target",
            "codex",
            "--model-map",
            "opus=",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert path.read_bytes() == original


def test_user_malformed_mapping_store_returns_controlled_error(tmp_path) -> None:
    import json
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a locally edited configuration with an invalid mappings container.
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "config.json").write_text('{"output_root":"/tmp/example","model_mappings":[]}', encoding="utf-8")
    # When configuration is queried, report the invalid shape instead of a later attribute error.
    result = subprocess.run(  # noqa: S603 - Fixed local CLI and task-owned configuration.
        [sys.executable, str(ENTRY), "--data-dir", str(directory), "config", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Traceback" not in result.stderr
