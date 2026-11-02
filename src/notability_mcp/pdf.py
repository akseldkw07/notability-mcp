"""Text and images of the PDF pages notes are built on, with PDFium."""

import threading

import pypdfium2 as pdfium
from PIL import Image

# PDFium is not thread-safe and the server may run tools in threads, so every PDFium object is
# created and closed under this lock (closing a document closes its pages and text pages).
_lock = threading.Lock()


def page_texts(data: bytes) -> list[str]:
    """The text of every page, in order."""
    with _lock:
        doc = pdfium.PdfDocument(data)
        try:
            return [page.get_textpage().get_text_bounded() for page in doc]
        finally:
            doc.close()


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    """Every page's size in points, in order."""
    with _lock:
        doc = pdfium.PdfDocument(data)
        try:
            return [tuple(page.get_size()) for page in doc]  # type: ignore[misc]
        finally:
            doc.close()


def render(data: bytes, index: int, size: tuple[int, int]) -> Image.Image | None:
    """Page `index` (0-based) drawn at `size` pixels; None if the PDF has no such page."""
    with _lock:
        doc = pdfium.PdfDocument(data)
        try:
            if not 0 <= index < len(doc):
                return None
            width, height = doc[index].get_size()
            if width <= 0 or height <= 0:
                return None
            # fit both edges, so an odd page shape can't make a huge bitmap
            bitmap = doc[index].render(scale=min(size[0] / width, size[1] / height))
            image = bitmap.to_pil().copy()  # to_pil may share the bitmap's buffer
            bitmap.close()
        finally:
            doc.close()
    return image.resize(size) if image.size != size else image
