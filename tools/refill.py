#!/usr/bin/env python3
"""Top the question bank up from free, key-less trivia APIs.

This is the safety net, not the primary source: it exists so the app can never
run dry unattended. Curated questions already in the bank are always drawn
first, because publish.py drains in insertion order.

    python3 tools/refill.py --min-per-category 60
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import triviakit as tk

USER_AGENT = "trivia-sprint-refill/1.0 (+https://github.com/mikesbuilds/trivia-sprint)"

# OpenTDB category ids. Several of our categories span more than one of theirs.
OPENTDB = {
    "History": [23],
    "Science": [17, 19],       # Science & Nature, Mathematics
    "Sports": [21],
    "Pop Culture": [11, 12, 14, 26],  # Film, Music, Television, Celebrities
    "Geography": [22],
    "Literature": [10],        # Books
    "Technology": [18, 30],    # Computers, Gadgets
}

# The Trivia API has no technology category, so Technology relies on OpenTDB.
TRIVIA_API = {
    "History": ["history"],
    "Science": ["science"],
    "Sports": ["sport_and_leisure"],
    "Pop Culture": ["film_and_tv", "music"],
    "Geography": ["geography"],
    "Literature": ["arts_and_literature"],
}

OPENTDB_COOLDOWN = 5.5  # OpenTDB rate-limits to one request per 5 seconds.


def get_json(url: str, timeout: int = 20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  fetch failed ({url.split('?')[0]}): {exc}", file=sys.stderr)
        return None


def to_question(category: str, prompt: str, correct: str, wrong: list[str],
                source: str) -> dict | None:
    """Assemble into the app's schema. answerIndex is provisional - publish.py
    re-places the correct answer when it builds the day."""
    if len(wrong) < tk.OPTIONS_PER_QUESTION - 1:
        return None
    options = [correct] + wrong[: tk.OPTIONS_PER_QUESTION - 1]
    return tk.normalize_question({
        "category": category,
        "prompt": prompt,
        "options": options,
        "answerIndex": 0,
        "source": source,
    })


def from_opentdb(category: str, wanted: int) -> list[dict]:
    out = []
    for cat_id in OPENTDB.get(category, []):
        if len(out) >= wanted:
            break
        url = ("https://opentdb.com/api.php?"
               + urllib.parse.urlencode({"amount": 50, "category": cat_id,
                                         "type": "multiple"}))
        payload = get_json(url)
        time.sleep(OPENTDB_COOLDOWN)
        if not payload or payload.get("response_code") != 0:
            continue
        for row in payload.get("results", []):
            q = to_question(category, row.get("question", ""),
                            row.get("correct_answer", ""),
                            list(row.get("incorrect_answers", [])), "opentdb")
            if q:
                out.append(q)
    return out


def from_trivia_api(category: str, wanted: int) -> list[dict]:
    out = []
    for slug in TRIVIA_API.get(category, []):
        if len(out) >= wanted:
            break
        url = ("https://the-trivia-api.com/v2/questions?"
               + urllib.parse.urlencode({"limit": 50, "categories": slug,
                                         "types": "text_choice"}))
        payload = get_json(url)
        if not isinstance(payload, list):
            continue
        for row in payload:
            prompt = (row.get("question") or {}).get("text", "")
            q = to_question(category, prompt, row.get("correctAnswer", ""),
                            list(row.get("incorrectAnswers", [])), "trivia-api")
            if q:
                out.append(q)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-per-category", type=int, default=60,
                        help="refill any category below this depth (default: 60)")
    parser.add_argument("--rounds", type=int, default=3,
                        help="fetch attempts per short category (default: 3)")
    args = parser.parse_args()

    bank = tk.load_bank()
    used = tk.load_used()

    seen = set(used) | {tk.qhash(q["prompt"]) for q in bank}
    depth = {c: sum(1 for q in bank if q["category"] == c) for c in tk.CATEGORIES}

    print("current bank depth: " + ", ".join(f"{c}={depth[c]}" for c in tk.CATEGORIES))

    added_total = 0
    for category in tk.CATEGORIES:
        need = args.min_per_category - depth[category]
        if need <= 0:
            continue
        print(f"{category}: need {need} more")

        added = 0
        for _ in range(args.rounds):
            if added >= need:
                break
            candidates = from_trivia_api(category, need - added)
            candidates += from_opentdb(category, need - added)
            random.shuffle(candidates)

            fresh = 0
            for q in candidates:
                if added >= need:
                    break
                h = tk.qhash(q["prompt"])
                if h in seen:
                    continue
                seen.add(h)
                bank.append(q)
                added += 1
                fresh += 1
            # Both sources return random samples; no new questions means the
            # reachable pool for this category is effectively drained.
            if fresh == 0:
                print(f"  no new questions available for {category}")
                break

        print(f"  added {added}")
        added_total += added

    if added_total:
        tk.save_bank(bank)

    final = {c: sum(1 for q in bank if q["category"] == c) for c in tk.CATEGORIES}
    print("new bank depth: " + ", ".join(f"{c}={final[c]}" for c in tk.CATEGORIES))
    print(f"added {added_total} question(s); "
          f"buffer covers {min(final.values())} more day(s)")

    if min(final.values()) < 14:
        print("::warning::bank still low after refill - add curated questions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
