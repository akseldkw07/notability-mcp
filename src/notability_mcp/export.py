"""Write a note as a PDF of rendered pages, laid out like the app's own "Save as… PDF"."""

import io
from collections.abc import Callable
from pathlib import Path

from notability_mcp import pdf
from notability_mcp.bundle import Note, exported_page_count
from notability_mcp.render import render_image
from notability_mcp.text import body_spans, page_spans

EXPORT_DPI = 150


def default_pages(note: Note) -> list[int]:
    """The pages the app's own export has: every page, less a last one that's empty."""
    return list(range(1, exported_page_count(note.pages) + 1))


def write_pdf(
    note: Note,
    numbers: list[int],
    asset: Callable[[str], bytes | None],
    path: Path,
    image: Callable[[str], bytes | None] | None = None,
) -> int:
    """Render pages `numbers` of `note` into a PDF at `path`: ink, shapes, typed text and
    pasted images over each page's PDF or paper. `asset` fetches a PDF background by
    SHA-512, `image` a pasted image. Returns the page count."""
    body = body_spans(note)

    def rendered():
        backgrounds: dict[str, bytes | None] = {}
        for number in numbers:
            page = note.pages[number - 1]
            background = None
            if page.pdf:
                if page.pdf.asset not in backgrounds:
                    backgrounds[page.pdf.asset] = asset(page.pdf.asset)
                background = backgrounds[page.pdf.asset]
            # the Mac app's File > Export > PDF keeps each page's own size (its iPad export
            # scales pages to US Letter width)
            width, height = page.width, page.height
            edge = round(max(width, height) / 72 * EXPORT_DPI)
            out = io.BytesIO()
            spans = page_spans(note, page, body)
            render_image(page, edge, background, image, spans).save(out, "JPEG", quality=85)
            yield out.getvalue(), width, height

    return pdf.write_images(rendered(), path)
