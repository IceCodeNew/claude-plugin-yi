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


def test_user_blocks_claude_specific_skill_frontmatter(tmp_path) -> None:
    # Given a skill that relies on Claude permission enforcement.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check\nallowed-tools: Read\n---\nRead files.\n",
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
def test_user_converts_plain_agent_to_native_definition(tmp_path, target) -> None:
    from yi.adapters import preview

    # Given an agent with descriptive metadata and no special tool permissions.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "agents").mkdir()
    (source / "agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review text\n---\nReview the supplied text.\n", encoding="utf-8"
    )
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


@pytest.mark.parametrize("target", ["opencode-v2", "pi", "codex"])
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
