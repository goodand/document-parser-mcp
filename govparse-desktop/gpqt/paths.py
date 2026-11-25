"""Path rules that differ by OS."""

from __future__ import annotations

import os
from pathlib import Path


def same_path(a: str, b: str) -> bool:
    """Same file? Uses the file system when both exist, so case rules follow the volume (Linux: case matters)."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)).rstrip("\\/") == os.path.normcase(os.path.abspath(b)).rstrip("\\/")


def unique(target: str) -> str:
    """target, or 'name (2)', 'name (3)' … if something already exists there (folders: after the whole name)."""
    if not os.path.exists(target):
        return target
    p = Path(target)
    stem, ext = (p.name, "") if p.is_dir() else (p.stem, p.suffix)
    n = 2
    while True:
        c = p.with_name(f"{stem} ({n}){ext}")
        if not c.exists():
            return str(c)
        n += 1


def base_name(path: str) -> str:
    """File name of a path written on any OS ('C:\\a\\b.hwpx' or '/a/b.hwpx')."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
