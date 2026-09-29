import hashlib
import json

from tests.test_usage import run_cli


def test_user_previews_installation_before_explicit_apply(tmp_path) -> None:
    # Given an intact unverified artifact and an empty target HOME.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    resource = root / "pi/home/.pi/agent/prompts/check.md"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"Check text.\n")
    relative = resource.relative_to(root).as_posix()
    (root / "manifests/pi-demo.json").write_text(
        json.dumps(
            {
                "plugin": "demo",
                "target": "pi",
                "components": [{"status": "unverified"}],
                "hashes": {relative: hashlib.sha256(resource.read_bytes()).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "destination"
    args = ("install", "--output", str(root), "--target", "pi", "--destination", str(destination), "--json")
    # When previewed, no destination files are created.
    preview = run_cli(tmp_path, *args)
    assert preview["applied"] is False
    assert not destination.exists()
    # When explicitly accepted as unverified, then only the owned prompt is copied.
    result = run_cli(tmp_path, *args, "--apply", "--accept-unverified")
    assert result["applied"] is True
    assert (destination / ".pi/agent/prompts/check.md").read_bytes() == b"Check text.\n"


def test_user_cannot_claim_installation_with_no_artifacts(tmp_path) -> None:
    import pytest

    from yi.install import install

    # Given an empty artifact directory, when applying, then no false success is returned.
    with pytest.raises(ValueError, match=r"No.*artifacts"):
        install(tmp_path / "empty", "pi", tmp_path / "home", apply=True, accept_unverified=True)


def test_user_installs_scripts_with_executable_mode(tmp_path) -> None:
    from yi.install import install

    # Given an intact executable resource in a migration manifest.
    root = tmp_path / "output"
    (root / "manifests").mkdir(parents=True)
    script = root / "pi/home/run.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"#!/bin/sh\nexit 0\n")
    (root / "manifests/pi-demo.json").write_text(
        json.dumps(
            {
                "components": [],
                "executables": ["pi/home/run.sh"],
                "hashes": {"pi/home/run.sh": hashlib.sha256(script.read_bytes()).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    # When installed, then the executable permission is present on the destination.
    home = tmp_path / "home"
    install(root, "pi", home, apply=True, accept_unverified=False)
    assert (home / "run.sh").stat().st_mode & 0o111
