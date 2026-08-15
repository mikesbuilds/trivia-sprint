#!/usr/bin/env python3
"""Write day files for dates in the past.

`publish.py` only ever fills forward from today, which is the right behaviour
for a rolling buffer but leaves no way to repair or replace history. The
archive feature in the app makes past days playable, so days that were never
written — or were written badly — now matter as much as tomorrow's.

    python3 tools/backfill.py --start 2026-01-01 --end 2026-01-31 --force
    python3 tools/backfill.py --dates 2026-06-02

By default an existing day the app can decode is left alone; --force replaces
it. Replacing is safe for players: a stored DayResult records the score and the
per-question marks, never the question text, so no history is invalidated.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta

import triviakit as tk
from publish import answer_positions, place_answer, publishable


def parse_date(value: str) -> date:
    try:
        y, m, d = (int(part) for part in value.split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"expected yyyy-mm-dd, got {value!r}")


def target_dates(args) -> list[str]:
    if args.dates:
        return sorted({d.isoformat() for d in args.dates})
    if not (args.start and args.end):
        return []
    if args.end < args.start:
        return []
    span = (args.end - args.start).days
    return [(args.start + timedelta(days=i)).isoformat() for i in range(span + 1)]


def build_day(target_date: str, pool: dict[str, list[dict]]) -> tuple[dict | None, str]:
    """Draw one question per category for a single date.

    Deliberately mirrors publish.build_day, including the seeded answer spread.
    A backfilled day that skewed toward option A would reintroduce exactly the
    bias the published buffer was fixed to remove.
    """
    short = [c for c in tk.CATEGORIES if not pool.get(c)]
    if short:
        return None, f"bank exhausted for: {', '.join(short)}"

    rng = random.Random(f"day:{target_date}")
    positions = answer_positions(target_date)

    questions = []
    for category, target in zip(tk.CATEGORIES, positions):
        questions.append(place_answer(pool[category].pop(), target, rng))

    return {
        "date": target_date,
        "categories": list(tk.CATEGORIES),
        "questions": [publishable(q) for q in questions],
    }, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, help="first date, yyyy-mm-dd")
    parser.add_argument("--end", type=parse_date, help="last date, inclusive")
    parser.add_argument("--dates", type=parse_date, nargs="+",
                        help="explicit dates instead of a range")
    parser.add_argument("--source", help="only draw bank questions with this source tag")
    parser.add_argument("--force", action="store_true",
                        help="replace days that already have a decodable file")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written without writing")
    args = parser.parse_args()

    wanted = target_dates(args)
    if not wanted:
        print("nothing to do: pass --start/--end or --dates", file=sys.stderr)
        return 1

    have = tk.published_dates()
    targets = wanted if args.force else [d for d in wanted if d not in have]
    if not targets:
        print(f"all {len(wanted)} day(s) already present; pass --force to replace")
        return 0

    bank = tk.load_bank()
    used = tk.load_used()

    # Compare against every day except the ones being rewritten. Including a
    # target would measure the new content against the content it replaces,
    # which is about to stop existing.
    replaced = set(targets)
    seen_keys = [tk.compare_key(q)
                 for day, questions in tk.load_days().items() if day not in replaced
                 for q in questions]

    # The hashes of questions currently sitting on the days being replaced. They
    # aired, so they stay in used.json and are never redrawn, but they must not
    # block the new draw either.
    pool: dict[str, list[dict]] = {c: [] for c in tk.CATEGORIES}
    kept_hashes: set[str] = set()
    repeats_skipped = 0
    for q in bank:
        if args.source and q.get("source") != args.source:
            continue
        h = tk.qhash(q["prompt"])
        if h in used or h in kept_hashes or q["category"] not in pool:
            continue
        key = tk.compare_key(q)
        if tk.first_duplicate(key, seen_keys)[0]:
            repeats_skipped += 1
            continue
        kept_hashes.add(h)
        seen_keys.append(key)
        pool[q["category"]].append(q)
    for questions in pool.values():
        questions.reverse()

    scope = f" tagged {args.source}" if args.source else ""
    print(f"filling {len(targets)} day(s); bank{scope} has "
          + ", ".join(f"{c}={len(pool[c])}" for c in tk.CATEGORIES))
    if repeats_skipped:
        print(f"held back {repeats_skipped} bank question(s) as repeats")

    written, drawn_hashes = [], set()
    for target_date in targets:
        payload, error = build_day(target_date, pool)
        if payload is None:
            print(f"stopping at {target_date}: {error}", file=sys.stderr)
            break

        errors = tk.validate_day(payload, target_date)
        if errors:
            print(f"REJECTED {target_date}:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

        for q in payload["questions"]:
            h = tk.qhash(q["prompt"])
            used[h] = target_date
            drawn_hashes.add(h)

        if not args.dry_run:
            tk.save_json(tk.day_path(target_date), payload)
        written.append(target_date)

    if not written:
        return 1

    if not args.dry_run:
        tk.save_bank([q for q in bank if tk.qhash(q["prompt"]) not in drawn_hashes])
        tk.save_used(used)

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {len(written)} day(s): {written[0]} .. {written[-1]}")
    if len(written) < len(targets):
        print(f"{len(targets) - len(written)} day(s) left unfilled", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
