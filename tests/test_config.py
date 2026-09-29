from tests.test_usage import run_cli


def test_user_saves_and_reads_output_root(tmp_path) -> None:
    # Given a chosen output root, when saved, then later operations resolve the same path.
    root = tmp_path / "artifacts"
    saved = run_cli(tmp_path, "config", "--output", str(root), "--json")
    loaded = run_cli(tmp_path, "config", "--json")
    assert saved == loaded == {"output_root": str(root)}
    assert not root.exists()
