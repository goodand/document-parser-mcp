"""Client for the document worker (worker.py / gpdesk.desktop_worker), SPEC.md §2."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CancelledError(Exception):
    """The call was cancelled by the user (Esc / 취소)."""


class WorkerError(RuntimeError):
    pass


def _env() -> dict:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    paths = [str(APP)]
    engine = APP.parent.parent / "src"
    if (engine / "govparse").is_dir():
        paths.append(str(engine))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            p.kill()
    except OSError:
        pass


class WorkerClient:
    def __init__(self, python: str | None = None, worker: Path | None = None):
        self.python = python or sys.executable
        self.worker = worker or APP / "worker.py"
        self._procs: set = set()
        self._serve = None
        self._serve_lock = threading.Lock()

    def run(self, mode: str, *args, on_progress=None, cancel: threading.Event | None = None) -> dict:
        p = subprocess.Popen([self.python, "-X", "utf8", str(self.worker), mode, *map(str, args)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             env=_env(), creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace")
        self._procs.add(p)
        err_tail: list[str] = []

        def read_err():
            for line in p.stderr:
                line = line.rstrip("\n")
                if line.startswith("@@progress "):
                    if on_progress:
                        try:
                            on_progress(json.loads(line[11:]))
                        except ValueError:
                            pass
                elif line.strip():
                    err_tail[:] = (err_tail + [line])[-20:]

        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        out: list[str] = []
        reader = threading.Thread(target=lambda: out.extend(p.stdout.read().splitlines()), daemon=True)
        reader.start()
        while p.poll() is None:
            if cancel is not None and cancel.wait(0.1):
                _kill_tree(p)
                p.wait()
                self._procs.discard(p)
                raise CancelledError()
            elif cancel is None:
                try:
                    p.wait(0.1)
                except subprocess.TimeoutExpired:
                    pass
        reader.join()
        t.join(2)
        self._procs.discard(p)
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        last = next((l for l in reversed(out) if l.strip()), "")
        if p.returncode != 0 or not last:
            raise WorkerError(err_tail[-1] if err_tail else f"작업이 실패했습니다 (종료 코드 {p.returncode})")
        if mode == "preview":
            pages, renderer = last.split("|", 1)
            return {"pages": int(pages), "renderer": renderer, "png": str(args[1])}
        return json.loads(last)

    def serve(self, cmd: dict) -> dict:
        """One request to the figure review session; {'ok', 'data' | 'error'}."""
        with self._serve_lock:
            if self._serve is None or self._serve.poll() is not None:
                self._serve = subprocess.Popen([self.python, "-X", "utf8", str(self.worker), "serve"],
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                               env=_env(), creationflags=NO_WINDOW, text=True, encoding="utf-8")
            self._serve.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
            self._serve.stdin.flush()
            line = self._serve.stdout.readline()
        if not line:
            return {"ok": False, "error": "그림 검토 작업이 종료되었습니다."}
        r = json.loads(line)
        if r.get("ok") and r.get("file"):
            r["data"] = json.loads(Path(r["file"]).read_text("utf-8"))
        return r

    def shutdown(self) -> None:
        for p in list(self._procs):
            _kill_tree(p)
        if self._serve is not None and self._serve.poll() is None:
            _kill_tree(self._serve)


def temp_png() -> str:
    d = Path(tempfile.gettempdir()) / "GovParseQt"
    d.mkdir(exist_ok=True)
    return str(d / f"{uuid.uuid4().hex}.png")
