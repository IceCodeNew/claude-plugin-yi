import json

import pytest

from tests.test_usage import run_cli


@pytest.mark.parametrize("target", ["ampcode", "codex", "opencode-v2", "pi"])
def test_user_previews_skill_resources_and_hook_blockers(tmp_path, target) -> None:
    # Given a plugin with portable instructions, resources, and an unsupported hook.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: check\ndescription: Check text\n---\nRead notes.txt.\n", encoding="utf-8"
    )
    (skill / "notes.txt").write_text("A portable resource.\n", encoding="utf-8")
    (source / "hooks").mkdir()
    (source / "hooks/hooks.json").write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
    output = tmp_path / "artifacts"
    # When the user previews conversion.
    result = run_cli(
        tmp_path, "migrate", "--source", str(source), "--target", target, "--output", str(output), "--dry-run", "--json"
    )
    # Then resources are planned, hooks remain blockers, and no output is written.
    assert any(name.endswith("notes.txt") for name in result["files"])
    assert result["complete"] is False
    assert any(item["kind"] == "hooks" and item["status"] == "blocked" for item in result["components"])
    assert not output.exists()


def test_user_selects_multiple_targets_in_one_preview(tmp_path) -> None:
    # Given a plugin without executable components.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    # When two targets are selected together.
    result = run_cli(
        tmp_path,
        "migrate",
        "--source",
        str(source),
        "--target",
        "codex",
        "--target",
        "pi",
        "--output",
        str(tmp_path / "output"),
        "--dry-run",
        "--json",
    )
    # Then both target plans are returned.
    assert [item["target"] for item in result["plans"]] == ["codex", "pi"]


def test_user_cannot_export_skill_directory_symlinks(tmp_path) -> None:
    # Given a skill directory that points outside the plugin.
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "SKILL.md").write_text("private data", encoding="utf-8")
    (source / "skills").mkdir()
    (source / "skills/leak").symlink_to(outside, target_is_directory=True)
    # When conversion is requested, then it refuses the source before copying bytes.
    with pytest.raises(ValueError, match="symlink"):
        preview(source, "codex")


@pytest.mark.parametrize("target", ["opencode-v2", "pi"])
def test_user_converts_plain_commands_to_native_prompt_files(tmp_path, target) -> None:
    # Given a plain command without Claude-only execution or permission fields.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/review.md").write_text(
        "---\ndescription: Review text\n---\nReview the supplied text.\n", encoding="utf-8"
    )
    # When the target supports Markdown commands or prompts.
    result = run_cli(
        tmp_path,
        "migrate",
        "--source",
        str(source),
        "--target",
        target,
        "--output",
        str(tmp_path / "output"),
        "--dry-run",
        "--json",
    )
    # Then a native prompt file is generated with no unsupported-command blocker.
    assert any(path.endswith("sample-review.md") for path in result["files"])
    assert not any(item["status"] == "blocked" for item in result["components"])


def test_user_blocks_exclusive_skill_tool_restrictions(tmp_path) -> None:
    # Given a skill that relies on Claude permission enforcement.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\ntools: Read\n---\nRead files.\n",
        encoding="utf-8",
    )
    # When converted, then permissions are not silently replaced by advisory instructions.
    result = run_cli(
        tmp_path,
        "migrate",
        "--source",
        str(source),
        "--target",
        "pi",
        "--output",
        str(tmp_path / "output"),
        "--dry-run",
        "--json",
    )
    assert any(item["status"] == "blocked" for item in result["components"])
    assert result["files"] == []


def test_user_selects_one_skill_without_exporting_siblings(tmp_path) -> None:
    # Given two skills in one plugin.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("one", "two"):
        (source / "skills" / name).mkdir(parents=True)
        (source / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Check\n---\nCheck text.\n",
            encoding="utf-8",
        )
    # When only one component is selected, then its sibling is absent.
    result = run_cli(
        tmp_path,
        "migrate",
        "--source",
        str(source),
        "--target",
        "codex",
        "--item",
        "sample:one",
        "--output",
        str(tmp_path / "output"),
        "--dry-run",
        "--json",
    )
    assert any("sample-one" in path for path in result["files"])
    assert not any("sample-two" in path for path in result["files"])


def test_user_receives_blockers_for_unknown_plugin_components(tmp_path) -> None:
    # Given a plugin with an extension the converter does not understand.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","lspServers":{"custom":{"command":"server"}}}',
        encoding="utf-8",
    )
    # When whole-plugin migration is previewed, then the extension cannot disappear silently.
    result = run_cli(
        tmp_path,
        "migrate",
        "--source",
        str(source),
        "--target",
        "codex",
        "--output",
        str(tmp_path / "output"),
        "--dry-run",
        "--json",
    )
    assert any(item["kind"] == "lspServers" and item["status"] == "blocked" for item in result["components"])


def test_user_rejects_unknown_selected_component(tmp_path) -> None:
    from yi.adapters import preview

    # Given a valid plugin and an item ID that does not exist.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    # When selecting that item, then migration cannot silently create an empty result.
    with pytest.raises(ValueError, match="Unknown selected"):
        preview(source, "codex", ["sample:missing"])


def test_user_selects_components_across_two_plugins(tmp_path) -> None:
    # Given one selected skill in each of two plugins.
    sources = []
    for plugin in ("alpha", "beta"):
        source = tmp_path / plugin
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": plugin}), encoding="utf-8")
        (source / "skills/check").mkdir(parents=True)
        (source / "skills/check/SKILL.md").write_text(
            "---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8"
        )
        sources.extend(["--source", str(source)])
    # When both item IDs are supplied, then both plugin plans are retained.
    result = run_cli(
        tmp_path,
        "migrate",
        *sources,
        "--item",
        "alpha:check",
        "--item",
        "beta:check",
        "--target",
        "pi",
        "--dry-run",
        "--json",
    )
    assert [plan["plugin"] for plan in result["plans"]] == ["alpha", "beta"]


def test_user_preserves_executable_skill_resource_metadata(tmp_path) -> None:
    from yi.adapters import preview

    # Given a portable skill with an executable helper.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nRun ./run.sh.\n", encoding="utf-8")
    script = skill / "run.sh"
    script.write_text("#!/bin/sh\nprintf checked\n", encoding="utf-8")
    script.chmod(0o755)
    # When conversion is planned, then executable metadata is retained for generation and installation.
    report, _files = preview(source, "pi")
    assert "pi/home/.pi/agent/skills/sample-check/run.sh" in report["executables"]


def test_user_gets_namespaced_skill_names_in_target_content(tmp_path) -> None:
    from yi.adapters import preview

    # Given a skill whose source name would collide across plugins.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8"
    )
    # When exported, then frontmatter and directory names agree for native discovery.
    _report, files = preview(source, "codex")
    assert b"name: sample-check" in files["codex/home/.agents/skills/sample-check/SKILL.md"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("private-key.pem", b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic fixture\n"),
        ("notes.txt", b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----\nsynthetic fixture\n"),
        ("credentials.json", b'{"token":"synthetic"}'),
    ],
)
def test_user_cannot_export_sensitive_skill_resources(tmp_path, filename, content) -> None:
    from yi.adapters import preview

    # Given a portable skill containing a credential resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8")
    (skill / filename).write_bytes(content)
    # When preparing files, then credentials are rejected before artifact writes.
    with pytest.raises(ValueError, match="Sensitive"):
        preview(source, "pi")


def test_user_rejects_same_named_plugin_installations(tmp_path) -> None:
    from yi.cli import selections_by_source

    # Given two installations with the same selectable component identity.
    sources = []
    for version in ("one", "two"):
        source = tmp_path / version
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin/plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
        (source / "skills/check").mkdir(parents=True)
        (source / "skills/check/SKILL.md").write_text("Check.", encoding="utf-8")
        sources.append(source)
    # When an ambiguous identity is selected, then neither source is applied.
    with pytest.raises(ValueError, match="Ambiguous"):
        selections_by_source(sources, ["demo:check"])


def test_user_migrates_a_standalone_skill_directory(tmp_path) -> None:
    # Given a skill directory without a plugin manifest.
    source = tmp_path / "check"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nRead notes.txt.\n", encoding="utf-8")
    (source / "notes.txt").write_text("Notes.", encoding="utf-8")
    # When selected directly, then resources are planned under a standalone identity.
    result = run_cli(tmp_path, "migrate", "--source", str(source), "--target", "pi", "--dry-run", "--json")
    assert any(path.endswith("/notes.txt") for path in result["files"])
    assert result["components"][0]["name"] == "check"


def test_user_converts_disabled_mcp_for_opencode(tmp_path) -> None:
    from yi.adapters import preview

    # Given a local MCP declaration without embedded credentials.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {"command": "node", "args": ["server.js"]},
                }
            }
        ),
        encoding="utf-8",
    )
    # When converted, the server is disabled until explicitly reviewed and enabled.
    report, files = preview(source, "opencode-v2")
    configs = [json.loads(content) for name, content in files.items() if name.endswith("opencode.json")]
    assert configs
    assert configs[0]["mcp"]["servers"]["sample-docs"]["disabled"] is True
    assert not any(item["kind"] == "mcp" and item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize("target", ["codex", "opencode-v2"])
@pytest.mark.parametrize("encoding", ["lf", "crlf", "bom-crlf"])
def test_user_converts_plain_agent_to_native_definition(tmp_path, target, encoding) -> None:
    from yi.adapters import preview

    # Given an agent with descriptive metadata and no special tool permissions.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    content = "---\nname: reviewer\ndescription: Review text\n---\nReview the supplied text.\n"
    if "crlf" in encoding:
        content = content.replace("\n", "\r\n")
    (source / "agents/reviewer.md").write_bytes(("\ufeff" if "bom" in encoding else "").encode() + content.encode())
    # When converted, target-native agent content is emitted and remains behavior-unverified.
    report, files = preview(source, target)
    assert any(b"Review the supplied text." in content for content in files.values())
    assert any(item["kind"] == "agent" and item["status"] == "unverified" for item in report["components"])


def test_user_converts_codex_command_hook_declarations_without_trust(tmp_path) -> None:
    from yi.adapters import preview

    # Given a supported command hook that does not depend on plugin-root expansion.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "hooks").mkdir()
    (source / "hooks/hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "printf done", "timeout": 5}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    # When translated, hook trust is never granted and execution remains unverified.
    report, files = preview(source, "codex")
    output = files["codex/home/.codex/hooks.json"]
    assert b"trusted_hash" not in output
    assert json.loads(output)["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "printf done"
    assert any(item["kind"] == "hooks" and item["status"] == "unverified" for item in report["components"])


def test_user_blocks_mcp_url_with_embedded_credentials(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    # Given credentials embedded in a remote URL, they must not enter a Git artifact.
    (source / ".mcp.json").write_text(
        '{"mcpServers":{"docs":{"type":"http","url":"https://user:secret@example.invalid/mcp?token=private"}}}',
        encoding="utf-8",
    )
    report, files = preview(source, "codex")
    assert files == {}
    assert any(item["status"] == "blocked" for item in report["components"])


def test_user_rejects_secrets_inside_agent_prompt(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n" + "-----BEGIN " + "PRIVATE KEY-----\nfixture",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Sensitive"):
        preview(source, "codex")


def test_user_malformed_frontmatter_blocks_only_affected_skill(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, content in (
        ("bad", "---\nname: [broken\n---\nBad."),
        ("good", "---\nname: good\ndescription: Good\n---\nGood."),
    ):
        (source / "skills" / name).mkdir(parents=True)
        (source / "skills" / name / "SKILL.md").write_text(content, encoding="utf-8")
    report, files = preview(source, "pi")
    assert any(item["name"] == "sample:bad" and item["status"] == "blocked" for item in report["components"])
    assert any("sample-good" in name for name in files)


def test_user_blocks_nested_plugin_root_hook_dependencies(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "hooks").mkdir()
    (source / "hooks/hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'ROOT="${CLAUDE_PLUGIN_ROOT:-}"; python "$ROOT/scripts/check.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    report, files = preview(source, "codex")
    assert "codex/home/.codex/hooks.json" not in files
    assert any(item["kind"] == "hooks" and item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize("target", ["opencode-v2", "pi"])
def test_user_migrates_prompt_arguments_to_native_templates(tmp_path, target) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/echo.md").write_text(
        '---\ndescription: Echo arguments\nargument-hint: "[text]"\n---\nReply with $ARGUMENTS and $1.\n',
        encoding="utf-8",
    )
    report, files = preview(source, target)
    assert any(b"$ARGUMENTS" in content for content in files.values())
    assert not any(item["status"] == "blocked" for item in report["components"])


def test_user_cannot_select_ambiguous_standalone_names(tmp_path) -> None:
    from yi.cli import selections_by_source

    sources = []
    for scope in ("user", "project"):
        source = tmp_path / scope / "check"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("Check.", encoding="utf-8")
        sources.append(source)
    with pytest.raises(ValueError, match="Ambiguous"):
        selections_by_source(sources, ["check"])


def test_user_migrates_plain_prompt_to_amp_palette_command(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/review.md").write_text("---\ndescription: Review\n---\nReview $ARGUMENTS.\n", encoding="utf-8")
    report, files = preview(source, "ampcode")
    assert "ampcode/home/.config/amp/plugins/sample-review.js" in files
    assert not any(item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize("source_name", ["ping", "clean_gone"])
def test_user_codex_command_becomes_explicit_only_skill(tmp_path, source_name) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    # Given a command whose source ID may contain an underscore.
    (source / f"commands/{source_name}.md").write_text("---\ndescription: Ping\n---\nReply PONG.\n", encoding="utf-8")
    # When migrated to Codex, use a discoverable native name without changing the source ID.
    report, files = preview(source, "codex")
    native_name = "sample-ping" if source_name == "ping" else "sample-clean-gone"
    destination = f"codex/home/.agents/skills/{native_name}"
    assert b"Reply PONG." in files[f"{destination}/SKILL.md"]
    assert f"name: {native_name}\n".encode() in files[f"{destination}/SKILL.md"]
    assert b"allow_implicit_invocation: false" in files[f"{destination}/agents/openai.yaml"]
    assert set(report["owners"].values()) == {f"sample:{source_name}"}
    assert not any(".codex/prompts" in name for name in files)
    if source_name == "clean_gone":
        assert "sample-clean-gone" in report["components"][0]["reason"]


@pytest.mark.parametrize("other_kind", ["command", "skill"])
def test_user_codex_command_alias_collision_is_rejected(tmp_path, other_kind) -> None:
    from yi.adapters import preview

    # Given distinct source components that would occupy the same native skill directory.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/clean_gone.md").write_text("Reply FIRST.\n", encoding="utf-8")
    if other_kind == "command":
        (source / "commands/clean-gone.md").write_text("Reply SECOND.\n", encoding="utf-8")
    else:
        (source / "skills/clean-gone").mkdir(parents=True)
        (source / "skills/clean-gone/SKILL.md").write_text(
            "---\nname: clean-gone\ndescription: Reply\n---\nReply SECOND.\n", encoding="utf-8"
        )
    # When previewed, fail before returning an ambiguous output plan.
    with pytest.raises(ValueError, match=r"collision.*sample-clean-gone"):
        preview(source, "codex")


def test_user_codex_template_substitution_is_not_silently_changed(tmp_path) -> None:
    from yi.adapters import preview

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/echo.md").write_text("Reply $ARGUMENTS.\n", encoding="utf-8")
    report, files = preview(source, "codex")
    assert not files
    assert report["components"][0]["status"] == "blocked"


def test_user_gets_specific_mcp_blocker_for_target_limit(tmp_path) -> None:
    from yi.adapters import preview

    # Given a valid stdio MCP declaration and an unsupported target release.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".mcp.json").write_text('{"mcpServers":{"receipt":{"command":"fixture-server"}}}', encoding="utf-8")
    # When planning Pi migration, the report distinguishes target support from invalid source data.
    report, files = preview(source, "pi")
    assert not files
    reason = report["components"][0]["reason"]
    assert "Pi" in reason
    assert "native MCP" in reason


def test_user_gets_specific_mcp_blocker_for_credentials(tmp_path) -> None:
    from yi.adapters import preview

    # Given a server with explicit credential-bearing headers.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "type": "http",
                        "url": "https://example.invalid/mcp",
                        "headers": {"Authorization": "fixture-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # When planning conversion, name the rejected field without exposing its value.
    report, files = preview(source, "codex")
    assert not files
    assert "headers" in report["components"][0]["reason"]
    assert "fixture-secret" not in json.dumps(report)


def test_user_codex_agent_preserves_non_bmp_unicode(tmp_path) -> None:
    import tomllib

    from yi.adapters import preview

    # Given an agent whose instructions contain non-BMP Unicode.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/check.md").write_text("---\nname: check\ndescription: Check\n---\nReply 🚀.\n", encoding="utf-8")
    # When converted, a real TOML parser accepts the exact instructions.
    _report, files = preview(source, "codex")
    document = tomllib.loads(files["codex/home/.codex/agents/sample-check.toml"].decode())
    assert document["developer_instructions"] == "Reply 🚀.\n"


def test_user_rejects_colliding_agent_destinations(tmp_path) -> None:
    from yi.adapters import preview

    # Given two agent paths that would produce one destination name.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for folder in ("one", "two"):
        (source / "agents" / folder).mkdir(parents=True)
        (source / "agents" / folder / "review.md").write_text(
            f"---\nname: {folder}\ndescription: Review\n---\n{folder}\n", encoding="utf-8"
        )
    # When planning, neither agent may silently overwrite the other.
    with pytest.raises(ValueError, match="collision"):
        preview(source, "codex")


@pytest.mark.parametrize("path", ["codex/home/.codex/config.toml", "opencode-v2/home/.config/opencode/opencode.json"])
def test_user_shared_mcp_names_cannot_collide(path) -> None:
    from yi.shared_config import combine

    # Given two independent contributions with an identical native MCP name.
    if path.endswith("toml"):
        pieces = ['[mcp_servers."a-b-c"]\ncommand="one"\n', '[mcp_servers."a-b-c"]\ncommand="two"\n']
    else:
        pieces = [json.dumps({"mcp": {"servers": {"a-b-c": {"command": [name]}}}}) for name in ("one", "two")]
    # When composed, neither duplicate tables nor silent last-writer wins are allowed.
    with pytest.raises(ValueError, match="collision"):
        combine(path, pieces)


def test_user_agent_declared_twice_is_converted_once(tmp_path) -> None:
    from yi.adapters import preview

    # Given one agent included by both defaults and an explicit manifest path.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","agents":["./agents/reviewer.md"]}', encoding="utf-8"
    )
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\nReview.\n", encoding="utf-8"
    )
    # When converted, duplicate discovery does not become a collision.
    report, files = preview(source, "codex")
    assert len(files) == 1
    assert len([item for item in report["components"] if item["kind"] == "agent"]) == 1


@pytest.mark.parametrize(
    ("filename", "document"),
    [
        (".mcp.json", {"mcpServers": None}),
        (".mcp.json", []),
        ("hooks/hooks.json", {"hooks": None}),
        ("hooks/hooks.json", []),
    ],
)
def test_user_invalid_configuration_container_gets_json_failure(tmp_path, filename, document) -> None:
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given invalid external configuration types.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    path = source / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    # When previewed, report failure as JSON without an uncaught traceback.
    result = subprocess.run(  # noqa: S603 - Fixed helper and isolated malformed input.
        [sys.executable, str(ENTRY), "migrate", "--source", str(source), "--target", "codex", "--dry-run", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["stage"] == "preview"
    assert "Traceback" not in result.stderr


def test_user_migrates_descriptive_skill_version_without_losing_metadata(tmp_path) -> None:
    from yi.adapters import preview

    # Given portable instructions with a descriptive version field.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\nversion: 1.2.3\n---\nCheck the input.\n", encoding="utf-8"
    )
    # When converted, descriptive version metadata must not block the whole skill.
    report, files = preview(source, "pi")
    assert files
    assert not any(item["status"] == "blocked" for item in report["components"])
    assert b"1.2.3" in next(content for path, content in files.items() if path.endswith("SKILL.md"))


def test_user_migrates_registered_source_without_local_manifest(tmp_path) -> None:
    # Given a cached source defined by a registered marketplace entry.
    root = tmp_path / "claude"
    source = tmp_path / "cached"
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8"
    )
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin/marketplace.json").write_text(
        '{"plugins":[{"name":"sample","source":"./sample","strict":false}]}', encoding="utf-8"
    )
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"sample@local": [{"installPath": str(source)}]}}), encoding="utf-8"
    )
    (root / "plugins/known_marketplaces.json").write_text(
        json.dumps({"local": {"installLocation": str(market)}}), encoding="utf-8"
    )
    # When explicitly resolving registry metadata, no source files need modification.
    result = run_cli(
        tmp_path, "migrate", "--source", str(source), "--claude-dir", str(root), "--target", "pi", "--dry-run", "--json"
    )
    assert result["plugin"] == "sample"
    assert any(path.endswith("SKILL.md") for path in result["files"])
    assert not (source / ".claude-plugin").exists()


def test_user_migrates_preapproval_metadata_without_granting_target_permissions(tmp_path) -> None:
    from yi.adapters import preview

    # Given a skill with source-only tool preapproval, not an exclusive tool restriction.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\nallowed-tools: Bash(fixture *)\n---\nCheck the input.\n",
        encoding="utf-8",
    )
    # When migrated, retain target permission prompts and report the lost preapproval.
    report, files = preview(source, "pi")
    assert files
    assert "preapproval" in report["components"][0]["reason"]
    assert b"allowed-tools" not in next(content for path, content in files.items() if path.endswith("SKILL.md"))


@pytest.mark.parametrize("target", ["codex", "opencode-v2", "pi"])
def test_user_manual_only_skill_keeps_explicit_invocation_policy(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given a skill that must not run implicitly.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\ndisable-model-invocation: true\n---\nCheck.\n", encoding="utf-8"
    )
    # When converted, use the target's actual manual-only field rather than losing the restriction.
    _report, files = preview(source, target)
    assert files
    combined = b"\n".join(files.values())
    field = {
        "codex": b"allow_implicit_invocation: false",
        "opencode-v2": b"opencode/autoinvoke: false",
        "pi": b"disable-model-invocation: true",
    }[target]
    assert field in combined


def test_user_command_preapproval_does_not_block_portable_prompt(tmp_path) -> None:
    from yi.adapters import preview

    # Given a reusable prompt with Claude-only tool preapproval.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text(
        "---\ndescription: Check\nallowed-tools: Read\n---\nReview the supplied text.\n", encoding="utf-8"
    )
    # When converted, no target permission is granted and the prompt remains available.
    report, files = preview(source, "pi")
    assert files
    assert b"allowed-tools" not in next(iter(files.values()))
    assert "preapproval" in report["components"][0]["reason"]


@pytest.mark.parametrize("target", ["codex", "opencode-v2"])
def test_user_mcp_bearer_reference_stays_unresolved_in_artifacts(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given a remote MCP server that references an environment variable.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "api": {
                        "type": "http",
                        "url": "https://example.invalid/mcp",
                        "headers": {"Authorization": "Bearer ${SAMPLE_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # When converted, use the native environment reference and leave the server disabled.
    report, files = preview(source, target)
    assert files
    content = b"\n".join(files.values())
    assert b"SAMPLE_TOKEN" in content
    assert not any(item["status"] == "blocked" for item in report["components"])
    if target == "codex":
        assert b'bearer_token_env_var = "SAMPLE_TOKEN"' in content
        assert b"enabled = false" in content
    else:
        assert b"Bearer {env:SAMPLE_TOKEN}" in content
        assert b'"disabled": true' in content


def test_user_context7_attribution_query_is_preserved_without_general_secret_queries(tmp_path) -> None:
    from yi.adapters import preview

    # Given the exact documented Context7 attribution URL and optional environment-backed authentication.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"context7"}', encoding="utf-8")
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "context7": {
                        "type": "http",
                        "url": "https://mcp.context7.com/mcp?client=claude-code-plugin",
                        "headers": {"Authorization": "${CONTEXT7_API_KEY:-}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # When planned, retain the fixed query and credential reference without resolving the environment.
    _report, files = preview(source, "codex")
    assert files
    content = next(iter(files.values()))
    assert b"client=claude-code-plugin" in content
    assert b"CONTEXT7_API_KEY" in content


def test_user_literal_email_and_shell_example_do_not_block_prompt_copy(tmp_path) -> None:
    from yi.adapters import preview

    # Given ordinary prose with an email address and a non-executed shell example.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    text = "Contact dev@example.invalid. Do not use `$(git rev-parse HEAD)` in a rendered URL.\n"
    (source / "commands/check.md").write_text(text, encoding="utf-8")
    # When converted, literal prose must not be mistaken for attachment or template execution.
    _report, files = preview(source, "pi")
    assert files
    assert text.encode() in next(iter(files.values()))


def test_user_lsp_blocker_names_target_runtime_limit(tmp_path) -> None:
    from yi.adapters import preview

    # Given a valid language-server definition from marketplace metadata.
    source = tmp_path / "source"
    source.mkdir()
    manifest = {
        "name": "gopls-lsp",
        "lspServers": {"gopls": {"command": "gopls", "extensionToLanguage": {".go": "go"}}},
    }
    # When the target cannot run native LSP, retain the protocol distinction and actionable boundary.
    report, files = preview(source, "opencode-v2", manifest=manifest)
    assert not files
    reason = report["components"][0]["reason"]
    assert "LSP runtime" in reason
    assert "2.0.19" in reason


def test_user_sibling_skill_reference_survives_namespacing(tmp_path) -> None:
    from yi.adapters import preview

    # Given two skills with a relative cross-skill reference.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, body in (("first", "Read ../second/SKILL.md."), ("second", "Second guidance.")):
        (source / "skills" / name).mkdir(parents=True)
        (source / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Guide\n---\n{body}\n", encoding="utf-8"
        )
    # When namespaced, the reference must resolve to the generated sibling directory.
    _report, files = preview(source, "pi")
    assert b"../sample-second/SKILL.md" in files["pi/home/.pi/agent/skills/sample-first/SKILL.md"]


def test_user_codex_manual_policy_preserves_existing_sidecar(tmp_path) -> None:
    import yaml

    from yi.adapters import preview

    # Given a manual-only skill with existing Codex interface metadata.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    (skill / "agents").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: check\ndescription: Check\ndisable-model-invocation: true\n---\nCheck.\n", encoding="utf-8"
    )
    (skill / "agents/openai.yaml").write_text("interface:\n  display_name: Existing name\n", encoding="utf-8")
    # When adding the manual policy, retain the existing native metadata.
    _report, files = preview(source, "codex")
    policy = yaml.safe_load(files["codex/home/.agents/skills/sample-check/agents/openai.yaml"])
    assert policy["interface"]["display_name"] == "Existing name"
    assert policy["policy"]["allow_implicit_invocation"] is False


def test_user_sibling_link_is_rewritten_only_once(tmp_path) -> None:
    from yi.adapters import preview

    # Given a source name that matches another skill's generated name.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, body in (("first", "Read ../second/SKILL.md."), ("second", "Second."), ("sample-second", "Other.")):
        (source / "skills" / name).mkdir(parents=True)
        (source / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Check\n---\n{body}\n", encoding="utf-8"
        )
    # When rewriting, the original link must resolve to second, never sample-second.
    _report, files = preview(source, "pi")
    body = files["pi/home/.pi/agent/skills/sample-first/SKILL.md"]
    assert b"../sample-second/SKILL.md" in body
    assert b"../sample-sample-second/SKILL.md" not in body


def test_user_portable_resource_with_claude_runtime_dependency_is_not_silent(tmp_path) -> None:
    from yi.adapters import preview

    # Given portable instructions whose bundled evaluator still launches Claude Code.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: check\ndescription: Check\n---\nRun evaluator.py when evaluating.\n", encoding="utf-8"
    )
    (skill / "evaluator.py").write_text(
        'import subprocess\nsubprocess.run(["claude", "-p", "check"])\n', encoding="utf-8"
    )
    # When migrated, preserve resources but expose the runtime dependency as a component blocker.
    report, files = preview(source, "pi")
    assert any(path.endswith("evaluator.py") for path in files)
    assert any(item["status"] == "blocked" and "Claude" in item["reason"] for item in report["components"])


def test_user_opencode_agent_inherits_model_without_literal_alias(tmp_path) -> None:
    import yaml

    from yi.adapters import preview

    # Given an agent that explicitly inherits the active model and has descriptive color metadata.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\nmodel: inherit\ncolor: blue\n---\nReview the supplied text.\n",
        encoding="utf-8",
    )
    # When converted, native model inheritance remains implicit and no invalid model alias is emitted.
    report, files = preview(source, "opencode-v2")
    assert files
    content = next(iter(files.values())).decode()
    metadata = yaml.safe_load(content.split("---", 2)[1])
    assert "model" not in metadata
    assert metadata["color"] == "#0000ff"
    assert not any(item["status"] == "blocked" for item in report["components"])


def test_user_nested_skill_resource_keeps_its_local_reference(tmp_path) -> None:
    from yi.adapters import preview

    # Given a nested document that references a local resource, not a top-level sibling.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Guide\n---\nGuide.\n", encoding="utf-8")
    first = source / "skills/first"
    (first / "references").mkdir()
    (first / "second").mkdir()
    (first / "references/SKILL.md").write_text("Read ../second/SKILL.md.\n", encoding="utf-8")
    (first / "second/SKILL.md").write_text("Local reference.\n", encoding="utf-8")
    # When exported, preserve the nested document's valid local link.
    _report, files = preview(source, "pi")
    assert files["pi/home/.pi/agent/skills/sample-first/references/SKILL.md"] == b"Read ../second/SKILL.md.\n"


def test_user_missing_manifest_name_returns_structured_failure(tmp_path) -> None:
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a manifest with no required source identity.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text("{}", encoding="utf-8")
    # When previewed, an invalid lookup cannot escape as a traceback.
    result = subprocess.run(  # noqa: S603 - Fixed helper and isolated invalid input.
        [sys.executable, str(ENTRY), "migrate", "--source", str(source), "--target", "pi", "--dry-run", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Traceback" not in result.stderr


def test_user_explicit_model_mapping_controls_agent_conversion(tmp_path) -> None:
    # Given a source agent whose model alias cannot be inferred by the converter.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\nmodel: opus\n---\nReview.\n", encoding="utf-8"
    )
    run_cli(tmp_path, "config", "--target", "opencode-v2", "--model-map", "opus=anthropic/claude-opus-5-5", "--json")
    # When explicitly mapped, the report records the selected destination model and emits the agent.
    result = run_cli(tmp_path, "migrate", "--source", str(source), "--target", "opencode-v2", "--dry-run", "--json")
    assert any(path.endswith("sample-reviewer.md") for path in result["files"])
    agent = next(item for item in result["components"] if item["kind"] == "agent")
    assert agent["source_model"] == "opus"
    assert agent["target_model"] == "anthropic/claude-opus-5-5"


def test_user_invalid_agent_model_type_blocks_only_that_component(tmp_path) -> None:
    from yi.adapters import preview

    # Given an invalid agent model list beside a valid portable skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/bad.md").write_text(
        "---\nname: bad\ndescription: Bad\nmodel: [opus]\n---\nReview.\n", encoding="utf-8"
    )
    (source / "skills/good").mkdir(parents=True)
    (source / "skills/good/SKILL.md").write_text("---\nname: good\ndescription: Good\n---\nGuide.\n", encoding="utf-8")
    # When model mapping is available, malformed source metadata cannot abort unrelated conversion.
    report, files = preview(source, "codex", model_mapping={"opus": "fixture-model"})
    assert any(path.endswith("SKILL.md") for path in files)
    assert any(item["kind"] == "agent" and item["status"] == "blocked" for item in report["components"])


def test_user_missing_yaml_dependency_returns_migration_guidance(tmp_path) -> None:
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a plain command that reaches YAML serialization without parsing frontmatter.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text("Review this text.\n", encoding="utf-8")
    # When Python has no site packages, emit a controlled failure with dependency setup guidance.
    result = subprocess.run(  # noqa: S603 - Fixed interpreter and isolated source; -S excludes optional packages.
        [sys.executable, "-S", str(ENTRY), "migrate", "--source", str(source), "--target", "pi", "--dry-run", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert "PyYAML" in report["error"]
    assert "Traceback" not in result.stderr


def test_user_hook_configuration_fifo_is_rejected_before_read(tmp_path) -> None:
    import os
    import subprocess
    import sys

    from tests.test_usage import ENTRY

    # Given a normal plugin whose hook configuration is a FIFO.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "hooks").mkdir()
    os.mkfifo(source / "hooks/hooks.json")
    # When previewed, reject the resource before a blocking read.
    result = subprocess.run(  # noqa: S603 - Fixed helper and isolated FIFO input.
        [sys.executable, str(ENTRY), "migrate", "--source", str(source), "--target", "codex", "--dry-run", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    assert result.returncode != 0
    assert "regular" in json.loads(result.stdout)["error"]


def test_user_blocked_sibling_keeps_independent_skills_in_preview(tmp_path) -> None:
    from yi.adapters import preview

    # Given a portable skill that depends on an incompatible sibling and another independent skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, extra, body in (
        ("first", "", "Read ../second/SKILL.md."),
        ("second", "tools: Read\n", "Restricted."),
        ("independent", "", "Independent."),
    ):
        skill = source / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Check\n{extra}---\n{body}\n", encoding="utf-8"
        )
    # When the whole plugin is planned, block the dependent skill without losing independent conversion.
    report, files = preview(source, "pi")
    assert "pi/home/.pi/agent/skills/sample-independent/SKILL.md" in files
    assert "pi/home/.pi/agent/skills/sample-first/SKILL.md" not in files
    assert any(item["name"] == "sample:first" and item["status"] == "blocked" for item in report["components"])


def test_user_array_declarations_are_not_silently_omitted(tmp_path) -> None:
    from yi.adapters import preview

    # Given explicitly declared arrays instead of conventional configuration paths.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","mcpServers":["configs/mcp.json"],"hooks":["configs/hooks.json"]}',
        encoding="utf-8",
    )
    (source / "configs").mkdir()
    (source / "configs/mcp.json").write_text('{"mcpServers":{"docs":{"command":"fixture"}}}', encoding="utf-8")
    (source / "configs/hooks.json").write_text(
        '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"printf fixture"}]}]}}', encoding="utf-8"
    )
    # When converted, each declared resource contributes to the target plan.
    report, files = preview(source, "codex")
    assert "codex/home/.codex/config.toml" in files
    assert "codex/home/.codex/hooks.json" in files
    assert {item["kind"] for item in report["components"]} >= {"mcp", "hooks"}


def test_user_missing_explicit_configuration_path_is_reported(tmp_path) -> None:
    from yi.adapters import preview

    # Given a declared resource that does not exist.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","mcpServers":"configs/missing.json"}', encoding="utf-8"
    )
    # When planning, a missing explicit declaration must not disappear as an absent optional component.
    with pytest.raises(ValueError, match="missing"):
        preview(source, "codex")


def test_user_inline_mcp_server_can_use_wrapper_like_name(tmp_path) -> None:
    from yi.adapters import preview

    # Given an inline server collection containing a server named mcpServers.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","mcpServers":{"mcpServers":{"command":"fixture"}}}',
        encoding="utf-8",
    )
    # When converted, inline collections are not confused with file-document wrappers.
    _report, files = preview(source, "codex")
    assert b"sample-mcpServers" in files["codex/home/.codex/config.toml"]


def test_user_removed_dependency_skill_has_no_stale_resource_diagnostic(tmp_path) -> None:
    from yi.adapters import preview

    # Given a skill with a host runtime dependency and an incompatible sibling.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, extra, body in (("first", "", "Read ../second/SKILL.md."), ("second", "tools: Read\n", "Restricted.")):
        skill = source / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Guide\n{extra}---\n{body}\n", encoding="utf-8"
        )
    (source / "skills/first/run.sh").write_text('claude -p "check"\n', encoding="utf-8")
    # When dependency closure removes first, only its dependency-block explanation remains.
    report, files = preview(source, "pi")
    assert not files
    assert not any(item["kind"] == "runtime-dependency" for item in report["components"])
    assert any(item["name"] == "sample:first" and item["kind"] == "skill-dependency" for item in report["components"])


def test_user_invalid_generated_skill_name_blocks_only_that_skill(tmp_path) -> None:
    from yi.adapters import preview

    # Given a valid source skill whose plugin prefix would exceed native name limits.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "p" * 50}), encoding="utf-8")
    skill = source / "skills" / ("s" * 20)
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: source\ndescription: Guide\n---\nGuide.\n", encoding="utf-8")
    # When converted, do not emit a skill that target discovery rejects.
    report, files = preview(source, "pi")
    assert not files
    assert report["components"][0]["status"] == "blocked"
    assert "name" in report["components"][0]["reason"].lower()


def test_user_shared_json_contribution_requires_object_container() -> None:
    from yi.shared_config import combine

    # Given persisted contribution text that is valid JSON but not a native config object.
    with pytest.raises(TypeError, match="object"):
        combine("opencode-v2/home/.config/opencode/opencode.json", ["[]"])


@pytest.mark.parametrize("description", [None, 7, "", "   "])
def test_user_invalid_skill_description_blocks_output(tmp_path, description) -> None:
    from yi.adapters import preview

    # Given frontmatter without a usable discovery description.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check"
    skill.mkdir(parents=True)
    header = "name: check\n" if description is None else "name: check\ndescription: " + json.dumps(description) + "\n"
    (skill / "SKILL.md").write_text("---\n" + header + "---\nCheck.\n", encoding="utf-8")
    # When converted, do not advertise an invalid native skill as available.
    report, files = preview(source, "pi")
    assert not files
    assert report["components"][0]["status"] == "blocked"


@pytest.mark.parametrize(
    ("kind", "target", "content", "detail"),
    [
        ("command", "pi", "Inspect !`git status`.\n", "inline-context-execution"),
        ("command", "codex", "Inspect $ARGUMENTS.\n", "native-arguments-unavailable"),
        ("command", "pi", "Inspect ${CLAUDE_PLUGIN_ROOT}/scripts/check.py.\n", "plugin-root-reference"),
        ("command", "pi", "---\nmodel: opus\n---\nInspect.\n", "model"),
        ("command", "pi", "---\n123: value\nmodel: opus\n---\nInspect.\n", "frontmatter-keys"),
        ("skill", "pi", "---\n123: value\ndescription: Inspect\n---\nInspect.\n", "frontmatter-keys"),
        ("skill", "pi", "---\ndescription: Inspect\ndisallowed-tools: Bash\n---\nInspect.\n", "disallowed-tools"),
        ("skill", "pi", "---\ndescription: 123\n---\nInspect.\n", "description"),
        (
            "skill",
            "ampcode",
            "---\ndescription: Inspect\ndisable-model-invocation: true\n---\nInspect.\n",
            "manual-invocation-policy-unavailable",
        ),
        ("skill", "pi", "---\ndescription: Inspect\n---\nInspect !`git status`.\n", "inline-context-execution"),
    ],
)
def test_user_gets_actionable_prompt_blocker(tmp_path, kind, target, content, detail) -> None:
    from yi.adapters import preview

    # Given a component with one unsupported requirement and an independent portable component.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    path = source / ("skills/check/SKILL.md" if kind == "skill" else "commands/check.md")
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    portable = source / "skills/portable/SKILL.md"
    portable.parent.mkdir(parents=True)
    portable.write_text("---\ndescription: Portable\n---\nRead text.\n", encoding="utf-8")
    # When previewed, isolate the blocker and name its source, exact requirement, and next action.
    report, files = preview(source, target)
    blocked = next(item for item in report["components"] if item["name"] == "sample:check")
    assert blocked["status"] == "blocked"
    assert str(path) in blocked["reason"]
    assert detail in blocked["reason"]
    assert "Adapt" in blocked["reason"]
    assert files
    assert set(report["owners"].values()) == {"sample:portable"}


def test_user_blocked_command_alias_does_not_prevent_portable_sibling(tmp_path) -> None:
    from yi.adapters import preview

    # Given a portable command and a colliding source with unsupported execution semantics.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/clean-gone.md").write_text("Reply PORTABLE.\n", encoding="utf-8")
    (source / "commands/clean_gone.md").write_text("---\nmodel: opus\n---\nReply BLOCKED.\n", encoding="utf-8")
    # When previewed together, only actual outputs participate in collision detection.
    report, files = preview(source, "codex")
    assert b"Reply PORTABLE." in files["codex/home/.agents/skills/sample-clean-gone/SKILL.md"]
    assert set(report["owners"].values()) == {"sample:clean-gone"}
    assert any(item["name"] == "sample:clean_gone" and item["status"] == "blocked" for item in report["components"])


def test_user_command_alias_cannot_impersonate_blocked_skill_dependency(tmp_path) -> None:
    from yi.adapters import preview

    # Given a blocked skill, its dependent, and an independent command with the same native destination.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, extra, body in (
        ("clean-gone", "model: opus\n", "Required skill behavior."),
        ("needs", "", "Read ../clean-gone/SKILL.md."),
    ):
        path = source / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\ndescription: Guide\n{extra}---\n{body}\n", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/clean_gone.md").write_text("Reply COMMAND instead.\n", encoding="utf-8")
    # When dependencies resolve, an unrelated command is not proof that the required skill exists.
    report, files = preview(source, "codex")
    assert b"Reply COMMAND instead." in files["codex/home/.agents/skills/sample-clean-gone/SKILL.md"]
    assert "codex/home/.agents/skills/sample-needs/SKILL.md" not in files
    assert set(report["owners"].values()) == {"sample:clean_gone"}
    assert any(item["name"] == "sample:needs" and item["kind"] == "skill-dependency" for item in report["components"])


@pytest.mark.parametrize("target", ["codex", "ampcode", "pi", "opencode-v2"])
def test_user_literal_shell_recipe_is_preserved_or_explicitly_blocked(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given a non-executed recipe with locally defined variables and literal awk fields.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    body = (
        "```bash\nwhile read branch; do\n"
        "  worktree=$(printf '%s' \"$branch\" | awk '{print $1}')\n"
        '  echo "$worktree"\ndone\n```\n'
    )
    (source / "commands/clean_gone.md").write_text("---\ndescription: Inspect recipe\n---\n" + body, encoding="utf-8")
    # When migrated, only non-template target surfaces can preserve the literal recipe.
    report, files = preview(source, target)
    if target in {"pi", "opencode-v2"}:
        assert not files
        assert "literal-template-collision" in report["components"][0]["reason"]
    elif target == "codex":
        assert files["codex/home/.agents/skills/sample-clean-gone/SKILL.md"].endswith(body.encode())
    else:
        # The generated JavaScript string must decode to the unchanged user message.
        text = files["ampcode/home/.config/amp/plugins/sample-clean_gone.js"].decode()
        content = text.split("const content = ", 1)[1].split(";\n", 1)[0]
        assert json.loads(content) == body
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "body",
    [
        "```bash\nworktree=local\necho $ARGUMENTS\n```\n",
        "```bash\nworktree=local\necho $unknown\n```\n",
        "```bash\nworktree=local\n```\nUse $worktree outside its recipe.\n",
        "```bash\nworktree=local\necho $1\n```\n",
        "```bash\n# while read unknown; do\necho $unknown\n```\n",
        '```bash\necho "while read unknown"\necho $unknown\n```\n',
        "```bash\nunknown=local true\necho $unknown\n```\n",
        "```bash\nunknown=$(printf x) true $(printf y)\necho $unknown\n```\n",
        "```bash\ncat <<EOF\nunknown=local\nEOF\necho $unknown\n```\n",
        "```bash\nwhile read branch-name; do :; done\necho $branch\n```\n",
        '```bash\nprintf "%s" "\nunknown=local\n"\necho $unknown\n```\n',
        "```bash\nprintf \"awk '{print $1}'\"\n```\n",
        "```bash\nawk '{print $1}'\nprintf \"awk '{print $1}'\"\n```\n",
        "```bash\nworktree=local\n````\nUse $worktree outside.\n```text\nExample.\n```\n",
    ],
)
def test_user_literal_recipe_exemption_does_not_hide_real_template_requirements(tmp_path, body) -> None:
    from yi.adapters import preview

    # Given a recipe with a genuine or unproven placeholder, not a demonstrable local reference.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text(body, encoding="utf-8")
    # When migrated to a Codex skill, do not pretend native argument binding is available.
    report, files = preview(source, "codex")
    assert not files
    assert report["components"][0]["status"] == "blocked"


def test_user_amp_arguments_prefix_is_not_mistaken_for_literal_local_variable(tmp_path) -> None:
    from yi.adapters import preview

    # Given a local variable name colliding with Amp's source argument placeholder prefix.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text(
        "```bash\nARGUMENTS_SUFFIX=local\necho $ARGUMENTS_SUFFIX\n```\n", encoding="utf-8"
    )
    # When mapped to an Amp message, block rather than opening a dialog that changes its literal name.
    report, files = preview(source, "ampcode")
    assert not files
    assert report["components"][0]["status"] == "blocked"


@pytest.mark.parametrize("encoding", ["crlf", "bom", "bom-crlf"])
@pytest.mark.parametrize("kind", ["command", "skill"])
def test_user_prompt_encoding_cannot_bypass_frontmatter_requirements(tmp_path, encoding, kind) -> None:
    from yi.adapters import preview

    # Given encoded frontmatter with an unsupported model requirement.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    path = source / ("skills/check/SKILL.md" if kind == "skill" else "commands/check.md")
    path.parent.mkdir(parents=True)
    content = "---\ndescription: Inspect\nmodel: opus\n---\nInspect.\n"
    if "crlf" in encoding:
        content = content.replace("\n", "\r\n")
    path.write_bytes(("\ufeff" if "bom" in encoding else "").encode() + content.encode())
    # When converted, unsupported metadata stays blocked and names the actual rejected field.
    report, files = preview(source, "codex")
    assert not files
    assert report["components"][0]["status"] == "blocked"
    assert "model" in report["components"][0]["reason"]


@pytest.mark.parametrize("encoding", ["crlf", "bom", "bom-crlf"])
def test_user_encoded_manual_skill_retains_explicit_codex_policy(tmp_path, encoding) -> None:
    from yi.adapters import preview

    # Given a portable manual-only skill saved by an editor using BOM or Windows line endings.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/check/SKILL.md"
    skill.parent.mkdir(parents=True)
    content = "---\ndescription: Inspect\ndisable-model-invocation: true\n---\nInspect.\n"
    if "crlf" in encoding:
        content = content.replace("\n", "\r\n")
    skill.write_bytes(("\ufeff" if "bom" in encoding else "").encode() + content.encode())
    # When converted, parsed source policy must also control the emitted sidecar.
    _report, files = preview(source, "codex")
    assert files["codex/home/.agents/skills/sample-check/SKILL.md"].endswith(b"Inspect.\n")
    assert b"allow_implicit_invocation: false" in files["codex/home/.agents/skills/sample-check/agents/openai.yaml"]


@pytest.mark.parametrize("description", ['description: ""\n', "description: null\n", "description: '   '\n"])
def test_user_codex_command_without_usable_description_gets_native_fallback(tmp_path, description) -> None:
    import yaml

    from yi.adapters import preview

    # Given a plain command with an explicit empty description that Codex cannot discover as a skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text("---\n" + description + "---\nReply RECEIPT.\n", encoding="utf-8")
    # When mapped to a native skill, supply a useful fallback while preserving command instructions.
    _report, files = preview(source, "codex")
    text = files["codex/home/.agents/skills/sample-check/SKILL.md"].decode()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["description"] == "Run check explicitly."
    assert text.endswith("Reply RECEIPT.\n")


@pytest.mark.parametrize("target", ["codex", "opencode-v2"])
def test_user_agent_runtime_root_dependency_is_not_silently_dropped(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given an agent whose required resource uses the source harness plugin root.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\ndescription: Review\n---\nRead ${CLAUDE_PLUGIN_ROOT}/scripts/check.py.\n", encoding="utf-8"
    )
    (source / "agents/portable.md").write_text("---\ndescription: Portable\n---\nReview text.\n", encoding="utf-8")
    # When planned, isolate the dependency blocker rather than generate a broken native agent.
    report, files = preview(source, target)
    blocked = next(item for item in report["components"] if item["name"] == "sample:agent:reviewer")
    assert blocked["status"] == "blocked"
    assert "plugin-root-reference" in blocked["reason"]
    assert not any("sample-reviewer" in path for path in files)
    assert any("sample-portable" in path for path in files)


@pytest.mark.parametrize("target", ["ampcode", "codex", "opencode-v2", "pi"])
def test_user_command_with_blocked_required_skill_is_not_generated(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given an explicit load directive and an incompatible required skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/rules/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Rules\ntools: Read\n---\nRequired guidance.\n", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/configure.md").write_text(
        "**Load sample:rules skill first** to understand rules.\n", encoding="utf-8"
    )
    (source / "commands/independent.md").write_text("Reply INDEPENDENT.\n", encoding="utf-8")
    # When previewed, prune dependent command outputs while preserving independent components.
    report, files = preview(source, target)
    assert not any("sample-configure" in path for path in files)
    assert any("sample-independent" in path for path in files)
    assert any(item["name"] == "sample:configure" and item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize("target", ["codex", "opencode-v2", "pi"])
@pytest.mark.parametrize("emphasis", [False, True])
def test_user_explicit_command_dependency_requires_selected_native_skill(tmp_path, target, emphasis) -> None:
    from yi.adapters import preview

    # Given a command requiring a portable source skill and a source-specific Skill-tool directive.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/rules/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Rules\n---\nRead rules.\n", encoding="utf-8")
    (source / "commands").mkdir()
    command = source / "commands/configure.md"
    directive = (
        "Load the **sample:rules** skill using the Skill tool to understand rules.\n"
        if emphasis
        else "**FIRST: Load the sample:rules skill** using the Skill tool to understand rules.\n"
    )
    command.write_text(directive, encoding="utf-8")
    # When the skill is unselected, request explicit selection rather than expanding scope.
    with pytest.raises(ValueError, match=r"Missing skill dependencies.*sample:rules"):
        preview(source, target, ["sample:configure"])
    # When both are selected, the command points to the actual target HOME skill, not the Claude tool.
    report, files = preview(source, target, ["sample:configure", "sample:rules"])
    assert any(item["name"] == "sample:configure" and item["kind"] == "command" for item in report["components"])
    content = next(
        content
        for path, content in files.items()
        if report["owners"][path] == "sample:configure" and path.endswith(".md")
    )
    assert b"sample-rules/SKILL.md" in content
    assert b"using the Skill tool" not in content
    assert b"target HOME" in content


@pytest.mark.parametrize("target", ["codex", "ampcode", "opencode-v2", "pi"])
def test_user_plugin_document_examples_remain_inactive_target_home_resources(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given an exact documentary examples pointer and an inert rule example with enabled metadata.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/help.md").write_text("See `${CLAUDE_PLUGIN_ROOT}/examples/` for examples.\n", encoding="utf-8")
    (source / "examples").mkdir()
    content = b"---\nenabled: true\nevent: bash\n---\nINERT-EXAMPLE-RECEIPT\n"
    (source / "examples/rule.local.md").write_bytes(content)
    # When exported, retain inert bytes outside rule/skill discovery and point to destination HOME.
    report, files = preview(source, target)
    resource = f"{target}/home/.local/share/yi/resources/sample/command/help/examples/rule.local.md"
    assert files[resource] == content
    assert report["owners"][resource] == "sample:help"
    assert not report["executables"]
    assert not any(".claude/hookify" in path for path in files)
    assert any(b"~/.local/share/yi/resources/sample/command/help/examples/" in content for content in files.values())
    assert not any(b"CLAUDE_PLUGIN_ROOT" in content for content in files.values())


@pytest.mark.parametrize("resource_kind", ["executable", "symlink", "runtime"])
def test_user_document_example_relocation_does_not_export_unsafe_resources(tmp_path, resource_kind) -> None:
    from yi.adapters import preview

    # Given a documentary directory containing a non-inert or indirect resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/help.md").write_text("See `${CLAUDE_PLUGIN_ROOT}/examples/`.\n", encoding="utf-8")
    (source / "examples").mkdir()
    resource = source / "examples/note.md"
    if resource_kind == "symlink":
        outside = tmp_path / "private.md"
        outside.write_text("Private fixture.", encoding="utf-8")
        resource.symlink_to(outside)
    elif resource_kind == "runtime":
        (source / "examples/run.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    else:
        resource.write_text("Executable fixture.\n", encoding="utf-8")
        resource.chmod(0o755)
    # When planned, block the referencing component rather than partially copy the unsafe directory.
    report, files = preview(source, "codex")
    assert not files
    assert report["components"][0]["status"] == "blocked"


@pytest.mark.parametrize("target", ["codex", "ampcode", "opencode-v2", "pi"])
def test_user_command_dependency_rewrite_preserves_document_examples(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given a real load directive plus the same text inside a fence and inert bundled example.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/rules/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Rules\n---\nRequired.\n", encoding="utf-8")
    (source / "commands").mkdir()
    directive = "Load sample:rules skill"
    (source / "commands/check.md").write_text(
        directive + "\n```text\n" + directive + "\n```\nSee `${CLAUDE_PLUGIN_ROOT}/examples/`.\n", encoding="utf-8"
    )
    (source / "examples").mkdir()
    example = (directive + "\n").encode()
    (source / "examples/note.md").write_bytes(example)
    # When rewritten, only the real directive changes; resources and fenced instructions remain literal.
    _report, files = preview(source, target)
    resource = f"{target}/home/.local/share/yi/resources/sample/command/check/examples/note.md"
    assert files[resource] == example
    assert any(directive.encode() in content for path, content in files.items() if "sample-check" in path)


@pytest.mark.parametrize(
    "body",
    [
        "```text\nLoad sample:rules skill\n```\n",
        "````text\n```\n```\nLoad sample:rules skill\n````\n",
        "```text\n~~~\n~~~\nLoad sample:rules skill\n```\n",
        "Load sample:missing skill\n",
    ],
)
def test_user_documentary_or_missing_command_dependencies_are_distinguished(tmp_path, body) -> None:
    from yi.adapters import preview

    # Given a known skill and either a documentary load example or an unknown local skill directive.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/rules/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Rules\n---\nRequired.\n", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text(body, encoding="utf-8")
    # When selected alone, examples need no closure but a missing real requirement blocks the command.
    report, files = preview(source, "codex", ["sample:check"])
    if body.startswith("```"):
        assert files
        assert any(body.encode() in content for content in files.values())
    else:
        assert not files
        assert any(item["status"] == "blocked" and "sample:missing" in item["reason"] for item in report["components"])


@pytest.mark.parametrize("reverse", [False, True])
def test_user_all_command_dependencies_validate_selection_before_pruning(tmp_path, reverse) -> None:
    from yi.adapters import preview

    # Given a selected blocked dependency and an unselected portable dependency in either directive order.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, extra in (("blocked", "tools: Read\n"), ("rules", "")):
        path = source / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\ndescription: Guide\n{extra}---\nGuide.\n", encoding="utf-8")
    (source / "commands").mkdir()
    lines = ["Load sample:blocked skill", "Load sample:rules skill"]
    (source / "commands/check.md").write_text("\n".join(reversed(lines) if reverse else lines) + "\n", encoding="utf-8")
    # When selected, all missing selection requirements must be reported before incompatible pruning.
    with pytest.raises(ValueError, match=r"Missing skill dependencies.*sample:rules"):
        preview(source, "codex", ["sample:check", "sample:blocked"])


def test_user_blocked_skill_dependency_removes_its_relocated_documents(tmp_path) -> None:
    from yi.adapters import preview

    # Given a source skill owning relocated examples but requiring an incompatible sibling.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name, extra, body in (
        ("needs", "", "Read ../rules/SKILL.md. See `${CLAUDE_PLUGIN_ROOT}/examples/`."),
        ("rules", "tools: Read\n", "Restricted."),
    ):
        path = source / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\ndescription: Guide\n{extra}---\n{body}\n", encoding="utf-8")
    (source / "examples").mkdir()
    (source / "examples/note.md").write_text("Inert example.\n", encoding="utf-8")
    # When closure blocks the skill, remove its entire owner set, not only its discovery directory.
    report, files = preview(source, "pi")
    assert not files
    assert not report["owners"]
    assert any(item["name"] == "sample:needs" and item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize(
    "content",
    [
        "```bash\ncat `${CLAUDE_PLUGIN_ROOT}/examples/`\n```\n",
        "```python\nroot = `${CLAUDE_PLUGIN_ROOT}/examples/`\n```\n",
    ],
)
def test_user_runtime_code_example_root_is_not_rewritten_as_documentary_pointer(tmp_path, content) -> None:
    from yi.adapters import preview

    # Given a source-root expression in an executable-language recipe, not a documentary link.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text(content, encoding="utf-8")
    (source / "examples").mkdir()
    (source / "examples/note.md").write_text("Inert.\n", encoding="utf-8")
    # When previewed, keep runtime-root semantics blocked instead of creating a changed recipe.
    report, files = preview(source, "codex")
    assert not files
    assert report["components"][0]["status"] == "blocked"


def test_user_inert_document_directory_does_not_export_hidden_environment_files(tmp_path) -> None:
    from yi.adapters import preview

    # Given an otherwise inert directory with a hidden environment resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/help.md").write_text("See `${CLAUDE_PLUGIN_ROOT}/examples/`.\n", encoding="utf-8")
    (source / "examples").mkdir()
    (source / "examples/.env.txt").write_text("SYNTHETIC_PRIVATE_CONFIGURATION=yes\n", encoding="utf-8")
    # When planned, refuse the resource rather than publish private configuration as a Markdown/text example.
    with pytest.raises(ValueError, match="Sensitive"):
        preview(source, "codex")


def test_user_dependency_blocked_command_alias_does_not_hide_portable_command(tmp_path) -> None:
    from yi.adapters import preview

    # Given colliding command aliases, only one of which has an unavailable required skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/rules/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Rules\ntools: Read\n---\nRestricted.\n", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/clean-gone.md").write_text("Reply PORTABLE.\n", encoding="utf-8")
    (source / "commands/clean_gone.md").write_text("Load sample:rules skill\n", encoding="utf-8")
    # When dependency pruning precedes output collision checking, preserve the independent command.
    report, files = preview(source, "codex")
    assert b"Reply PORTABLE." in files["codex/home/.agents/skills/sample-clean-gone/SKILL.md"]
    assert set(report["owners"].values()) == {"sample:clean-gone"}
    assert any(item["name"] == "sample:clean_gone" and item["status"] == "blocked" for item in report["components"])


@pytest.mark.parametrize("description", ["{foo: bar}", "[Review]", "true", "17", '"   "'])
@pytest.mark.parametrize("target", ["codex", "opencode-v2"])
def test_user_agent_requires_native_string_description(tmp_path, target, description) -> None:
    from yi.adapters import preview

    # Given an agent whose descriptive metadata violates the native agent string contract.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/check.md").write_text(f"---\ndescription: {description}\n---\nReview text.\n", encoding="utf-8")
    # When planned, report a blocker instead of emitting invalid TOML or non-string native metadata.
    report, files = preview(source, target)
    assert not files
    assert report["components"][0]["status"] == "blocked"


def test_user_command_dependency_cannot_hide_collision_with_its_required_skill(tmp_path) -> None:
    from yi.adapters import preview

    # Given a portable skill and a command alias requiring that skill while occupying the same native path.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    skill = source / "skills/clean-gone/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/clean_gone.md").write_text("Load sample:clean-gone skill\n", encoding="utf-8")
    # When planned, resolve the real skill before checking command overlap; do not misreport it as unavailable.
    with pytest.raises(ValueError, match="collision"):
        preview(source, "codex")


@pytest.mark.parametrize("location", ["command", "skill", "example"])
def test_user_invalid_utf8_blocks_only_the_affected_prompt_component(tmp_path, location) -> None:
    from yi.adapters import preview

    # Given a malformed prompt/example byte stream and an independent valid skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    portable = source / "skills/portable/SKILL.md"
    portable.parent.mkdir(parents=True)
    portable.write_text("---\ndescription: Portable\n---\nRead text.\n", encoding="utf-8")
    if location == "skill":
        bad = source / "skills/bad/SKILL.md"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"---\ndescription: Bad\n---\n\xff")
    else:
        bad = source / "commands/bad.md"
        bad.parent.mkdir(parents=True)
        if location == "command":
            bad.write_bytes(b"Bad \xff")
        else:
            bad.write_text("See `${CLAUDE_PLUGIN_ROOT}/examples/`.\n", encoding="utf-8")
            (source / "examples").mkdir()
            (source / "examples/note.md").write_bytes(b"Bad \xff")
    # When previewed, identify UTF-8 adaptation for that component and retain independent output.
    report, files = preview(source, "codex")
    assert set(report["owners"].values()) == {"sample:portable"}
    assert files
    blocked = next(item for item in report["components"] if item["name"] == "sample:bad")
    assert blocked["status"] == "blocked"
    assert "UTF-8" in blocked["reason"]


@pytest.mark.parametrize("target", ["ampcode", "codex", "opencode-v2", "pi"])
def test_user_namespaced_sibling_script_resources_execute_from_unrelated_cwd(tmp_path, target) -> None:
    import subprocess

    from yi.adapters import preview
    from yi.artifacts import apply

    # Given an extensionless entry script using a real sibling resource and a document link to that resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        skill = source / "skills" / name
        (skill / "scripts").mkdir(parents=True)
        body = "Run ../second/scripts/receipt.\n" if name == "first" else "Provide receipt.\n"
        (skill / "SKILL.md").write_text(f"---\ndescription: Guide\n---\n{body}", encoding="utf-8")
    entry = source / "skills/first/scripts/start"
    entry.write_text('#!/bin/sh\nset -eu\n"$(dirname "$0")/../../second/scripts/receipt"\n', encoding="utf-8")
    receipt = source / "skills/second/scripts/receipt"
    receipt.write_text('#!/bin/sh\nprintf "SIBLING-RESOURCE-RECEIPT\\n"\n', encoding="utf-8")
    entry.chmod(0o755)
    receipt.chmod(0o755)
    original = entry.read_bytes()
    root = tmp_path / "output"
    # When generated, relative resource links resolve inside the target layout without changing source files.
    report, files = preview(source, target)
    apply(root, report, files)
    start = next(root / name for name in files if name.endswith("sample-first/scripts/start"))
    result = subprocess.run([str(start)], cwd=tmp_path, capture_output=True, text=True, check=False)  # noqa: S603 - Task-owned fixed synthetic receipt scripts.
    assert result.returncode == 0, result.stderr
    assert result.stdout == "SIBLING-RESOURCE-RECEIPT\n"
    assert entry.read_bytes() == original
    with pytest.raises(ValueError, match="Missing skill dependencies"):
        preview(source, target, ["sample:first"])


@pytest.mark.parametrize("data", [b"\xff../second/note.txt", b"\x00../second/note.txt"])
def test_user_sibling_relocation_preserves_binary_resources(tmp_path, data) -> None:
    from yi.adapters import preview

    # Given an opaque resource that happens to contain bytes resembling a sibling path.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/blob.bin").write_bytes(data)
    (source / "skills/second/note.txt").write_text("Note.\n", encoding="utf-8")
    # When exported, binary payloads remain byte-identical and are never used to infer executable dependencies.
    _report, files = preview(source, "pi")
    assert files["pi/home/.pi/agent/skills/sample-first/blob.bin"] == data


def test_user_absolute_path_fragments_are_not_sibling_references(tmp_path) -> None:
    from yi.adapters import preview

    # Given a literal absolute path whose tail resembles an otherwise real sibling.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    body = "Do not use /tmp/../second/note.txt or prefix../second/note.txt.\n"
    (source / "skills/first/SKILL.md").write_text("---\ndescription: Guide\n---\n" + body, encoding="utf-8")
    (source / "skills/second/note.txt").write_text("Note.\n", encoding="utf-8")
    # When generated, absolute/prefixed path substrings retain their original meaning.
    _report, files = preview(source, "pi")
    assert files["pi/home/.pi/agent/skills/sample-first/SKILL.md"].endswith(body.encode())


def test_user_unselected_sibling_resource_symlink_cannot_redirect_dependency(tmp_path) -> None:
    from yi.adapters import preview

    # Given an unselected sibling resource symlink pointing at another selected skill.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second", "third"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../second/link/note.txt.\n", encoding="utf-8"
    )
    (source / "skills/third/note.txt").write_text("Note.\n", encoding="utf-8")
    (source / "skills/second/link").symlink_to(source / "skills/third", target_is_directory=True)
    # When following a discovered edge, refuse the symlink rather than relabel it as the third skill.
    with pytest.raises(ValueError, match="symlink"):
        preview(source, "pi", ["sample:first", "sample:third"])


@pytest.mark.parametrize("missing", [False, True])
def test_user_empty_sibling_directory_is_not_reported_as_available_resource(tmp_path, missing) -> None:
    from yi.adapters import preview

    # Given a sibling directory that contributes no generated files.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../second/empty/.\n", encoding="utf-8"
    )
    if not missing:
        (source / "skills/second/empty").mkdir()
    # When planning, the consumer is blocked instead of linking an absent output directory.
    report, files = preview(source, "pi")
    assert "pi/home/.pi/agent/skills/sample-first/SKILL.md" not in files
    assert any(item["name"] == "sample:first" and item["status"] == "blocked" for item in report["components"])


def test_user_sibling_directory_glob_keeps_its_separator(tmp_path) -> None:
    from yi.adapters import preview

    # Given a real cross-skill directory followed by a glob that remains literal.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../second/scripts/*.\n", encoding="utf-8"
    )
    (source / "skills/second/scripts").mkdir()
    (source / "skills/second/scripts/note.txt").write_text("Note.\n", encoding="utf-8")
    # When namespaced, the star stays within the migrated scripts directory, not a new filename prefix.
    _report, files = preview(source, "pi")
    assert b"../sample-second/scripts/*" in files["pi/home/.pi/agent/skills/sample-first/SKILL.md"]


def test_user_sibling_filename_is_not_tracked_by_prefix(tmp_path) -> None:
    from yi.adapters import preview

    # Given two resources sharing a prefix but one filename outside the bounded relocation grammar.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../second/receipt+old.\n", encoding="utf-8"
    )
    for name in ("receipt", "receipt+old"):
        (source / "skills/second" / name).write_text("Receipt.\n", encoding="utf-8")
    # When rewritten, track the complete concrete resource rather than a shared filename prefix.
    report, files = preview(source, "pi")
    assert b"../sample-second/receipt+old" in files["pi/home/.pi/agent/skills/sample-first/SKILL.md"]
    dependencies = next(
        item["dependencies"]
        for item in report["components"]
        if item["name"] == "sample:first" and item["kind"] == "skill-dependency"
    )
    assert dependencies == ["pi/home/.pi/agent/skills/sample-second/receipt+old"]


def test_user_sibling_traversal_cannot_normalize_away_a_source_symlink(tmp_path) -> None:
    from yi.adapters import preview

    # Given a path that enters a symlink and then traverses upwards before reaching an apparent sibling.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "third"):
        path = source / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../../bridge/../skills/third/data.\n", encoding="utf-8"
    )
    (source / "skills/third/data").write_text("Inside.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "skills/third").mkdir(parents=True)
    (outside / "skills/third/data").write_text("Outside.\n", encoding="utf-8")
    (source / "bridge").symlink_to(outside / "deep", target_is_directory=True)
    # When resolving the original path, enforce link review before cancelling dot segments.
    with pytest.raises(ValueError, match="symlink"):
        preview(source, "pi")


def test_user_unsupported_sibling_backup_suffix_blocks_consumer(tmp_path) -> None:
    from yi.adapters import preview

    # Given a backup filename whose supported prefix is another existing resource.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    for name in ("first", "second"):
        path = source / "skills" / name
        (path / "scripts").mkdir(parents=True)
        (path / "SKILL.md").write_text("---\ndescription: Guide\n---\nRead text.\n", encoding="utf-8")
    (source / "skills/first/SKILL.md").write_text(
        "---\ndescription: Guide\n---\nRead ../second/scripts/receipt~.\n", encoding="utf-8"
    )
    for name in ("receipt", "receipt~"):
        (source / "skills/second/scripts" / name).write_text("Receipt.\n", encoding="utf-8")
    # When a token is outside the bounded grammar, refuse the consumer instead of recording a wrong prefix edge.
    report, files = preview(source, "pi")
    assert "pi/home/.pi/agent/skills/sample-first/SKILL.md" not in files
    assert any(item["name"] == "sample:first" and item["status"] == "blocked" for item in report["components"])
