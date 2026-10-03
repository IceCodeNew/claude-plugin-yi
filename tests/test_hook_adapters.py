"""Exercise generated adapters through native callbacks and the real command runtime."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from yi.hook_renderers import render_hook_adapter

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for generated hook adapters")


@pytest.mark.parametrize("command", ["deny", "invalid-update"])
def test_amp_rejected_result_does_not_run_post_hook_but_executed_errors_do(tmp_path, command) -> None:
    # Native Amp emits tool.result after reject-and-continue even though the tool never ran.
    # Replay that verified callback boundary; real subprocess receipts own the hook assertion.
    home = tmp_path / "home"
    root = home / ".local/share/yi/hooks/fixture"
    source = root / "source"
    source.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "yi/hook_runtime.mjs", root / "runner.mjs")
    (source / "probe.mjs").write_text(
        "import {readFileSync,appendFileSync} from 'node:fs';\n"
        "const p=JSON.parse(readFileSync(0,'utf8'));\n"
        "appendFileSync('receipts.jsonl',JSON.stringify({event:p.hook_event_name,id:p.tool_use_id,"
        "thread:p.session_id})+'\\n');\n"
        "if(p.hook_event_name==='PreToolUse'&&p.tool_input.command==='deny') "
        "console.log(JSON.stringify({hookSpecificOutput:{permissionDecision:'deny',permissionDecisionReason:'denied'}}));\n"
        "if(p.hook_event_name==='PreToolUse'&&p.tool_input.command==='invalid-update') "
        "console.log(JSON.stringify({hookSpecificOutput:{updatedInput:{command:42},"
        "additionalContext:'not executed'}}));\n",
        encoding="utf-8",
    )
    hook = {"type": "command", "command": 'node "${CLAUDE_PLUGIN_ROOT}/probe.mjs"'}
    events = {event: [{"hooks": [hook]}] for event in ("PreToolUse", "PostToolUse")}
    rendered = render_hook_adapter("ampcode", "fixture", Path(".local/share/yi/hooks/fixture"), events)
    adapter = tmp_path / "adapter.mjs"
    adapter.write_bytes(next(iter(rendered.values())))
    harness = tmp_path / "replay.mjs"
    harness.write_text(
        "import adapter from './adapter.mjs';\n"
        "import {pathToFileURL,fileURLToPath} from 'node:url';\n"
        "const callbacks=new Map();\n"
        "adapter({on:(name,fn)=>callbacks.set(name,fn),helpers:{"
        "filePathFromURI:fileURLToPath,shellCommandFromToolCall:e=>({command:e.input.command})}});\n"
        "const ctx={system:{workspaceRoot:pathToFileURL(process.cwd())},"
        "ui:{notify:async()=>{}},logger:{log:()=>{}},thread:{cancel:async()=>{}}};\n"
        "const call=(thread,id,command)=>({thread:{id:thread},toolUseID:id,tool:'Bash',input:{command}});\n"
        "const rejected=call('thread-a','shared-id',process.argv[2]);\n"
        "const rejection=await callbacks.get('tool.call')(rejected,ctx);\n"
        "if(rejection?.action!=='reject-and-continue')throw Error('expected native rejection');\n"
        "const unrelated=call('thread-b','shared-id','executed error');\n"
        "await callbacks.get('tool.call')(unrelated,ctx);\n"
        "await callbacks.get('tool.result')({...unrelated,status:'error',error:'real execution failed'},ctx);\n"
        "await callbacks.get('tool.result')({...rejected,status:'error',error:rejection.message},ctx);\n"
        "const reused=call('thread-a','shared-id','executed error after rejected result');\n"
        "await callbacks.get('tool.call')(reused,ctx);\n"
        "await callbacks.get('tool.result')({...reused,status:'error',error:'real execution failed'},ctx);\n",
        encoding="utf-8",
    )
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Controlled callback replay and newly written local fixture commands.
        [NODE, str(harness), command],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home), "YI_ENABLE_MIGRATED_HOOKS": "fixture"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipts = [json.loads(line) for line in (tmp_path / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(item["event"], item["thread"]) for item in receipts] == [
        ("PreToolUse", "thread-a"),
        ("PreToolUse", "thread-b"),
        ("PostToolUse", "thread-b"),
        ("PreToolUse", "thread-a"),
        ("PostToolUse", "thread-a"),
    ]


def test_amp_stop_limit_settles_chain_without_exhausting_later_user_run(tmp_path) -> None:
    home = tmp_path / "home"
    root = home / ".local/share/yi/hooks/fixture"
    source = root / "source"
    source.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "yi/hook_runtime.mjs", root / "runner.mjs")
    (source / "stop.mjs").write_text(
        "import {readFileSync,appendFileSync} from 'node:fs';\n"
        "const p=JSON.parse(readFileSync(0,'utf8'));\n"
        "appendFileSync('stop-receipts.jsonl',JSON.stringify({active:p.stop_hook_active})+'\\n');\n"
        "console.log(JSON.stringify({decision:'block',reason:'Continue the controlled fixture.'}));\n",
        encoding="utf-8",
    )
    events = {"Stop": [{"hooks": [{"type": "command", "command": 'node "${CLAUDE_PLUGIN_ROOT}/stop.mjs"'}]}]}
    rendered = render_hook_adapter("ampcode", "fixture", Path(".local/share/yi/hooks/fixture"), events)
    (tmp_path / "adapter.mjs").write_bytes(next(iter(rendered.values())))
    harness = tmp_path / "continuations.mjs"
    harness.write_text(
        "import adapter from './adapter.mjs';\n"
        "import {pathToFileURL,fileURLToPath} from 'node:url';\n"
        "const callbacks=new Map();\n"
        "adapter({on:(name,fn)=>callbacks.set(name,fn),helpers:{filePathFromURI:fileURLToPath}});\n"
        "const ctx={system:{workspaceRoot:pathToFileURL(process.cwd())},"
        "ui:{notify:async()=>{}},logger:{log:()=>{}},thread:{cancel:async()=>{}}};\n"
        "const thread={id:'one-thread'};\n"
        "let message='First real user prompt';\n"
        "for(let index=0;index<6;index++){\n"
        "await callbacks.get('agent.start')({thread,message,id:index},ctx);\n"
        "const result=await callbacks.get('agent.end')({thread,message,id:index,status:'done',"
        "messages:[{role:'assistant',content:[{type:'text',text:'Completed fixture'}]}]},ctx);\n"
        "if(index<5&&result?.action!=='continue')throw Error('chain stopped before native limit');\n"
        "if(index===5&&result!==undefined)throw Error('chain exceeded native limit');\n"
        "message=result?.userMessage||message;\n"
        "}\n"
        "await callbacks.get('agent.start')({thread,message:'Later real user prompt',id:20},ctx);\n"
        "const later=await callbacks.get('agent.end')({thread,message:'Later real user prompt',id:20,status:'done',"
        "messages:[{role:'assistant',content:[{type:'text',text:'Later completion'}]}]},ctx);\n"
        "console.log(JSON.stringify(later||null));\n",
        encoding="utf-8",
    )
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - Native callback replay with a real local Stop fixture.
        [NODE, str(harness)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home), "YI_ENABLE_MIGRATED_HOOKS": "fixture"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipts = [
        json.loads(line) for line in (tmp_path / "stop-receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["active"] for item in receipts] == [False, True, True, True, True, True, False]
    assert json.loads(result.stdout) == {
        "action": "continue",
        "userMessage": "Continue the controlled fixture.",
        "maxContinuations": 5,
    }
