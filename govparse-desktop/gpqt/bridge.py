"""Local channel for MCP tools (SPEC.md §5): one UTF-8 line in, one JSON line out, current user only.

Windows: named pipe GovParseDesktop-<pid>.  macOS/Linux: <dir>/govparse-desktop/app-<pid>.sock, dir 0700, socket 0600.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PIPE_PREFIX = "GovParseDesktop-"


def socket_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") if sys.platform.startswith("linux") else None
    if not base:
        base = os.path.join(tempfile.gettempdir(), f"govparse-{os.getuid()}") if hasattr(os, "getuid") else tempfile.gettempdir()
    return Path(base) / "govparse-desktop"


def server_name() -> str:
    """Name given to QLocalServer.listen()."""
    if sys.platform == "win32":
        return f"{PIPE_PREFIX}{os.getpid()}"
    d = socket_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    if d.parent.name.startswith("govparse-"):
        os.chmod(d.parent, 0o700)
    return str(d / f"app-{os.getpid()}.sock")


def publish_server(address: str) -> Path | None:
    """Publish the active Windows pipe address in the per-user temp directory."""
    if sys.platform != "win32":
        return None
    d = Path(tempfile.gettempdir()) / "govparse-desktop"
    d.mkdir(parents=True, exist_ok=True)
    record = d / f"app-{os.getpid()}.pipe"
    record.write_text(address, encoding="utf-8")
    return record


def forget_server(record: Path | None) -> None:
    if record is not None:
        try:
            record.unlink(missing_ok=True)
        except OSError:
            pass


def request(address: str, line: str, timeout: float = 3.0) -> dict:
    if sys.platform == "win32":
        import time

        end = time.time() + timeout
        while True:
            try:
                with open(address, "r+b", buffering=0) as f:
                    f.write((line + "\n").encode("utf-8"))
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        buf += chunk
                    return json.loads(buf.decode("utf-8"))
            except OSError:
                if time.time() > end:
                    raise
                time.sleep(0.05)
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(address)
        s.sendall((line + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    finally:
        s.close()


@dataclass
class Found:
    status: str            # app_closed | one | multiple_windows | timeout
    address: str | None = None


def _alive_unix(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(0.5)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def _alive_windows(path: str) -> bool:
    """Reject stale or inaccessible pipe names left in the Windows namespace."""
    try:
        reply = request(path, "state", timeout=1.0)
    except (OSError, TimeoutError, json.JSONDecodeError):
        return False
    return isinstance(reply, dict) and isinstance(reply.get("status"), str)


def discover(directory: Path | None = None) -> Found:
    """Count the apps that actually accept a connection. A socket file left by a crashed app does not count."""
    if sys.platform == "win32":
        d = Path(directory) if directory else Path(tempfile.gettempdir()) / "govparse-desktop"
        live = []
        for record in sorted(d.glob("app-*.pipe")) if d.is_dir() else []:
            try:
                path = record.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if path.startswith("\\\\.\\pipe\\" + PIPE_PREFIX) and _alive_windows(path):
                live.append(path)
            else:
                forget_server(record)
    else:
        d = Path(directory) if directory else socket_dir()
        live = [str(p) for p in sorted(d.glob("app-*.sock"))] if d.is_dir() else []
        live = [p for p in live if _alive_unix(p)]
    if not live:
        return Found("app_closed")
    if len(live) > 1:
        return Found("multiple_windows")
    return Found("one", live[0])
