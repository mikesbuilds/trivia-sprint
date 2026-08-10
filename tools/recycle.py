#!/usr/bin/env python3
"""Return previously-published questions to the bank for reuse.

This is a deliberate, consequential operation: it puts questions users may have
already seen back into circulation. Read the tradeoff before running it.

Only questions whose *most recent* airing is older than --min-age-days are
eligible, so recency is respected. Eligible prompts are appended to the bank and
their hashes are removed from bank/used.json, which is what "released back into
circulation" means mechanically. They are re-ledgered with a new date when they
are published again.

The policy for this repo is 365 days (the default). Note that this yields
nothing until early 2027: the Jan-Feb 2026 day files were a wholesale re-upload
of the Jan-Feb 2025 questions, so nearly every prompt has aired within the last
year. Treat this as a tool that switches itself on later, not a source of
runway today.

Ordering: recycled questions are inserted after curated ones but before anything
scraped from an external API, so the drain order is
curated -> recycled -> external.

    python3 tools/recycle.py --min-age-days 180 --dry-run
    python3 tools/recycle.py --min-age-days 180
"""

from __future__ import annotations

import argparse
import collections
import datetime
import sys
from zoneinfo import ZoneInfo

import triviakit as tk

# Older files predate the current seven-category naming.
CATEGORY_REMAP = {
    "World History": "History",
    "Math": "Science",
    "Movies": "Pop Culture",
    "Music": "Pop Culture",
    "Television": "Pop Culture",
    "Food": "Pop Culture",
    "Art": "Literature",
}


def most_recent_airings() -> dict[str, tuple[str, dict]]:
    """hash -> (last date aired, question) across every decodable day file."""
    airings: dict[str, tuple[str, dict]] = {}
    for path in sorted(tk.ROOT.glob("20[0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")):
        payload = tk.load_json(path, None)
        if not tk.decodes_for_app(payload):
            continue
        for raw in payload["questions"]:
            question = dict(raw)
            question["category"] = CATEGORY_REMAP.get(
                question["category"], question["category"])
            airings[tk.qhash(question["prompt"])] = (path.stem, question)
    return airings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-age-days", type=int, default=365,
                        help="only recycle questions last aired at least this "
                             "long ago (default: 365)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be recycled without changing anything")
    args = parser.parse_args()

    today = datetime.datetime.now(ZoneInfo("America/New_York")).date()
    cutoff = (today - datetime.timedelta(days=args.min_age_days)).isoformat()

    bank = tk.load_bank()
    used = tk.load_used()
    in_bank = {tk.qhash(q["prompt"]) for q in bank}

    eligible, too_recent, unusable = [], 0, 0
    for h, (last_aired, question) in most_recent_airings().items():
        if h in in_bank:
            continue
        if last_aired >= cutoff:
            too_recent += 1
            continue
        normalized = tk.normalize_question(question)
        if normalized is None:
            unusable += 1
            continue
        normalized["source"] = "recycled"
        normalized["last_aired"] = last_aired
        eligible.append((h, normalized))

    # Oldest first, so the least recently seen questions come back first.
    eligible.sort(key=lambda item: item[1]["last_aired"])

    by_category = collections.Counter(q["category"] for _, q in eligible)
    print(f"cutoff: last aired before {cutoff} ({args.min_age_days} days)")
    print(f"  eligible   : {len(eligible)}")
    print(f"  too recent : {too_recent}")
    print(f"  unusable   : {unusable} (malformed or unmappable category)")
    print("  by category: " + ", ".join(
        f"{c}={by_category.get(c, 0)}" for c in tk.CATEGORIES))

    if not eligible:
        print("\nnothing to recycle")
        return 0

    oldest = eligible[0][1]["last_aired"]
    newest = eligible[-1][1]["last_aired"]
    print(f"  aired between {oldest} and {newest}")

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    # Keep drain order meaningful: curated, then recycled, then external.
    curated = [q for q in bank if q.get("source") in (None, "curated")]
    external = [q for q in bank if q.get("source") not in (None, "curated", "recycled")]
    prior_recycled = [q for q in bank if q.get("source") == "recycled"]

    tk.save_bank(curated + prior_recycled + [q for _, q in eligible] + external)

    # Release them from the ledger; publishing re-adds them with a fresh date.
    for h, _ in eligible:
        used.pop(h, None)
    tk.save_used(used)

    new_bank = tk.load_bank()
    depth = {c: sum(1 for q in new_bank if q["category"] == c) for c in tk.CATEGORIES}
    print(f"\nbank now {len(new_bank)} questions = {min(depth.values())} days")
    print("  " + ", ".join(f"{c}={depth[c]}" for c in tk.CATEGORIES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
