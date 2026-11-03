"""Compare our PDF export of a note with the app's own ("Save as… > PDF"), page by page.

Reference PDFs come from scripts/save_as.py, which drives the app; they are the user's notes,
so they live in the cache directory (PARITY_DIR), never in the repo. tests/test_parity.py
checks every reference there against our export.
"""

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageFilter, ImageStat

from notability_mcp import export, local
from notability_mcp.bundle import decode_local

DPI = 50  # enough to see a missing line or a misplaced word, coarse enough to forgive antialiasing
THRESHOLD = 48  # grey levels: smaller differences (paper texture, antialiasing) don't count
MARKED = 230  # darker than this is ink, text, a line or a picture, not paper
MIN_MARKED = 0.01  # count at least 1% of the page as marked
TOLERANCE = 0.05  # a page passes when under 5% of its marked pixels differ
TONE_TOLERANCE = 3.0  # and its average colour is within 3 levels per channel (paper colour)


def parity_dir() -> Path:
    return local.cache_dir() / "parity"


def references() -> list[tuple[str, Path]]:
    """(note id, the app's PDF) for every reference saved by scripts/save_as.py."""
    return sorted((p.stem, p) for p in parity_dir().glob("*.pdf"))


def our_export(note_id: str, path: Path) -> None:
    """Export a note from the Mac app's local data with the same code as the export tool."""
    log = local.change_log(note_id)
    if log is None:
        raise FileNotFoundError(f"no change log for note {note_id}")
    note = decode_local(note_id, log)
    export.write_pdf(note, export.default_pages(note), local.asset, path, image=local.asset)


@dataclass
class PageDiff:
    page: int
    ours: tuple[float, float]  # page size in points
    app: tuple[float, float]
    diff: float  # differing pixels, as a fraction of the marked ones (see page_diff)
    tone: float = 0.0  # largest difference of the pages' average R, G or B

    @property
    def ok(self) -> bool:
        same_size = all(abs(a - b) <= 1.0 for a, b in zip(self.ours, self.app, strict=True))
        return same_size and self.diff <= TOLERANCE and self.tone <= TONE_TOLERANCE


@dataclass
class Report:
    pages_ours: int
    pages_app: int
    diffs: list[PageDiff]

    @property
    def ok(self) -> bool:
        return self.pages_ours == self.pages_app and all(d.ok for d in self.diffs)

    def summary(self) -> str:
        worst = max(self.diffs, key=lambda d: d.diff, default=None)
        head = f"pages {self.pages_ours} (ours) vs {self.pages_app} (app)"
        if worst is None:
            return head
        bad = [d.page for d in self.diffs if not d.ok]
        return (
            f"{head}; worst page {worst.page}: {worst.diff:.1%} of marks differ, tone "
            f"{worst.tone:.1f}; failing pages {bad or 'none'}"
        )


def _render(page: pdfium.PdfPage, size: tuple[int, int] | None = None) -> Image.Image:
    """The page at DPI, area-averaged down from 3x: drawn straight at a low resolution, a
    vector hairline still takes a whole pixel, so the app's vector pages would look darker
    than our raster ones."""
    big = page.render(scale=3 * DPI / 72).to_pil().convert("RGB")  # pyright: ignore[reportArgumentType]  # fmt: skip
    w, h = page.get_size()
    return big.resize(size or (round(w * DPI / 72), round(h * DPI / 72)), Image.Resampling.BOX)


def _grey(img: Image.Image) -> Image.Image:
    return img.convert("L").filter(ImageFilter.GaussianBlur(1))


def tone(ours: Image.Image, app: Image.Image) -> float:
    """Largest difference between two RGB images' average red, green or blue."""
    a, b = ImageStat.Stat(ours).mean, ImageStat.Stat(app).mean
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def page_diff(ours: Image.Image, app: Image.Image) -> tuple[float, Image.Image]:
    """How different two greyscale page images of the same size are: pixels that differ by
    more than THRESHOLD, as a fraction of the pixels marked (darker than MARKED) in either
    image, so a missing line counts for as much as it is of the page's ink, not of its area.
    Also returns a mask of the differing pixels (white)."""
    cut = [0] * (THRESHOLD + 1) + [255] * (255 - THRESHOLD)
    mask = ImageChops.difference(ours, app).point(cut)
    dark = [255] * MARKED + [0] * (256 - MARKED)
    marked = ImageChops.lighter(ours.point(dark), app.point(dark))
    floor = MIN_MARKED * ours.width * ours.height  # a near-empty page isn't judged on a few pixels
    return mask.histogram()[255] / max(1, floor, marked.histogram()[255]), mask


def compare(ours: bytes, app: bytes, diff_dir: Path | None = None) -> Report:
    """Compare two PDFs page by page; with `diff_dir`, also write each page's images and diff
    mask there (ours | app | diff) to look at."""
    a, b = pdfium.PdfDocument(ours), pdfium.PdfDocument(app)
    diffs = []
    for i in range(min(len(a), len(b))):
        pa, pb = a[i], b[i]
        rb = _render(pb)
        ra = _render(pa, rb.size)
        ga, gb = _grey(ra), _grey(rb)
        fraction, mask = page_diff(ga, gb)
        diffs.append(PageDiff(i + 1, pa.get_size(), pb.get_size(), fraction, tone(ra, rb)))
        if diff_dir:
            diff_dir.mkdir(parents=True, exist_ok=True)
            strip = Image.new("L", (gb.width * 3 + 20, gb.height), 128)
            for k, im in enumerate((ga, gb, mask)):
                strip.paste(im, (k * (gb.width + 10), 0))
            strip.save(diff_dir / f"page-{i + 1:03}.png")
    return Report(len(a), len(b), diffs)
