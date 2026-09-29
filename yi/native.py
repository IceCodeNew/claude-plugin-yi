"""Explicit native discovery checks for isolated migration artifacts."""

import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from yi import native_opencode
from yi.targets import SKILL_ROOTS


def check(root: Path, target: str, executable: str | None, *, allow_auth: bool = False) -> dict:
    """Run model-free resource discovery in a disposable isolated HOME."""
    command = executable or {"ampcode": "amp", "opencode-v2": "opencode"}.get(target, target)
    resolved = shutil.which(command)
    if resolved is None:
        return {"status": "unavailable", "target": target, "reason": f"CLI not found: {command}"}
    if target == "ampcode" and not allow_auth:
        return {
            "status": "authorization-required",
            "target": target,
            "reason": "Amp may query account metadata with existing credentials; pass --allow-auth explicitly.",
        }
    source_home = root / target / "home"
    expected = sorted(path.parent.name for path in (source_home / SKILL_ROOTS[target]).rglob("SKILL.md"))
    prompt_root = {"pi": ".pi/agent/prompts", "opencode-v2": ".config/opencode/commands"}.get(target)
    if prompt_root:
        expected.extend(path.stem for path in (source_home / prompt_root).rglob("*.md"))
    with tempfile.TemporaryDirectory(prefix="yi-native-") as temporary:
        home = Path(temporary) / "home"
        copy_discovery_resources(source_home, home, target)
        work = Path(temporary) / "work"
        work.mkdir()
        environment = isolated_environment(home)
        try:
            version_result = subprocess.run(  # noqa: S603 - Explicit CLI version query with isolated configuration.
                [resolved, "version" if target == "ampcode" else "--version"],
                cwd=work,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            cli_version = version_result.stdout.strip()
            if target == "pi":
                found = pi_resources(resolved, work, environment)
            elif target == "codex":
                found = codex_resources(resolved, work, environment)
            elif target == "opencode-v2":
                found = native_opencode.resources(resolved, work, environment, expected)
            else:
                found = amp_resources(resolved, work, environment)
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"status": "unverified", "target": target, "reason": "Native discovery failed or timed out."}
    missing = sorted(set(expected) - set(found))
    return {
        "status": "discovered" if expected and not missing else "unverified",
        "target": target,
        "found": found,
        "missing": missing,
        "behavior_verified": False,
        "cli_version": cli_version,
    }


def copy_discovery_resources(source: Path, destination: Path, target: str) -> None:
    """Copy only inert skill and prompt files, excluding executable configuration."""
    destination.mkdir(parents=True)
    roots = [source / SKILL_ROOTS[target]]
    roots.extend([source / ".pi/agent/prompts", source / ".config/opencode/commands"])
    for path in (path for folder in roots for path in folder.rglob("*.md")):
        relative = path.relative_to(source)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            msg = "Native discovery rejects symlink resources."
            raise ValueError(msg)
        if not {"skills", "prompts", "commands"}.intersection(relative.parts):
            continue
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(path.read_bytes())


def isolated_environment(home: Path) -> dict[str, str]:
    """Exclude ambient credentials and point configuration roots at the probe HOME."""
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", os.defpath),
        "TERM": "dumb",
        "NO_COLOR": "1",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "CODEX_HOME": str(home / ".codex"),
        "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
    }


def pi_resources(executable: str, work: Path, environment: dict[str, str]) -> list[str]:
    """Ask Pi to list commands without extensions, tools, or model messages."""
    result = subprocess.run(  # noqa: S603 - Explicit executable and fixed RPC; no model input or shell.
        [
            executable,
            "--offline",
            "--no-extensions",
            "--no-context-files",
            "--no-approve",
            "--no-session",
            "--no-tools",
            "--mode",
            "rpc",
        ],
        input=json.dumps({"id": "commands", "type": "get_commands"}) + "\n",
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    for line in result.stdout.splitlines():
        response = json.loads(line)
        if response.get("id") == "commands" and response.get("success"):
            return [
                item["name"].removeprefix("skill:")
                for item in response["data"]["commands"]
                if item.get("source") in {"skill", "prompt"}
            ]
    msg = "Pi did not return a command discovery response."
    raise ValueError(msg)


def codex_resources(executable: str, work: Path, environment: dict[str, str]) -> list[str]:
    """List Codex skills through the initialized app-server protocol."""
    Path(environment["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(  # noqa: S603 - Explicit CLI with fixed discovery RPC; no shell or model turn.
        [executable, "app-server", "--stdio"],
        cwd=work,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            send_rpc(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "yi-check", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                },
            )
            receive_rpc(process, 1)
            send_rpc(process, {"method": "initialized", "params": {}})
            send_rpc(process, {"id": 2, "method": "skills/list", "params": {"cwds": [str(work)], "forceReload": True}})
            response = receive_rpc(process, 2)
            return [
                skill["name"]
                for entry in response["result"]["data"]
                for skill in entry["skills"]
                if skill.get("enabled")
            ]
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def send_rpc(process: subprocess.Popen, message: dict) -> None:
    """Write one framed JSON request to a child process."""
    if process.stdin is None:
        msg = "Native process has no input stream."
        raise ValueError(msg)
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


def receive_rpc(process: subprocess.Popen, identity: int) -> dict:
    """Read bounded JSON lines until the requested response arrives."""
    if process.stdout is None:
        msg = "Native process has no output stream."
        raise ValueError(msg)
    deadline = time.monotonic() + 30
    pending = b""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(timeout=max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") == identity:
                    return response
    msg = "Native discovery response timed out or ended early."
    raise ValueError(msg)


def amp_resources(executable: str, work: Path, environment: dict[str, str]) -> list[str]:
    """Use explicitly authorized Amp authentication without copying credentials."""
    home = Path(environment["HOME"])
    environment["XDG_DATA_HOME"] = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    settings = work / "amp-settings.json"
    settings.write_text(
        json.dumps(
            {
                "amp.skills.disableClaudeCodeSkills": True,
                "amp.skills.disableGlobalAgentsSkills": True,
                "amp.skills.path": str(home / ".config/amp/skills"),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 - Explicitly authorized discovery command; no model turn.
        [executable, "--settings-file", str(settings), "skill", "list", "--json"],
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(result.stdout)
    items = data if isinstance(data, list) else data.get("skills", [])
    return [item["name"] for item in items if item.get("source") == "user-amp"]
