"""HWP/HWPX 한 건을 PDF로 변환하는 워커 (한컴오피스 COM).

배치 전체를 한 프로세스에서 돌리면 파일 하나가 COM 안에서 멈출 때 배치가 통째로
멈춘다. 그래서 파일마다 이 워커를 별도 프로세스로 띄우고 부모가 타임아웃을 건다.

사용: python -m govparse.hwp_worker <src> <dst.pdf>
종료코드 0 = 성공, 그 외 = 실패(사유는 stderr).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m govparse.hwp_worker <src> <dst.pdf>", file=sys.stderr)
        return 2

    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    import win32com.client as win32

    hwp = None
    try:
        hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
        # 파일 접근 승인 팝업 억제. 실패해도 변환 자체는 시도한다.
        try:
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception as e:  # noqa: BLE001
            print(f"RegisterModule 실패: {e}", file=sys.stderr)
        # 대화상자 자동 응답 (뜨더라도 배치가 멈추지 않도록)
        try:
            hwp.SetMessageBoxMode(0x00020000)
        except Exception as e:  # noqa: BLE001
            print(f"SetMessageBoxMode 실패: {e}", file=sys.stderr)

        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:  # noqa: BLE001, S110
            pass

        # versionwarning:false 가 없으면 "상위 버전에서 작동한 문서입니다" 대화상자에서 멈춘다.
        # suspendpassword:true 는 암호 걸린 문서에서 멈추는 것을 막는다.
        open_arg = "forceopen:true;versionwarning:false;suspendpassword:true"
        if not hwp.Open(str(src), "", open_arg):
            print("Open 실패", file=sys.stderr)
            return 1

        pages = 0
        try:
            pages = int(hwp.PageCount)
        except Exception:  # noqa: BLE001, S110
            pass

        hwp.SaveAs(str(dst), "PDF")
        if not dst.exists() or dst.stat().st_size == 0:
            print("PDF 미생성", file=sys.stderr)
            return 1

        print(f"pages={pages}")
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if hwp is not None:
            try:
                hwp.Quit()
            except Exception:  # noqa: BLE001, S110
                pass


if __name__ == "__main__":
    sys.exit(main())
