"""Status-strip text: elapsed time and progress lines (same wording as the Windows app)."""

from __future__ import annotations

import math


def ago(seconds: float) -> str:
    s = int(seconds)
    return f"{s}초" if s < 60 else f"{s // 60}분 {s % 60:02d}초"


def progress_text(event: dict, elapsed: float) -> str:
    """'표 이미지 10/40쪽 (25%) · 남은 시간 약 3분' from one @@progress event and the seconds spent in its stage."""
    stage = event.get("stage", "")
    total, done = event.get("total"), event.get("done")
    if not total:
        return f"{stage} ({event['detail']})" if event.get("detail") else stage
    done = done or 0
    text = f"{stage} {done:,}/{total:,}쪽 ({round(100 * done / total)}%)"
    if done >= 5 and done < total and elapsed > 0:
        left = elapsed / done * (total - done)
        text += " · 곧 끝남" if left < 60 else f" · 남은 시간 약 {math.ceil(left / 60)}분"
    return text
