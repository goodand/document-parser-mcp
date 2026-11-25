"""Stage outputs next to the target (same volume), validate, then publish.

A failed or cancelled export never touches an existing result or the source.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path, PurePosixPath


class PublishError(Exception):
    pass


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


_RESERVE_LOCK = threading.Lock()
_RESERVED: set[str] = set()  # final paths promised to a live Stager (normcase)


def _taken(p: Path) -> bool:
    return p.exists() or os.path.normcase(os.path.abspath(p)) in _RESERVED


def unique_target(target: Path) -> Path:
    target = Path(target)
    if not _taken(target):
        return target
    stem, suffix = (target.stem, target.suffix) if target.suffix and not target.is_dir() else (
        target.name, "")
    n = 2
    while True:
        cand = target.with_name(f"{stem} ({n}){suffix}")
        if not _taken(cand):
            return cand
        n += 1


def check_relative_refs(root: Path, refs: list[str]) -> list[str]:
    bad = []
    base = Path(root).resolve()
    for ref in refs:
        p = PurePosixPath(ref.replace("\\", "/"))
        if p.is_absolute() or ":" in ref or ".." in p.parts:
            bad.append(ref)
            continue
        full = (base / Path(*p.parts)).resolve()
        if base not in full.parents or not full.is_file():
            bad.append(ref)
    return bad


class Stager:
    """Context manager: commit on clean exit, abort on exception."""

    def __init__(self, source: Path, target: Path, single_file: bool = False) -> None:
        source, target = Path(source), Path(target)
        if _same(source, target) or (single_file and _same(source.parent, target)):
            raise PublishError("원본과 같은 경로에는 저장할 수 없습니다.")
        if not single_file and source.resolve().is_relative_to(target.resolve()) and target.exists():
            pass  # writing a new sibling folder never touches the source
        self.source = source
        with _RESERVE_LOCK:  # TLC JobRouting NoOverwrite: reserve the name atomically
            self.final = unique_target(target)
            self._key = os.path.normcase(os.path.abspath(self.final))
            _RESERVED.add(self._key)
        self.single_file = single_file
        parent = self.final.parent
        parent.mkdir(parents=True, exist_ok=True)
        self.staging = parent / f".govparse-staging-{uuid.uuid4().hex[:10]}"
        self.staging.mkdir()
        self.staged_file = self.staging / self.final.name
        self.committed = False

    def commit(self) -> Path:
        if self.single_file and (not self.staged_file.is_file()
                                 or self.staged_file.stat().st_size == 0):
            raise PublishError("결과 파일이 생성되지 않았습니다.")
        with _RESERVE_LOCK:
            if self.final.exists():  # created outside the app meanwhile: never overwrite
                _RESERVED.discard(self._key)
                self.final = unique_target(self.final)
                self._key = os.path.normcase(os.path.abspath(self.final))
                _RESERVED.add(self._key)
            if self.single_file:
                os.rename(self.staged_file, self.final)  # rename fails instead of overwriting
            else:
                os.rename(self.staging, self.final)
            _RESERVED.discard(self._key)
        shutil.rmtree(self.staging, ignore_errors=True)
        self.committed = True
        return self.final

    def abort(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
        with _RESERVE_LOCK:
            _RESERVED.discard(self._key)

    def __enter__(self) -> "Stager":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            try:
                self.commit()
            except BaseException:
                self.abort()
                raise
        else:
            self.abort()
        return False
