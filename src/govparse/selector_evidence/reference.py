"""Render the original into a vector PDF with the same geometry used for selection."""
from pathlib import Path

import pymupdf
import rhwp


def render_reference_pdf(source: Path, destination: Path) -> Path:
    """Write into the caller's private run directory; pages retain SVG dimensions."""
    document = rhwp.parse(str(source))
    with pymupdf.open() as combined:
        for number in range(document.page_count):
            svg = document.render_svg(number).encode("utf-8")
            with pymupdf.open(stream=svg, filetype="svg") as drawing:
                with pymupdf.open(stream=drawing.convert_to_pdf(), filetype="pdf") as page:
                    combined.insert_pdf(page)
        combined.save(destination, garbage=4, deflate=True)
    return destination
