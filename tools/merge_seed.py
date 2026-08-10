#!/usr/bin/env python3
"""Merge curated seed files into the bank.

Drop any number of JSON files into bank/seed/ shaped like
{"questions": [{category, prompt, options, answerIndex}, ...]} and run this.
Anything malformed, duplicated, or already published is dropped with a reason.

    python3 tools/merge_seed.py
"""

from __future__ import annotations

import sys
from collections import Counter

import triviakit as tk

SEED_DIR = tk.ROOT / "bank" / "seed"


def main() -> int:
    bank = tk.load_bank()
    used = tk.load_used()

    seen = {tk.qhash(q["prompt"]) for q in bank}
    rejected = Counter()
    added = 0

    for path in sorted(SEED_DIR.glob("*.json")):
        payload = tk.load_json(path, None)
        if payload is None:
            print(f"{path.name}: unreadable, skipped", file=sys.stderr)
            rejected["unreadable file"] += 1
            continue

        file_added = 0
        for raw in payload.get("questions", []):
            question = tk.normalize_question(raw)
            if question is None:
                rejected["malformed"] += 1
                continue

            h = tk.qhash(question["prompt"])
            if h in used:
                rejected["already published"] += 1
                continue
            if h in seen:
                rejected["duplicate in seed"] += 1
                continue

            seen.add(h)
            question.setdefault("source", "curated")
            bank.append(question)
            file_added += 1

        print(f"{path.name}: +{file_added}")
        added += file_added

    tk.save_bank(bank)

    depth = {c: sum(1 for q in bank if q["category"] == c) for c in tk.CATEGORIES}
    print(f"\nadded {added} question(s)")
    if rejected:
        print("rejected: " + ", ".join(f"{k}={v}" for k, v in rejected.most_common()))
    print("bank depth: " + ", ".join(f"{c}={depth[c]}" for c in tk.CATEGORIES))
    print(f"covers {min(depth.values())} day(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
