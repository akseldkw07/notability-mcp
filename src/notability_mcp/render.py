"""Rasterize a note page (its PDF background and ink) to PNG."""

import io
import math
from collections.abc import Callable, Iterator

from PIL import Image, ImageChops, ImageDraw

from notability_mcp import pdf
from notability_mcp.bundle import HIGHLIGHTER, IMAGE_BLOCK, Block, Ink, Page, decode_path

type Point = tuple[float, float]

MAX_EDGE = 1568  # Claude downscales images whose long edge is larger
_SUPERSAMPLE = 2  # draw at 2x, then downscale: cheap antialiasing


def render_page(
    page: Page,
    max_edge: int = MAX_EDGE,
    background: bytes | None = None,
    image: Callable[[str], bytes | None] | None = None,
) -> bytes:
    """PNG of the page's ink, long edge `max_edge` px, over `background` (the page's PDF) or white,
    with pasted images when `image` (SHA-512 -> file bytes) is given.

    Shapes and typed text are not drawn.
    """
    k = max_edge / max(page.width, page.height)
    size = (round(page.width * k), round(page.height * k))
    s = k * _SUPERSAMPLE
    canvas = (size[0] * _SUPERSAMPLE, size[1] * _SUPERSAMPLE)
    img = Image.new("RGB", canvas, "white")
    if background and page.pdf and (drawn := pdf.render(background, page.pdf.index, canvas)):
        img.paste(drawn)
    if image:
        for block in page.blocks:
            _paste_image(img, block, image, s)
    if highlights := [ink for ink in page.inks if ink.tool == HIGHLIGHTER]:
        # multiplied in, like a real highlighter: the PDF text under it stays legible
        layer = Image.new("RGB", canvas, "white")
        _draw(layer, highlights, s)
        img = ImageChops.multiply(img, layer)
    _draw(img, [ink for ink in page.inks if ink.tool != HIGHLIGHTER], s)
    img = img.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _draw(img: Image.Image, inks: list[Ink], s: float) -> None:
    draw = ImageDraw.Draw(img)
    for ink in inks:
        color = _color(ink)
        for line, width in _polylines(ink):
            pts = [(x * s, y * s) for x, y in line]
            draw.line(pts, fill=color, width=max(1, round(width * s)), joint="curve")


def _color(ink: Ink) -> tuple[int, int, int]:
    r, g, b, a = ink.color
    if ink.tool == HIGHLIGHTER:  # its translucency, as the color it gives on white paper
        return (255 - (255 - r) * a // 255, 255 - (255 - g) * a // 255, 255 - (255 - b) * a // 255)
    return (r, g, b)


def _transform(ink: Ink) -> Callable[[float, float], Point]:
    (ox, oy), (sx, sy) = ink.origin, ink.scale
    sx, sy = sx or 1.0, sy or 1.0
    cos, sin = math.cos(ink.rotation), math.sin(ink.rotation)
    return lambda x, y: (ox + x * sx * cos - y * sy * sin, oy + x * sx * sin + y * sy * cos)


def _polylines(ink: Ink) -> Iterator[tuple[list[Point], float]]:
    """Flatten the ink's Bézier path into polylines, each with one stroke width."""
    f = _transform(ink)
    scale = abs(ink.scale[0]) or 1.0
    line: list[Point] = []
    width = ink.width
    for raw in ink.paths:
        for kind, pts, w in decode_path(raw):
            pts = [f(x, y) for x, y in pts]
            width = w or width
            if kind in (3, 7) or not line:
                if len(line) > 1:
                    yield line, width * scale
                line = [pts[0]]
                if kind in (3, 7):
                    continue
            line.extend(_flatten(line[-1], kind, pts))
    if len(line) > 1:
        yield line, width * scale
    elif line:  # a dot
        yield [line[0], line[0]], width * scale


def _flatten(start: Point, kind: int, pts: list[Point]) -> list[Point]:
    if kind in (0, 4):  # cubic
        (x1, y1), (x2, y2), (x3, y3) = pts
        ts = (0.25, 0.5, 0.75, 1.0)
        return [
            (
                (1 - t) ** 3 * start[0]
                + 3 * (1 - t) ** 2 * t * x1
                + 3 * (1 - t) * t * t * x2
                + t**3 * x3,
                (1 - t) ** 3 * start[1]
                + 3 * (1 - t) ** 2 * t * y1
                + 3 * (1 - t) * t * t * y2
                + t**3 * y3,
            )
            for t in ts
        ]
    if kind in (1, 5):  # quadratic
        (x1, y1), (x2, y2) = pts
        ts = (1 / 3, 2 / 3, 1.0)
        return [
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * x1 + t * t * x2,
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * y1 + t * t * y2,
            )
            for t in ts
        ]
    return [pts[-1]]  # line


def _paste_image(
    canvas: Image.Image, block: Block, image: Callable[[str], bytes | None], s: float
) -> None:
    """Draw an image block: cropped, resized to its size on the page, at its origin."""
    if block.kind != IMAGE_BLOCK or not block.asset:
        return
    try:
        data = image(block.asset)
        if not data:
            return
        pic = Image.open(io.BytesIO(data))
        pic.load()
    except Exception:  # a missing or unreadable image leaves a gap, not a failed page
        return
    if block.crop and block.crop[2] > 0 and block.crop[3] > 0:
        x, y, w, h = block.crop
        pic = pic.crop((round(x), round(y), round(x + w), round(y + h)))
    width = block.size[0] * block.scale[0] * s
    height = block.size[1] * block.scale[1] * s
    if width < 1 or height < 1 or width * height > 64e6:  # bounds a corrupt or hostile size
        return
    pic = pic.convert("RGBA").resize((round(width), round(height)), Image.Resampling.LANCZOS)
    if block.rotation:
        pic = pic.rotate(-math.degrees(block.rotation), expand=True)
    canvas.paste(pic, (round(block.origin[0] * s), round(block.origin[1] * s)), pic)
