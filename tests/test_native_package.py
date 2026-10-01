import json

import pytest

from tests.test_usage import run_cli


def test_user_stages_upstream_native_package_without_activation(tmp_path) -> None:
    # Given a plugin with an upstream Pi entrypoint and sibling skill resources.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text(
        json.dumps({"name": "sample", "pi": {"extensions": ["./native/start.ts"], "skills": ["./skills"]}}),
        encoding="utf-8",
    )
    (source / "native").mkdir()
    (source / "native/start.ts").write_text("export default function () {}\n", encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8"
    )
    # When native packaging is explicitly selected, preserve layout outside auto-discovery roots.
    result = run_cli(
        tmp_path, "migrate", "--source", str(source), "--target", "pi", "--native-package", "--dry-run", "--json"
    )
    assert "pi/home/.local/share/yi/packages/sample/native/start.ts" in result["files"]
    assert "pi/home/.local/share/yi/packages/sample/skills/check/SKILL.md" in result["files"]
    assert not any(".pi/agent/extensions" in name or name.endswith("settings.json") for name in result["files"])
    assert result["components"][0]["status"] == "unverified"
    assert result["activation"] == "not-registered"


def test_user_native_package_rejects_symlink_and_secret_resources(tmp_path) -> None:
    from yi.native_package import preview

    # Given a native package that includes a credential resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    (source / "auth.json").write_text('{"key":"fixture"}', encoding="utf-8")
    # When staging, credentials fail before any output write.
    with pytest.raises(ValueError, match="Sensitive"):
        preview(source, "pi")
    (source / "auth.json").unlink()
    (source / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        preview(source, "pi")


def test_user_native_package_rejects_external_entrypoints(tmp_path) -> None:
    from yi.native_package import preview

    # Given a package with an entrypoint outside the source root.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (tmp_path / "outside.ts").write_text("export default function() {}", encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["../outside.ts"]}}', encoding="utf-8")
    # When staged, no externally referenced code may be bundled or activated.
    with pytest.raises(ValueError, match="inside"):
        preview(source, "pi")


def test_user_native_package_refuses_special_files(tmp_path) -> None:
    import os

    from yi.native_package import preview

    # Given a native package with a FIFO instead of a regular resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    os.mkfifo(source / "stream")
    # When staged, report the unsupported file instead of silently dropping it or blocking on reads.
    with pytest.raises(ValueError, match="regular"):
        preview(source, "pi")


def test_user_native_codex_manifest_cannot_reference_external_hooks(tmp_path) -> None:
    from yi.native_package import preview

    # Given a Codex package whose manifest references a hook outside the source.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text('{"name":"sample","hooks":"../outside.json"}', encoding="utf-8")
    (tmp_path / "outside.json").write_text('{"hooks":{}}', encoding="utf-8")
    # When staged, declared runtime paths must remain inside the preserved package.
    with pytest.raises(ValueError, match="inside"):
        preview(source, "codex")


def test_user_native_staging_does_not_remove_existing_converted_skills(tmp_path) -> None:
    from yi.adapters import preview as converted_preview
    from yi.artifacts import apply
    from yi.native_package import preview

    # Given a previously converted skill and an upstream native package for the same source.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"],"skills":["skills"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8"
    )
    root = tmp_path / "output"
    apply(root, *converted_preview(source, "pi"))
    existing = root / "pi/home/.pi/agent/skills/sample-check/SKILL.md"
    before = existing.read_bytes()
    # When staging an inert native alternative, do not silently replace the active conversion.
    apply(root, *preview(source, "pi"))
    assert existing.read_bytes() == before


def test_user_native_staging_excludes_private_runtime_directories(tmp_path) -> None:
    from yi.native_package import preview

    # Given user state mixed into a package cache.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    for relative in (".aws/credentials", ".pi/sessions/private.jsonl"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private fixture", encoding="utf-8")
    # When staged, no runtime credentials or session history enters the artifact.
    report, files = preview(source, "pi")
    assert not any(".aws" in name or "/sessions/" in name for name in files)
    assert report["excluded_paths"] == [".aws/credentials", ".pi/sessions/private.jsonl"]
    assert ".aws/credentials" in report["components"][0]["reason"]
    assert "private fixture" not in report["components"][0]["reason"]


def test_user_native_codex_staging_preserves_normal_shared_configuration(tmp_path) -> None:
    from yi.adapters import preview as converted_preview
    from yi.artifacts import apply
    from yi.checks import inspect
    from yi.native_package import preview

    # Given a normal converted MCP contribution and a native package for the same source.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".mcp.json").write_text('{"mcpServers":{"docs":{"command":"fixture-server"}}}', encoding="utf-8")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text('{"name":"sample","hooks":{}}', encoding="utf-8")
    root = tmp_path / "output"
    apply(root, *converted_preview(source, "codex"))
    config = root / "codex/home/.codex/config.toml"
    original = config.read_bytes()
    # When the native alternative is staged and normal generation repeats, neither mode removes the other's state.
    apply(root, *preview(source, "codex"))
    apply(root, *converted_preview(source, "codex"))
    assert config.read_bytes() == original
    assert (root / "codex/home/.local/share/yi/packages/sample/.codex-plugin/plugin.json").is_file()
    assert inspect(root)["intact"] is True


def test_user_native_declared_resource_must_survive_copy_policy(tmp_path) -> None:
    from yi.native_package import preview

    # Given a declared skill tree located in excluded runtime state.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    (source / "sessions/private").mkdir(parents=True)
    (source / "sessions/private/SKILL.md").write_text("Private session data.", encoding="utf-8")
    (source / "package.json").write_text(
        '{"pi":{"extensions":["entry.ts"],"skills":["sessions/private"]}}', encoding="utf-8"
    )
    # When staged, reject a declaration that would point to omitted private state.
    with pytest.raises(ValueError, match="inside"):
        preview(source, "pi")


def test_user_native_entrypoint_excluded_by_suffix_is_rejected(tmp_path) -> None:
    from yi.native_package import preview

    # Given a declared entrypoint omitted by the artifact copy policy.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "entry.log").write_text("export default function() {}", encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.log"]}}', encoding="utf-8")
    # When staging, an advertised runtime entry must exist in the final inventory.
    with pytest.raises(ValueError, match="excluded"):
        preview(source, "pi")


def test_user_native_ownership_cannot_be_claimed_by_similar_plugin_name(tmp_path) -> None:
    from yi.artifacts import apply

    # Given native sample output whose old-style manifest name resembles a normal plugin name.
    root = tmp_path / "output"
    native = {"plugin": "sample", "target": "pi", "activation": "not-registered", "components": [], "owners": {}}
    apply(root, native, {"pi/home/.local/share/yi/packages/sample/entry.ts": b"native"})
    # When another plugin claims that manifest identity, preserve the native output.
    normal = {"plugin": "sample--native", "target": "pi", "components": [], "owners": {}}
    with pytest.raises(ValueError, match="identity"):
        apply(root, normal, {"pi/home/normal.txt": b"normal"})
    assert (root / "pi/home/.local/share/yi/packages/sample/entry.ts").read_bytes() == b"native"


def test_user_native_declared_hooks_cannot_disappear_from_copy(tmp_path) -> None:
    from yi.native_package import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text('{"name":"sample","hooks":"hooks.log"}', encoding="utf-8")
    (source / "hooks.log").write_text('{"hooks":{}}', encoding="utf-8")
    # A source declaration cannot survive without its referenced staged file.
    with pytest.raises(ValueError, match="excluded"):
        preview(source, "codex")


def test_user_native_package_refuses_embedded_mcp_credentials(tmp_path) -> None:
    from yi.native_package import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "api": {
                        "url": "https://example.invalid/mcp",
                        "headers": {"Authorization": "Bearer fixture-private-token"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # Native layout preservation must not export a resolved credential.
    with pytest.raises(ValueError, match="credential"):
        preview(source, "pi")


@pytest.mark.parametrize("document", [None, [], {"pi": {"extensions": "entry.ts"}}])
def test_user_native_invalid_package_declaration_returns_controlled_error(tmp_path, document) -> None:
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given an invalid upstream package declaration.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text(json.dumps(document), encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    # When previewed, malformed external metadata must not leak a traceback.
    result = subprocess.run(  # noqa: S603 - Fixed helper and isolated malformed package metadata.
        [
            sys.executable,
            str(ENTRY),
            "migrate",
            "--source",
            str(source),
            "--target",
            "pi",
            "--native-package",
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("filename", "server"),
    [
        (
            "mcp.json",
            {"url": "https://example.invalid/mcp", "headers": {"Authorization": "Bearer fixture-private-token"}},
        ),
        (".mcp.json", {"url": "https://user:fixture-private-token@example.invalid/mcp"}),
    ],
)
def test_user_native_mcp_credentials_cannot_bypass_declared_resource_checks(tmp_path, filename, server) -> None:
    from yi.native_package import preview

    # Given MCP configuration with resolved credentials in a declared resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "sample", "mcpServers": filename}), encoding="utf-8"
    )
    (source / filename).write_text(json.dumps({"mcpServers": {"api": server}}), encoding="utf-8")
    # When staged, neither filename changes nor URL authentication may export the credential.
    with pytest.raises(ValueError, match="credential"):
        preview(source, "codex")


def test_user_declared_mcp_fifo_fails_without_blocking_preview(tmp_path) -> None:
    import os
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a declared MCP resource that is a FIFO, not a JSON file.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text('{"name":"sample","mcpServers":"mcp.json"}', encoding="utf-8")
    os.mkfifo(source / "mcp.json")
    # When previewed in a real child process, reject before a blocking read.
    result = subprocess.run(  # noqa: S603 - Fixed local helper and isolated FIFO fixture.
        [
            sys.executable,
            str(ENTRY),
            "migrate",
            "--source",
            str(source),
            "--target",
            "codex",
            "--native-package",
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    assert result.returncode != 0
    assert "regular" in json.loads(result.stdout)["error"]


def test_user_native_inline_wrapper_named_server_cannot_export_credentials(tmp_path) -> None:
    from yi.native_package import preview

    # Given an inline server whose name matches the file-document wrapper key.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    manifest = {
        "name": "sample",
        "mcpServers": {
            "mcpServers": {"command": "fixture", "headers": {"Authorization": "Bearer synthetic-private-token"}}
        },
    }
    (source / ".claude-plugin/plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["entry.ts"]}}', encoding="utf-8")
    (source / "entry.ts").write_text("export default function() {}", encoding="utf-8")
    # When staging, server names must not change credential validation semantics.
    with pytest.raises(ValueError, match="credential"):
        preview(source, "pi")


@pytest.mark.parametrize(
    "declaration",
    [
        {"extensions": ["extensions"]},
        {"skills": ["skills"]},
        {"prompts": ["prompts/*.md", "!prompts/excluded.md"]},
    ],
)
def test_user_stages_pi_resource_only_directories_and_globs_inertly(tmp_path, declaration) -> None:
    from yi.native_package import preview

    # Given a valid Pi package declaration using native directory/glob resource syntax, without requiring extensions.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text(json.dumps({"pi": declaration}), encoding="utf-8")
    (source / "extensions").mkdir()
    (source / "extensions/start.ts").write_text("export default function() {}\n", encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text("---\ndescription: Check\n---\nRead text.\n", encoding="utf-8")
    (source / "prompts").mkdir()
    (source / "prompts/receipt.md").write_text("Reply RECEIPT.\n", encoding="utf-8")
    (source / "prompts/excluded.md").write_text("Inactive retained source example.\n", encoding="utf-8")
    original = (source / "package.json").read_bytes()
    # When staged, preserve the original manifest and layout without registering or executing any resource.
    report, files = preview(source, "pi")
    assert report["activation"] == "not-registered"
    assert files["pi/home/.local/share/yi/packages/sample/package.json"] == original
    assert "pi/home/.local/share/yi/packages/sample/prompts/receipt.md" in files
    assert not any(".pi/agent/extensions" in path for path in files)


@pytest.mark.parametrize("filtered", [False, True])
def test_user_pi_glob_validation_uses_actual_prompt_inventory(tmp_path, filtered) -> None:
    from yi.native_package import preview

    # Given a prompt glob with hidden/empty/non-prompt paths and an optional excluded log.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    entries = ["prompts/*"] + (["!prompts/debug.log"] if filtered else [])
    (source / "package.json").write_text(json.dumps({"pi": {"prompts": entries}}), encoding="utf-8")
    (source / "prompts/empty").mkdir(parents=True)
    (source / "prompts/.empty").mkdir()
    (source / "prompts/receipt.md").write_text("Reply RECEIPT.\n", encoding="utf-8")
    (source / "prompts/.hidden.log").write_text("Hidden log.\n", encoding="utf-8")
    if filtered:
        (source / "prompts/debug.log").write_text("Explicitly filtered native file.\n", encoding="utf-8")
    # When staged, require only Pi's effective prompt resources, not every raw filesystem match.
    report, files = preview(source, "pi")
    assert report["activation"] == "not-registered"
    assert "pi/home/.local/share/yi/packages/sample/prompts/receipt.md" in files


@pytest.mark.parametrize("entry", ["../../../outside.ts", "start.log"])
def test_user_pi_nested_extension_declarations_require_review(tmp_path, entry) -> None:
    from yi.native_package import preview

    # Given a nested extension package that redirects native entrypoint resolution.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text('{"pi":{"extensions":["extensions"]}}', encoding="utf-8")
    (source / "extensions/sub").mkdir(parents=True)
    (source / "extensions/sub/package.json").write_text(json.dumps({"pi": {"extensions": [entry]}}), encoding="utf-8")
    (source / "extensions/sub/start.log").write_text("Inert entry.\n", encoding="utf-8")
    (tmp_path / "outside.ts").write_text("export default function() {}\n", encoding="utf-8")
    # When staging, block indirect declarations rather than assume directory presence proves containment.
    with pytest.raises(ValueError, match=r"nested|Nested"):
        preview(source, "pi")


@pytest.mark.parametrize("restore", [False, True])
def test_user_pi_filter_precedence_preserves_only_effective_required_resources(tmp_path, restore) -> None:
    from yi.native_package import preview

    # Given a log-file declaration removed by exclusion and optionally reinstated by an exact plus rule.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    entries = ["prompts/*", "!prompts/debug.log"] + (["+prompts/debug.log"] if restore else [])
    (source / "package.json").write_text(json.dumps({"pi": {"prompts": entries}}), encoding="utf-8")
    (source / "prompts").mkdir()
    (source / "prompts/debug.log").write_text("Inert native file omitted by copy policy.\n", encoding="utf-8")
    (source / "prompts/receipt.md").write_text("Reply RECEIPT.\n", encoding="utf-8")
    # When resolved, plus reinstatement must restore the survival obligation rather than bypass copy policy.
    if restore:
        with pytest.raises(ValueError, match="excluded"):
            preview(source, "pi")
    else:
        _report, files = preview(source, "pi")
        assert "pi/home/.local/share/yi/packages/sample/prompts/receipt.md" in files


@pytest.mark.parametrize("pattern", ["prompts/**/*.md", "prompts/{a,b}*.md", "../outside.md", "/outside.md"])
def test_user_pi_unsupported_or_escaping_glob_requires_explicit_review(tmp_path, pattern) -> None:
    from yi.native_package import preview

    # Given a declaration outside the supported contained single-level grammar.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "package.json").write_text(json.dumps({"pi": {"prompts": [pattern]}}), encoding="utf-8")
    # When planned, fail clearly rather than claim the native-valid advanced pattern was resolved.
    with pytest.raises(ValueError, match=r"Unsupported|inside"):
        preview(source, "pi")
