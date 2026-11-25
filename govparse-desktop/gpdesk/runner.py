"""Cancellable subprocesses with tree kill, and Hancom instance bookkeeping.

COM/MCP/OCR work never runs in the UI thread or process: each step is a child
process the app can kill. Hancom cleanup kills only Hwp.exe instances that
appeared while our own worker ran (pid + creation time), never pre-existing
user windows.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HANCOM_IMAGE = "Hwp.exe"
_HANCOM_LOCK = threading.Lock()
_OWNED_HANCOM: set[int] = set()
_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


class Cancelled(Exception):
    pass


class RunTimeout(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                   creationflags=NO_WINDOW, check=False)


def run(cmd: list[str], *, timeout: float, token: CancelToken | None = None,
        env: dict | None = None, cwd: str | None = None) -> Result:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                         creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    with _LIVE_LOCK:
        _LIVE.add(p)
    out: dict[str, bytes] = {}

    def pump(name, stream):
        out[name] = stream.read()

    threads = [threading.Thread(target=pump, args=(n, s), daemon=True)
               for n, s in (("o", p.stdout), ("e", p.stderr))]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    try:
        while p.poll() is None:
            if token is not None and token.cancelled:
                kill_tree(p.pid)
                raise Cancelled()
            if time.monotonic() > deadline:
                kill_tree(p.pid)
                raise RunTimeout(f"{timeout:.0f}초 제한 시간을 넘었습니다.")
            time.sleep(0.1)
        for t in threads:
            t.join(5)
        dec = lambda b: (b or b"").decode("utf-8", "replace")  # noqa: E731
        return Result(p.returncode, dec(out.get("o")), dec(out.get("e")))
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(p)


def hancom_instances() -> set[tuple[int, float, bool]] | None:
    """(pid, creation time, is COM automation server) of running Hwp.exe.

    Measured on this PC: a COM-created instance runs as ``hwp.exe -Automation
    -Embedding``; a window the user opened carries a document path instead.
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Hwp.exe'\" | ForEach-Object { "
          "$a = if ($_.CommandLine -match '-Automation') {1} else {0}; "
          "\"$($_.ProcessId) $($_.CreationDate.ToFileTimeUtc()) $a\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                       text=True, creationflags=NO_WINDOW, check=False)
    if r.returncode != 0:
        return None  # snapshot unavailable: ownership cannot be established
    found = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].isdigit():
            found.add((int(parts[0]), float(parts[1]), parts[2] == "1"))
    return found


def new_hancom_pids(before: set, after: set) -> set[int]:
    """Instances that appeared during our run AND are automation servers.

    If either snapshot is unavailable, ownership is unresolved and nothing is ours.
    """
    if before is None or after is None:
        return set()
    return {pid for pid, _created, automation in after - before if automation}


def run_hancom(cmd: list[str], *, timeout: float, token: CancelToken | None = None,
               env: dict | None = None) -> Result:
    """Serialize COM jobs; on any exit, close only the Hwp.exe instances we started."""
    with _HANCOM_LOCK:
        before = hancom_instances()
        try:
            return run(cmd, timeout=timeout, token=token, env=env)
        finally:
            ours = new_hancom_pids(before, hancom_instances())
            for pid in ours:
                kill_tree(pid)
            _OWNED_HANCOM.difference_update(ours)


def shutdown_children() -> None:
    """App exit: kill only processes this app started."""
    with _LIVE_LOCK:
        live = list(_LIVE)
    for p in live:
        if p.poll() is None:
            kill_tree(p.pid)
    for pid in list(_OWNED_HANCOM):
        kill_tree(pid)
    _ = os.getpid()
