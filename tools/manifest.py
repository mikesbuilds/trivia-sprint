#!/usr/bin/env python3
"""Regenerate index.json, the archive's coverage list.

`publish.py` and `backfill.py` already rewrite it whenever they change what is
covered. This exists for the case they do not cover: days removed by hand, as
happens when a stretch is cleared ahead of a rewrite.

    python3 tools/manifest.py
"""

from __future__ import annotations

import sys

import triviakit as tk


def main() -> int:
    manifest = tk.write_manifest()
    dates = manifest["dates"]
    if not dates:
        print("no days at or after the archive floor", file=sys.stderr)
        return 1

    # Report coverage as runs. A single count hides the gaps, and gaps are the
    # thing worth seeing before the archive goes out.
    from datetime import date, timedelta

    runs: list[list[str]] = []
    for d in dates:
        if runs and date.fromisoformat(d) - date.fromisoformat(runs[-1][1]) == timedelta(days=1):
            runs[-1][1] = d
        else:
            runs.append([d, d])

    print(f"archive_start {manifest['archive_start']}, {manifest['count']} day(s)")
    for start, end in runs:
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        print(f"  {start} .. {end}  ({span})" if start != end else f"  {start}")
    if len(runs) > 1:
        print(f"{len(runs) - 1} gap(s) in coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
