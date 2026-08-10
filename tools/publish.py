#!/usr/bin/env python3
"""Keep a rolling buffer of published day files ahead of today.

Run daily. It tops the buffer back up to --days of future coverage, so a run
that fails (or a workflow that breaks entirely) costs buffer depth rather than
causing an outage the next morning.

    python3 tools/publish.py --days 30
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import triviakit as tk

# The content day is pinned to New York so a run at 00:30 UTC still publishes
# for the right local date, regardless of where the runner or the user is.
CONTENT_TZ = ZoneInfo("America/New_York")


def today() -> date:
    from datetime import datetime

    return datetime.now(CONTENT_TZ).date()


def answer_positions(seed: str) -> list[int]:
    """Seven answer slots spread as evenly as four positions allow (2/2/2/1).

    Models and scraped sources both skew heavily toward the first option; re-
    placing the answer ourselves is what keeps the long-run distribution flat.
    """
    bag = [i for i in range(tk.OPTIONS_PER_QUESTION) for _ in range(2)]
    random.Random(f"pos:{seed}").shuffle(bag)
    return bag[: tk.QUESTIONS_PER_DAY]


def place_answer(question: dict, target: int, rng: random.Random) -> dict:
    correct = question["options"][question["answerIndex"]]
    others = [o for i, o in enumerate(question["options"]) if i != question["answerIndex"]]
    rng.shuffle(others)
    options = others[:target] + [correct] + others[target:]
    return {**question, "options": options, "answerIndex": target}


def publishable(question: dict) -> dict:
    """Strip a bank entry down to the keys the app reads.

    `source` is bookkeeping and stays out of published files. `fact` is carried
    through when present — without this the review screen would never see one,
    since the bank entry is otherwise discarded here.
    """
    out = {k: question[k] for k in ("category", "prompt", "options", "answerIndex")}
    if question.get("fact"):
        out["fact"] = question["fact"]
    return out


def build_day(target_date: str, pool: dict[str, list[dict]]) -> tuple[dict | None, str]:
    """Draw one question per category. Returns (payload, error_message)."""
    short = [c for c in tk.CATEGORIES if not pool.get(c)]
    if short:
        return None, f"bank exhausted for: {', '.join(short)}"

    rng = random.Random(f"day:{target_date}")
    positions = answer_positions(target_date)

    questions = []
    for category, target in zip(tk.CATEGORIES, positions):
        drawn = pool[category].pop()
        questions.append(place_answer(drawn, target, rng))

    return {
        "date": target_date,
        "categories": list(tk.CATEGORIES),
        "questions": [publishable(q) for q in questions],
    }, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30,
                        help="days of future coverage to maintain (default: 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written without writing")
    args = parser.parse_args()

    bank = tk.load_bank()
    used = tk.load_used()
    have = tk.published_dates()

    start = today()
    wanted = [(start + timedelta(days=i)).isoformat() for i in range(args.days + 1)]
    missing = [d for d in wanted if d not in have]

    if not missing:
        print(f"buffer full: {args.days} days covered from {start.isoformat()}")
        return 0

    # Group the bank by category, skipping anything already published. Reversed
    # so .pop() draws oldest-first and the bank drains in insertion order.
    #
    # `used` only catches a prompt republished verbatim. Repeats far more often
    # arrive reworded ("Which metal is liquid at room temperature?" after
    # "Which metallic element...") or inverted, so also compare against the last
    # year of content and the unaired buffer by meaning, not just by hash.
    history = tk.recent_keys(start.isoformat())
    buffered = [tk.compare_key(q)
                for questions in tk.load_days(start=start.isoformat()).values()
                for q in questions]

    pool: dict[str, list[dict]] = {c: [] for c in tk.CATEGORIES}
    kept_hashes: set[str] = set()
    seen_keys: list[dict] = history + buffered
    repeats_skipped = 0
    for q in bank:
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

    print(f"missing {len(missing)} day(s); bank has "
          + ", ".join(f"{c}={len(pool[c])}" for c in tk.CATEGORIES))
    if repeats_skipped:
        print(f"held back {repeats_skipped} bank question(s) as repeats of the "
              f"last {tk.REPEAT_WINDOW_DAYS} days")

    written, drawn_hashes = [], set()
    for target_date in missing:
        payload, error = build_day(target_date, pool)
        if payload is None:
            print(f"stopping at {target_date}: {error}", file=sys.stderr)
            print("run tools/refill.py to top the bank up", file=sys.stderr)
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
        # Served as a fallback for any date the app requests that has no file.
        newest = max(tk.published_dates())
        tk.save_json(tk.ROOT / "latest.json",
                     tk.load_json(tk.day_path(newest), {}))

    verb = "would publish" if args.dry_run else "published"
    print(f"{verb} {len(written)} day(s): {written[0]} .. {written[-1]}")

    remaining = min(len(v) for v in pool.values())
    print(f"bank depth after run: {remaining} more day(s)")
    if remaining < 14:
        print(f"::warning::bank is low ({remaining} days) - refill soon")

    return 0


if __name__ == "__main__":
    sys.exit(main())
