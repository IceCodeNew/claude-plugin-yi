import json

from tests.test_usage import run_cli


def test_user_discovers_plugin_components_without_execution(tmp_path) -> None:
    # Given a local plugin with a skill and a legacy command.
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (plugin / "skills/check").mkdir(parents=True)
    (plugin / "skills/check/SKILL.md").write_text(
        "---\ndescription: Check things\n---\nDo not execute.\n", encoding="utf-8"
    )
    (plugin / "commands").mkdir()
    (plugin / "commands/build.md").write_text("Build the project.\n", encoding="utf-8")
    # When yi inspects this explicit source.
    items = run_cli(tmp_path, "catalog", "--source", str(plugin), "--json")["items"]
    # Then both distinct components retain their plugin identity.
    assert {(item["name"], item["kind"]) for item in items} == {
        ("demo:check", "skill"),
        ("demo:build", "command"),
    }


def test_user_discovers_custom_component_paths(tmp_path) -> None:
    # Given a plugin whose manifest declares a custom skill directory.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample","skills":"./custom"}', encoding="utf-8")
    (source / "custom/check").mkdir(parents=True)
    (source / "custom/check/SKILL.md").write_text("Check text.", encoding="utf-8")
    # When inspected, then the component is not silently omitted.
    result = run_cli(tmp_path, "catalog", "--source", str(source), "--json")
    assert [item["name"] for item in result["items"]] == ["sample:check"]


def test_user_discovers_installed_plugin_index(tmp_path) -> None:
    # Given an installed-plugin index that points to a local plugin.
    root = tmp_path / "claude"
    source = tmp_path / "plugin"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text("Check text.", encoding="utf-8")
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"sample@local": [{"scope": "user", "installPath": str(source)}]},
            }
        ),
        encoding="utf-8",
    )
    # When the indexed catalog is requested, then the installed skill is available for selection.
    result = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")
    assert [item["name"] for item in result["items"] if item["kind"] == "skill"] == ["sample:check"]


def test_user_cannot_select_ambiguous_component_names(tmp_path) -> None:
    import pytest

    from yi.catalog import discover

    # Given a skill and command sharing one qualified name.
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample"}', encoding="utf-8")
    (source / "skills/check").mkdir(parents=True)
    (source / "skills/check/SKILL.md").write_text("Check.", encoding="utf-8")
    (source / "commands").mkdir()
    (source / "commands/check.md").write_text("Different check.", encoding="utf-8")
    # When the catalog is built, then ambiguous identities cannot broaden selection.
    with pytest.raises(ValueError, match="Ambiguous"):
        discover(source)


def test_user_sees_plugins_without_skills_in_installed_catalog(tmp_path) -> None:
    # Given an installed plugin containing only hooks.
    root = tmp_path / "claude"
    source = tmp_path / "plugin"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"hooks-only"}', encoding="utf-8")
    (source / "hooks").mkdir()
    (source / "hooks/hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {"hooks-only@local": [{"installPath": str(source)}]},
            }
        ),
        encoding="utf-8",
    )
    # When the catalog is requested, then whole-plugin selection remains possible.
    result = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")
    assert any(item["name"] == "hooks-only" and item["kind"] == "plugin" for item in result["items"])


def test_user_discovers_standalone_skills_and_commands_without_plugin_index(tmp_path) -> None:
    # Given a Claude directory with standalone resources and no plugin index.
    root = tmp_path / "claude"
    (root / "skills/check").mkdir(parents=True)
    (root / "skills/check/SKILL.md").write_text("---\nname: check\ndescription: Check\n---\nCheck.\n", encoding="utf-8")
    (root / "commands").mkdir()
    (root / "commands/build.md").write_text("Build.", encoding="utf-8")
    # When the local catalog is requested, both entries expose direct migration sources.
    items = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")["items"]
    assert {(item["name"], item["kind"]) for item in items} == {("check", "skill"), ("build", "command")}
    assert all(item["source"] for item in items)


def test_user_rejects_linked_standalone_source(tmp_path) -> None:
    import pytest

    from yi.catalog import discover

    # Given a standalone command linked to a file outside its advertised source.
    outside = tmp_path / "outside.md"
    outside.write_text("Private text.", encoding="utf-8")
    linked = tmp_path / "command.md"
    linked.symlink_to(outside)
    # When discovery runs, then explicit source selection cannot bypass link protection.
    with pytest.raises(ValueError, match="symlink"):
        discover(linked)


def test_user_catalog_keeps_valid_sources_when_one_install_is_missing(tmp_path) -> None:
    # Given an obsolete plugin entry alongside a valid standalone skill.
    root = tmp_path / "claude"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {"lost@market": [{"installPath": str(tmp_path / "missing")}]},
            }
        ),
        encoding="utf-8",
    )
    (root / "skills/check").mkdir(parents=True)
    (root / "skills/check/SKILL.md").write_text("Check.", encoding="utf-8")
    result = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")
    assert any(item["name"] == "check" for item in result["items"])
    assert any(item["name"] == "lost" and item["status"] == "unresolved" for item in result["items"])


def test_user_catalog_ranks_counts_deterministically(tmp_path) -> None:
    root = tmp_path / "claude"
    for name in ("alpha", "beta"):
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text("Check.", encoding="utf-8")
    run_cli(
        tmp_path,
        "record",
        event={
            "hook_event_name": "PostToolUse",
            "session_id": "s",
            "tool_use_id": "t",
            "tool_name": "Skill",
            "tool_input": {"skill": "beta"},
        },
    )
    result = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")
    assert [(item["name"], item["count"]) for item in result["items"]] == [("beta", 1), ("alpha", 0)]


def test_user_discovers_explicit_skill_directory_under_aliased_parent(tmp_path) -> None:
    # Given an explicit skill directory and a platform-style ancestor alias.
    actual = tmp_path / "actual"
    source = actual / "plugin"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        '{"name":"sample","skills":["./custom/check"]}', encoding="utf-8"
    )
    (source / "custom/check").mkdir(parents=True)
    (source / "custom/check/SKILL.md").write_text("Check.", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    # When discovered, the directory's own SKILL.md is not omitted.
    result = run_cli(tmp_path, "catalog", "--source", str(alias / "plugin"), "--json")
    assert [item["name"] for item in result["items"]] == ["sample:check"]


def test_user_catalog_reports_corrupt_plugin_without_losing_valid_items(tmp_path) -> None:
    # Given an installed plugin with malformed metadata and a valid standalone skill.
    root = tmp_path / "claude"
    broken = tmp_path / "broken"
    (broken / ".claude-plugin").mkdir(parents=True)
    (broken / ".claude-plugin/plugin.json").write_text("{invalid", encoding="utf-8")
    (root / "plugins").mkdir(parents=True)
    (root / "plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"broken@local": [{"installPath": str(broken)}]}}), encoding="utf-8"
    )
    (root / "skills/good").mkdir(parents=True)
    (root / "skills/good/SKILL.md").write_text("Good.", encoding="utf-8")
    # When listed, the invalid entry is unresolved instead of aborting the catalog.
    result = run_cli(tmp_path, "catalog", "--claude-dir", str(root), "--json")
    assert any(item["name"] == "good" for item in result["items"])
    assert any(item["name"] == "broken" and item["status"] == "unresolved" for item in result["items"])
