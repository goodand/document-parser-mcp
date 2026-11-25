"""Embedded picture binaries (BinData) from HWP 5 (OLE) and HWPX (zip).

Mapping rule: rhwp emits ``bin://<id>``. HWP stores BinData/BIN%04X.<ext> (hex id),
HWPX stores BinData/image<id>.<ext>. Unmatched pictures are reported missing.
"""

from __future__ import annotations

import re
import zipfile
import zlib
from pathlib import Path

_HWP = re.compile(r"^BIN([0-9A-Fa-f]{4})\.", re.ASCII)
_HWPX = re.compile(r"^[A-Za-z_]*?(\d+)\.", re.ASCII)


def bin_id_from_name(name: str) -> int | None:
    m = _HWP.match(name)
    if m:
        return int(m.group(1), 16)
    m = _HWPX.match(name)
    return int(m.group(1)) if m else None


def extract_hwpx_bindata(path: Path) -> dict[int, tuple[str, bytes]]:
    out: dict[int, tuple[str, bytes]] = {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.filename.startswith("BinData/") and not info.is_dir():
                name = info.filename.split("/", 1)[1]
                bid = bin_id_from_name(name)
                if bid is not None:
                    out[bid] = (name, z.read(info))
    return out


def extract_hwp_bindata(path: Path) -> dict[int, tuple[str, bytes]]:
    import olefile

    out: dict[int, tuple[str, bytes]] = {}
    with olefile.OleFileIO(str(path)) as ole:
        for entry in ole.listdir():
            if len(entry) == 2 and entry[0] == "BinData":
                raw = ole.openstream(entry).read()
                bid = bin_id_from_name(entry[1])
                if bid is None:
                    continue
                try:  # most streams are raw-deflate compressed
                    data = zlib.decompressobj(-15).decompress(raw)
                except zlib.error:
                    data = raw
                out[bid] = (entry[1], data)
    return out


def extract_bindata(path: Path) -> dict[int, tuple[str, bytes]]:
    head = Path(path).read_bytes()[:8]
    if head.startswith(b"PK"):
        return extract_hwpx_bindata(path)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return extract_hwp_bindata(path)
    return {}


def collect_picture_uris(ir: dict) -> list[str]:
    found: list[str] = []

    def walk(n):
        if isinstance(n, list):
            for c in n:
                walk(c)
        elif isinstance(n, dict):
            if n.get("kind") == "picture":
                uri = (n.get("image") or {}).get("uri")
                if uri and uri not in found:
                    found.append(uri)
            for k in ("blocks", "cells", "body"):
                if k in n:
                    walk(n[k])

    walk(ir.get("body"))
    return found
