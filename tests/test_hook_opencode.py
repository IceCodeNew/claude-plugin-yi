"""Guard the generated OpenCode transport using real SDK Effects and hook subprocesses."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.fixture
def generated_opencode(tmp_path, request) -> tuple[Path, Path]:
    sdk = os.environ.get("YI_OPENCODE_SDK_DIR")
    if NODE is None or not sdk:
        pytest.skip("Node and YI_OPENCODE_SDK_DIR with reviewed OpenCode 2.0.19 dependencies are required")
    sdk_path = Path(sdk).resolve()
    if not (sdk_path / "effect/package.json").is_file() or not (sdk_path / "@opencode/schema/package.json").is_file():
        pytest.skip("The supplied OpenCode SDK directory does not contain the required dependencies")
    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / "hooks").mkdir()
    (source / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fixture"}), encoding="utf-8")
    startup = getattr(request, "param", None)
    event = "SessionStart" if startup else "PreToolUse"
    handler = {"type": "command", "command": 'node "${CLAUDE_PLUGIN_ROOT}/hooks/probe.mjs"'}
    if startup == "timeout":
        handler["timeout"] = 0.5
    (source / "hooks/hooks.json").write_text(json.dumps({"hooks": {event: [{"hooks": [handler]}]}}), encoding="utf-8")
    # The subprocess is the source hook. It emits actual command-hook JSON, not a runner mock.
    (source / "hooks/probe.mjs").write_text(
        "import {readFileSync,appendFileSync} from 'node:fs';\n"
        "const p=JSON.parse(readFileSync(0,'utf8'));\n"
        "appendFileSync('receipts.jsonl',JSON.stringify(p)+'\\n');\n"
        "const decision=JSON.parse(readFileSync(new URL('decision.json',import.meta.url),'utf8'));\n"
        "console.log(JSON.stringify({hookSpecificOutput:decision}));\n",
        encoding="utf-8",
    )
    if startup == "timeout":
        with (source / "hooks/probe.mjs").open("a", encoding="utf-8") as probe:
            probe.write("setTimeout(()=>{},5000);\n")
    initial_decision = {"additionalContext": "STARTUP_CONTEXT"} if startup == "success" else {}
    (source / "hooks/decision.json").write_text(json.dumps(initial_decision), encoding="utf-8")
    output = tmp_path / "output"
    result = subprocess.run(  # noqa: S603 - Public migration CLI receives only the synthetic inert source fixture.
        [
            sys.executable,
            str(PROJECT / "scripts/yi.py"),
            "migrate",
            "--source",
            str(source),
            "--target",
            "opencode-v2",
            "--output",
            str(output),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    package = output / "opencode-v2/home/.local/share/yi/hooks/fixture/opencode"
    (package / "node_modules").symlink_to(sdk_path, target_is_directory=True)
    harness = package / "replay.mjs"
    # Registration is a transport capture; Effects, Tool.Error, runner and source commands are real.
    # It deliberately does not simulate native input validation: invalid updates must be rejected by the adapter.
    harness.write_text(
        "import adapter from './index.js';\n"
        "import {Effect} from 'effect';\n"
        "import {Tool} from '@opencode/schema/tool';\n"
        "const callbacks=new Map();\n"
        "const hook=domain=>(name,callback)=>Effect.sync(()=>{callbacks.set(domain+'.'+name,callback);"
        "return {dispose:Effect.void};});\n"
        "await Effect.runPromise(adapter.effect({location:{directory:process.cwd()},"
        "tool:{hook:hook('tool')},session:{hook:hook('session')}}));\n"
        "const event={...JSON.parse(process.argv[2]),sessionID:'session-fixture',agent:'build',"
        "messageID:'message-fixture',id:'call-fixture'};\n"
        "const outcome=await Effect.runPromise(callbacks.get('tool.execute.before')(event).pipe(Effect.match({"
        "onFailure:error=>({status:'denied',controlled:error instanceof Tool.Error,reason:error.message}),"
        "onSuccess:()=>({status:'allowed'})})));\n"
        "const context={sessionID:'session-fixture',system:[],messages:[]};\n"
        "await Effect.runPromise(callbacks.get('session.context')(context));\n"
        "console.log(JSON.stringify({outcome,input:event.input,system:context.system}));\n",
        encoding="utf-8",
    )
    if startup:
        harness.write_text(
            "import adapter from './index.js';\n"
            "import {Effect} from 'effect';\n"
            "const callbacks=new Map();\n"
            "await Effect.runPromise(adapter.effect({location:{directory:process.cwd()},session:{hook:(name,fn)=>"
            "Effect.sync(()=>{callbacks.set(name,fn);return {dispose:Effect.void};})}}));\n"
            "const contexts=Array.from({length:3},()=>({sessionID:'session-fixture',system:[],messages:[]}));\n"
            "const invoke=context=>Effect.runPromise(callbacks.get('context')(context));\n"
            "const first=await Promise.allSettled(contexts.slice(0,2).map(invoke));\n"
            "const later=await Promise.allSettled([invoke(contexts[2])]);\n"
            "console.log(JSON.stringify({statuses:[...first,...later].map(result=>result.status),"
            "systems:contexts.map(context=>context.system)}));\n",
            encoding="utf-8",
        )
    return package, tmp_path


@pytest.mark.parametrize(
    ("tool", "original", "updated"),
    [
        ("custom-tool", {"command": "original"}, {"command": "changed"}),
        ("constructor", {"command": "original"}, {"command": "changed"}),
        ("shell", {"command": "original"}, {"command": 42}),
        ("read", {"path": "original.txt"}, {"file_path": []}),
        ("edit", {"path": "original.txt", "oldString": "old", "newString": "new"}, {"replace_all": "yes"}),
        ("shell", {"command": "original"}, {"command": "changed", "unreviewed": True}),
        ("shell", {"command": "original"}, {"__proto__": {"command": "changed"}}),
    ],
)
def test_unreviewed_or_invalid_updates_are_controlled_denials_without_context_leak(
    generated_opencode, tool, original, updated
) -> None:
    package, work = generated_opencode
    decision = package.parent / "source/hooks/decision.json"
    decision.write_text(json.dumps({"updatedInput": updated, "additionalContext": "MUST_NOT_LEAK"}), encoding="utf-8")
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Real SDK callback transport and controlled source-hook subprocess.
        [NODE, str(package / "replay.mjs"), json.dumps({"tool": tool, "input": original})],
        cwd=work,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "fixture"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["outcome"]["status"] == "denied", actual
    assert actual["outcome"]["controlled"] is True, actual
    assert "updatedInput" in actual["outcome"]["reason"]
    assert actual["input"] == original
    assert actual["system"] == []
    receipts = [json.loads(line) for line in (work / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == 1
    assert receipts[0]["hook_event_name"] == "PreToolUse"


@pytest.mark.parametrize(
    ("tool", "original", "updated", "expected"),
    [
        (
            "edit",
            {"path": "old.txt", "oldString": "old", "newString": "new", "replaceAll": False, "nativeExtra": "retained"},
            {"file_path": "new.txt", "old_string": "before", "new_string": "after", "replace_all": True},
            {
                "path": "new.txt",
                "oldString": "before",
                "newString": "after",
                "replaceAll": True,
                "nativeExtra": "retained",
            },
        ),
        (
            "shell",
            {"command": "original", "workdir": "/fixture"},
            {"command": "changed", "run_in_background": True, "timeout": 0},
            {"command": "changed", "workdir": "/fixture", "background": True, "timeout": 0},
        ),
        ("custom-tool", {"opaque": {"native": True}}, None, {"opaque": {"native": True}}),
    ],
)
def test_reviewed_updates_are_inverse_mapped_and_unknown_tools_without_updates_remain_allowed(
    generated_opencode, tool, original, updated, expected
) -> None:
    package, work = generated_opencode
    decision = {"additionalContext": "ALLOWED_CONTEXT"}
    if updated is not None:
        decision["updatedInput"] = updated
    (package.parent / "source/hooks/decision.json").write_text(json.dumps(decision), encoding="utf-8")
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Real SDK callback transport and controlled source-hook subprocess.
        [NODE, str(package / "replay.mjs"), json.dumps({"tool": tool, "input": original})],
        cwd=work,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "fixture"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["outcome"] == {"status": "allowed"}
    assert actual["input"] == expected
    assert actual["system"] == [{"type": "text", "text": "ALLOWED_CONTEXT"}]
    receipts = [json.loads(line) for line in (work / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == 1
    assert receipts[0]["hook_event_name"] == "PreToolUse"


@pytest.mark.parametrize(
    ("generated_opencode", "expected_context"),
    [("timeout", ""), ("success", "STARTUP_CONTEXT")],
    indirect=["generated_opencode"],
)
def test_startup_runs_once_and_never_poison_later_or_concurrent_context_requests(
    generated_opencode, expected_context
) -> None:
    package, work = generated_opencode
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Real SDK Effects await a controlled startup-hook subprocess.
        [NODE, str(package / "replay.mjs")],
        cwd=work,
        env={**os.environ, "YI_ENABLE_MIGRATED_HOOKS": "fixture"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["statuses"] == ["fulfilled", "fulfilled", "fulfilled"]
    receipts = [json.loads(line) for line in (work / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == 1
    assert receipts[0]["hook_event_name"] == "SessionStart"
    assert actual["systems"] == (
        [[{"type": "text", "text": expected_context}]] * 3 if expected_context else [[], [], []]
    )
    if expected_context:
        assert result.stderr == ""
    else:
        assert result.stderr.count("SessionStart hook failed") == 1
