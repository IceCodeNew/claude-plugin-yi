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
