"""Rasterize a note page (its PDF background and ink) to PNG."""

import io
import itertools
import math
from collections.abc import Callable, Iterator

from PIL import Image, ImageChops, ImageDraw

from notability_mcp import bundle, pdf
from notability_mcp.bundle import (
    HIGHLIGHTER,
    IMAGE_BLOCK,
    Block,
    Ink,
    Page,
    Paper,
    Shape,
    decode_path,
)
from notability_mcp.text import Span, rulings
from notability_mcp.text import draw as draw_text

type Point = tuple[float, float]

MAX_EDGE = 1568  # Claude downscales images whose long edge is larger
_SUPERSAMPLE = 2  # draw at 2x, then downscale: cheap antialiasing
PAPER_TINT = (251, 248, 242)  # the app's paper texture, averaged
PAPER_LINE = (194, 192, 183)
PAPER_LINE_WIDTH = 0.5


def render_page(
    page: Page,
    max_edge: int = MAX_EDGE,
    background: bytes | None = None,
    image: Callable[[str], bytes | None] | None = None,
    text: list[Span] | None = None,
) -> bytes:
    """PNG of the page's ink and shapes, long edge `max_edge` px, over `background` (the page's
    PDF) or its paper, with pasted images when `image` (SHA-512 -> file bytes) is given and
    typed text (`text`, from text.page_spans).
    """
    out = io.BytesIO()
    render_image(page, max_edge, background, image, text).save(out, "PNG", optimize=True)
    return out.getvalue()


def render_image(
    page: Page,
    max_edge: int = MAX_EDGE,
    background: bytes | None = None,
    image: Callable[[str], bytes | None] | None = None,
    text: list[Span] | None = None,
) -> Image.Image:
    """The page drawn as in render_page, as an RGB image."""
    k = max_edge / max(page.width, page.height)
    size = (round(page.width * k), round(page.height * k))
    s = k * _SUPERSAMPLE
    canvas = (size[0] * _SUPERSAMPLE, size[1] * _SUPERSAMPLE)
    img = Image.new("RGB", canvas, "white")
    drawn = None
    if background and page.pdf:
        drawn = pdf.render(background, page.pdf.index, canvas, transparent=True)
    if page.paper:  # under a PDF page too: the app shows its paper through the PDF
        _draw_paper(img, page.paper, s, pattern=drawn is None)
    if drawn:
        img.paste(drawn, (0, 0), drawn if drawn.mode == "RGBA" else None)
    if image:
        for block in page.blocks:
            _paste_image(img, block, image, s)
    if text:
        draw_text(ImageDraw.Draw(img), text, s)
    marks = [*page.inks, *page.shapes]
    if highlights := [m for m in marks if m.tool == HIGHLIGHTER]:
        # multiplied in, like a real highlighter: the PDF text under it stays legible
        layer = Image.new("RGB", canvas, "white")
        _draw(layer, highlights, s)
        img = ImageChops.multiply(img, layer)
    _draw(img, [m for m in marks if m.tool != HIGHLIGHTER], s)
    return img.resize(size, Image.Resampling.LANCZOS)


def _draw_paper(img: Image.Image, paper: Paper, s: float, pattern: bool = True) -> None:
    """Fill the paper's colour and (with `pattern`) draw its lines, dots or grid, as measured
    on the app's own PDF exports: the paper is a cream texture (about PAPER_TINT) tinted by its
    colour, except legacy paper 13, which is plain white; the pattern is laid out so the
    page's centre falls mid-cell."""
    if paper.legacy != 13:
        r, g, b, _ = paper.color or (255, 255, 255, 255)
        tint = tuple(c * t // 255 for c, t in zip((r, g, b), PAPER_TINT, strict=True))
        img.paste(tint, (0, 0, *img.size))
    if not pattern:
        return
    if paper.flair not in (0, 1, 2) or not paper.spacing or paper.spacing <= 1:
        return
    w, h = img.size
    inset = 0.0 if paper.bleeds else 36 * s  # default margins

    def ticks(length: float) -> list[float]:
        return [t * s for t in rulings(length / s, paper.spacing or 0.0)]

    xs = [x for x in ticks(w) if inset <= x <= w - inset]
    ys = [y for y in ticks(h) if inset <= y <= h - inset]
    draw = ImageDraw.Draw(img)
    width = max(1, round(PAPER_LINE_WIDTH * s))
    if paper.flair == 1:
        r = max(1.0, 0.8 * s)
        for x in xs:
            for y in ys:
                draw.ellipse((x - r, y - r, x + r, y + r), fill=PAPER_LINE)
        return
    for y in ys:
        draw.line([(inset, y), (w - inset, y)], fill=PAPER_LINE, width=width)
    if paper.flair == 2:
        for x in xs:
            draw.line([(x, inset), (x, h - inset)], fill=PAPER_LINE, width=width)


def _draw(img: Image.Image, marks: list[Ink | Shape], s: float) -> None:
    draw = ImageDraw.Draw(img)
    for mark in marks:
        color = _color(mark.color, mark.tool)
        if isinstance(mark, Shape):
            outline = [(x * s, y * s) for x, y in _outline(mark)]
            if mark.fill and mark.fill[3] and len(outline) > 2:
                draw.polygon(outline, fill=_color(mark.fill, mark.tool))
            width = max(1, round(mark.width * (abs(mark.scale[0]) or 1.0) * s))
            for dash in _dashes(outline, mark.style, width):
                draw.line(dash, fill=color, width=width, joint="curve")
            continue
        for line, width in _polylines(mark):
            pts = [(x * s, y * s) for x, y in line]
            draw.line(pts, fill=color, width=max(1, round(width * s)), joint="curve")


def _color(rgba: tuple[int, int, int, int], tool: int) -> tuple[int, int, int]:
    r, g, b, a = rgba
    if tool == HIGHLIGHTER:  # its translucency, as the color it gives on white paper
        return (255 - (255 - r) * a // 255, 255 - (255 - g) * a // 255, 255 - (255 - b) * a // 255)
    return (r, g, b)


def _transform(mark: Ink | Shape) -> Callable[[float, float], Point]:
    (ox, oy), (sx, sy) = mark.origin, mark.scale
    sx, sy = sx or 1.0, sy or 1.0
    cos, sin = math.cos(mark.rotation), math.sin(mark.rotation)
    return lambda x, y: (ox + x * sx * cos - y * sy * sin, oy + x * sx * sin + y * sy * cos)


def _outline(shape: Shape) -> list[Point]:
    """The shape as a polyline on the page (closed for polygons and ellipses)."""
    f = _transform(shape)
    match shape.kind:
        case bundle.LINE if len(shape.points) > 2:  # a curve through its control points
            pts = [_bezier(shape.points, i / 16) for i in range(17)]
        case bundle.LINE:
            pts = list(shape.points)
        case bundle.POLYGON:
            pts = [*shape.points, *shape.points[:1]]
        case bundle.ELLIPSE:
            w, h = shape.size
            turns = [2 * math.pi * i / 64 for i in range(65)]
            pts = [(w / 2 * (1 + math.cos(a)), h / 2 * (1 + math.sin(a))) for a in turns]
        case _:
            pts = []
    return [f(x, y) for x, y in pts]


def _bezier(points: list[Point], t: float) -> Point:
    """The point at `t` on the Bézier curve with these control points (de Casteljau)."""
    while len(points) > 1:
        points = [
            ((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1])
            for a, b in itertools.pairwise(points)
        ]
    return points[0]


def _dashes(line: list[Point], style: int, width: int) -> Iterator[list[Point]]:
    """The polyline whole, or cut into dashes (style 2) or dots (style 3)."""
    if style not in (2, 3):
        if len(line) > 1:
            yield line
        return
    on, off = (3.0 * width, 2.0 * width) if style == 2 else (0.1, 2.0 * width)
    period, pos = on + off, 0.0
    for a, b in itertools.pairwise(line):
        length = math.dist(a, b)
        t = 0.0
        while t < length:
            phase = (pos + t) % period
            step = min((on - phase) if phase < on else (period - phase), length - t)
            if phase < on:
                p0 = (a[0] + (b[0] - a[0]) * t / length, a[1] + (b[1] - a[1]) * t / length)
                t1 = t + step
                p1 = (a[0] + (b[0] - a[0]) * t1 / length, a[1] + (b[1] - a[1]) * t1 / length)
                yield [p0, p1]
            t += max(step, 1e-6)
        pos += length


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
