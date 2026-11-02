"""Synthetic copies of the Mac app's search-index files (NotesSearchIndex/), in both formats:
binary plists (until about April 2026) and FlatBuffers (since). Layouts are in appindex.py."""

import plistlib
from pathlib import Path

import flatbuffers

from notability_mcp.bundle import SeqId


def _envelope(b: flatbuffers.Builder, versions: tuple[int, int], payload: int) -> bytes:
    b.StartObject(4)
    b.PrependUint8Slot(0, versions[0], 0)
    b.PrependUint8Slot(1, versions[1], 0)
    b.PrependUOffsetTRelativeSlot(2, payload, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def _vector(b: flatbuffers.Builder, offsets: list[int]) -> int:
    b.StartVector(4, len(offsets), 4)
    for off in reversed(offsets):
        b.PrependUOffsetTRelative(off)
    return b.EndVector()


def _seq_id(b: flatbuffers.Builder, seq: SeqId) -> int:
    site, ts, index = seq
    b.Prep(4, 12)
    b.PrependUint32(index)
    b.PrependUint32(ts)
    b.Pad(2)
    b.PrependUint16(site)
    return b.Offset()


def handwriting_fb(pages: list[tuple[SeqId, str]], versions=(4, 5)) -> bytes:
    """Pages with recognized text: {0: [page {0: page id, 1: text, 3: sha256}]}."""
    b = flatbuffers.Builder(256)
    tables = []
    for seq, text in pages:
        s, digest = b.CreateString(text), b.CreateByteVector(bytes(32))
        b.StartObject(4)
        b.PrependStructSlot(0, _seq_id(b, seq), 0)
        b.PrependUOffsetTRelativeSlot(1, s, 0)
        b.PrependUOffsetTRelativeSlot(3, digest, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    return _envelope(b, versions, b.EndObject())


def image_fb(texts: list[str], versions=(1, 2)) -> bytes:
    """{0: [image {0: id (2 x u32), 1: text}]}."""
    b = flatbuffers.Builder(256)
    tables = []
    for i, text in enumerate(texts):
        s = b.CreateString(text)
        b.StartObject(4)
        b.Prep(4, 8)
        b.PrependUint32(i)
        b.PrependUint32(1)
        b.PrependStructSlot(0, b.Offset(), 0)
        b.PrependUOffsetTRelativeSlot(1, s, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    return _envelope(b, versions, b.EndObject())


def recording_fb(
    jobs: list[tuple[str, str, list[tuple[float, float, str]]]], versions=(2, 3)
) -> bytes:
    """{0: titles, 1: [job {0: sha512 (64 bytes), 1: language, 2: [segment {0: text, 1: start,
    2: end}]}]}."""
    b = flatbuffers.Builder(256)
    job_tables = []
    for sha512, language, segments in jobs:
        segs = []
        for start, end, text in segments:
            s = b.CreateString(f" {text} ")  # the app's segment text has stray spaces
            b.StartObject(3)
            b.PrependUOffsetTRelativeSlot(0, s, 0)
            b.PrependFloat64Slot(1, start, 0)
            b.PrependFloat64Slot(2, end, 0)
            segs.append(b.EndObject())
        seg_vec, lang = _vector(b, segs), b.CreateString(language)
        b.StartObject(3)
        b.Prep(1, 64)
        for byte in reversed(bytes.fromhex(sha512)):
            b.PrependUint8(byte)
        b.PrependStructSlot(0, b.Offset(), 0)
        b.PrependUOffsetTRelativeSlot(1, lang, 0)
        b.PrependUOffsetTRelativeSlot(2, seg_vec, 0)
        job_tables.append(b.EndObject())
    vec, titles = _vector(b, job_tables), b.CreateString("Recording 1")
    b.StartObject(3)
    b.PrependUOffsetTRelativeSlot(0, titles, 0)
    b.PrependUOffsetTRelativeSlot(1, vec, 0)
    return _envelope(b, versions, b.EndObject())


def handwriting_plist(pages: dict[int, str]) -> bytes:
    """The old format: {pages: {"<0-based page>": {text}}}."""
    return plistlib.dumps(
        {"pages": {str(k): {"text": v} for k, v in pages.items()}}, fmt=plistlib.FMT_BINARY
    )


def write(library: Path, indexer: str, note_id: str, name: str, data: bytes) -> Path:
    path = library / "NotesSearchIndex" / indexer / note_id.upper() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
