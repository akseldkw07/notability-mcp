"""Compare our export of each saved reference with the app's, and print one line per note.

    uv run scripts/parity_report.py              # every reference
    uv run scripts/parity_report.py 15d3 a7ea    # notes whose id starts with these
    uv run scripts/parity_report.py --diffs      # also write page images (parity/diffs/<id>/)

A quicker read than tests/test_parity.py when tuning the renderer. References come from
scripts/save_as.py.
"""

import argparse
import sys
import tempfile
from pathlib import Path

from notability_mcp import parity


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("prefix", nargs="*", help="only notes whose id starts with one of these")
    ap.add_argument("--diffs", action="store_true", help="write ours | app | diff page images")
    args = ap.parse_args()
    failed = 0
    refs = [
        (n, p)
        for n, p in parity.references()
        if not args.prefix or n.startswith(tuple(args.prefix))
    ]
    for note_id, app_pdf in refs:
        with tempfile.TemporaryDirectory() as tmp:
            ours = Path(tmp) / "ours.pdf"
            parity.our_export(note_id, ours)
            diff_dir = parity.parity_dir() / "diffs" / note_id if args.diffs else None
            report = parity.compare(ours.read_bytes(), app_pdf.read_bytes(), diff_dir)
        failed += not report.ok
        print(f"{note_id[:8]} {'OK ' if report.ok else 'BAD'} {report.summary()}")
    print(f"{len(refs) - failed}/{len(refs)} match")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
