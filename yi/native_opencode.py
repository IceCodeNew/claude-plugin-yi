"""Authenticated localhost resource discovery for OpenCode v2."""

import base64
import json
import os
import selectors
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def resources(executable: str, work: Path, environment: dict[str, str], expected: list[str]) -> list[str]:
    """Wait for native resource readiness and always stop the private server."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with subprocess.Popen(  # noqa: S603 - Explicit CLI and localhost-only server, without model requests.
        [executable, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=work,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        try:
            password = server_password(process)
            token = base64.b64encode(f"opencode:{password}".encode()).decode()
            query = urllib.parse.urlencode({"location[directory]": str(work)})
            url = f"http://127.0.0.1:{port}/api"
            deadline = time.monotonic() + 20
            found = []
            while time.monotonic() < deadline:
                found = []
                for kind in ("skill", "command"):
                    request = urllib.request.Request(  # noqa: S310 - URL is constructed only from localhost and fixed routes.
                        f"{url}/{kind}?{query}", headers={"Authorization": f"Basic {token}"}
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - Fixed localhost URL.
                            found.extend(item["name"] for item in json.load(response)["data"])
                    except (urllib.error.URLError, TimeoutError):
                        continue
                if set(expected).issubset(found):
                    return found
                time.sleep(0.2)
            return found
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def server_password(process: subprocess.Popen) -> str:
    """Consume the generated password in memory without logging credentials."""
    if process.stdout is None:
        msg = "OpenCode has no startup stream."
        raise ValueError(msg)
    pending = b""
    deadline = time.monotonic() + 15
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
                if line.startswith(b"server password "):
                    return line.removeprefix(b"server password ").decode().strip()
    msg = "OpenCode did not complete authenticated startup."
    raise ValueError(msg)
