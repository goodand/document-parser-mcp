"""Synthetic text image for OCR tests (rendered with PyMuPDF, Malgun Gothic)."""

import pymupdf


def text_png(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=90)
    page.insert_font(fontname="mg", fontfile=r"C:\Windows\Fonts\malgun.ttf")
    page.insert_text((20, 55), text, fontname="mg", fontsize=28)
    return page.get_pixmap(dpi=150).tobytes("png")
