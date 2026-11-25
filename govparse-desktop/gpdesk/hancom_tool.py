"""Hancom COM worker, run in the *engine* interpreter (needs pywin32) as its own process.

usage:
  python hancom_tool.py save <src> <dst> <FORMAT>   FORMAT in HWP|HWPX|PDF|HTML
  python hancom_tool.py verify <path>               re-open, print pages=N

Same open options as govparse.hwp_worker. Uses DispatchEx so a fresh automation
server is created instead of attaching to a user's running 한글 window.
"""

from __future__ import annotations

import sys
from pathlib import Path

OPEN_ARG = "forceopen:true;versionwarning:false;suspendpassword:true"


def _hwp():
    import win32com.client as win32

    hwp = win32.DispatchEx("HWPFrame.HwpObject")
    try:
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
    except Exception as e:  # noqa: BLE001
        print(f"RegisterModule 실패: {e}", file=sys.stderr)
    try:
        hwp.SetMessageBoxMode(0x00020000)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:  # noqa: BLE001, S110
        pass
    return hwp


def _open(hwp, src: Path, fmt: str = "") -> int:
    if not hwp.Open(str(src), fmt, OPEN_ARG):
        raise RuntimeError("Open 실패")
    try:
        return int(hwp.PageCount)
    except Exception:  # noqa: BLE001
        return 0


def main(argv: list[str]) -> int:
    hwp = None
    try:
        hwp = _hwp()
        if argv[0] == "save":
            src, dst, fmt = Path(argv[1]).resolve(), Path(argv[2]).resolve(), argv[3].upper()
            in_fmt = "HTML" if src.suffix.lower() in (".htm", ".html") else ""
            pages = _open(hwp, src, in_fmt)
            if not hwp.SaveAs(str(dst), fmt, ""):
                raise RuntimeError(f"SaveAs {fmt} 실패")
            if not dst.is_file() or dst.stat().st_size == 0:
                raise RuntimeError("결과 파일 미생성")
            print(f"pages={pages}")
        elif argv[0] == "verify":
            pages = _open(hwp, Path(argv[1]).resolve())
            print(f"pages={pages}")
        else:
            print("unknown command", file=sys.stderr)
            return 2
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                hwp.Quit()
            except Exception:  # noqa: BLE001, S110
                pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
