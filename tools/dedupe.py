#!/usr/bin/env python3
"""Find and replace repeated questions in day files that have not yet aired.

`bank/used.json` blocks a prompt from being published twice verbatim, but a
repeat usually arrives reworded or inverted instead, and slips straight past a
hash. This audits the unaired buffer against everything from the last 365 days
and swaps offenders for clean questions from the bank.

    python3 tools/dedupe.py --report
    python3 tools/dedupe.py --apply --dry-run
    python3 tools/dedupe.py --apply

Only unaired days are touched. A replacement keeps the outgoing question's
`answerIndex`, so the even A/B/C/D spread the publisher works to maintain is
preserved exactly.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date

import triviakit as tk


def today() -> str:
    return date.today().isoformat()


def place_answer(question: dict, target: int, rng: random.Random) -> dict:
    """Rebuild options so the correct answer sits at `target`."""
    correct = question["options"][question["answerIndex"]]
    others = [o for i, o in enumerate(question["options"]) if i != question["answerIndex"]]
    rng.shuffle(others)
    return {**question, "options": others[:target] + [correct] + others[target:],
            "answerIndex": target}


def build_clean_pool(history: list[dict], accepted: list[dict]) -> dict[str, list[dict]]:
    """Bank questions that repeat nothing in history, the buffer, or each other."""
    used = tk.load_used()
    pool: dict[str, list[dict]] = {c: [] for c in tk.CATEGORIES}
    chosen: list[dict] = []
    for question in tk.load_bank():
        category = question.get("category")
        if category not in pool:
            continue
        if tk.qhash(question.get("prompt", "")) in used:
            continue
        key = tk.compare_key(question)
        if not key["answer"]:
            continue
        if tk.first_duplicate(key, history + accepted + chosen)[0]:
            continue
        chosen.append(key)
        pool[category].append(question)
    return pool


def audit(today_key: str):
    """Returns (findings, upcoming_days, history_keys)."""
    history = tk.recent_keys(today_key)
    upcoming = tk.load_days(start=today_key)

    findings = []
    accepted: list[dict] = []
    for day in sorted(upcoming):
        for index, question in enumerate(upcoming[day]):
            key = tk.compare_key(question)
            reason, other = tk.first_duplicate(key, history + accepted)
            if reason:
                findings.append({"date": day, "index": index, "reason": reason,
                                 "question": question, "clash": other})
            else:
                accepted.append(key)
    return findings, upcoming, history


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="swap repeats for clean questions")
    parser.add_argument("--dry-run", action="store_true", help="with --apply, do not write")
    parser.add_argument("--date", default=today(), help="treat this date as today")
    args = parser.parse_args()

    findings, upcoming, history = audit(args.date)
    total = sum(len(q) for q in upcoming.values())

    print(f"{len(upcoming)} unaired day(s), {total} question(s)")
    print(f"repeats found: {len(findings)}")
    if not findings:
        return 0

    by_reason: dict[str, int] = {}
    for f in findings:
        by_reason[f["reason"]] = by_reason.get(f["reason"], 0) + 1
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))

    if not args.apply:
        for f in findings[:60]:
            q = f["question"]
            print(f"\n  {f['date']} q{f['index']} [{q.get('category')}] {f['reason']}")
            print(f"    {q.get('prompt','')[:76]}")
        if len(findings) > 60:
            print(f"\n  ... and {len(findings) - 60} more")
        return 0

    # --- repair ---
    accepted = [tk.compare_key(q) for day in sorted(upcoming)
                for i, q in enumerate(upcoming[day])
                if not any(f["date"] == day and f["index"] == i for f in findings)]

    pool = build_clean_pool(history, accepted)
    print("clean bank available: " + ", ".join(f"{c}={len(pool[c])}" for c in tk.CATEGORIES))

    short = {}
    for f in findings:
        category = f["question"].get("category")
        short[category] = short.get(category, 0) + 1
    for category, wanted in sorted(short.items()):
        if wanted > len(pool.get(category, [])):
            print(f"not enough clean {category} questions: need {wanted}, "
                  f"have {len(pool.get(category, []))}", file=sys.stderr)
            return 1

    rng = random.Random("dedupe")
    replaced: dict[str, list[tuple[int, dict, dict]]] = {}
    used = tk.load_used()
    drawn_hashes = set()

    for f in findings:
        outgoing = f["question"]
        category = outgoing.get("category")
        incoming = pool[category].pop()
        incoming = place_answer(incoming, outgoing["answerIndex"], rng)
        published = {k: incoming[k] for k in ("category", "prompt", "options", "answerIndex")}
        replaced.setdefault(f["date"], []).append((f["index"], outgoing, published))
        h = tk.qhash(published["prompt"])
        used[h] = f["date"]
        drawn_hashes.add(h)

    written = 0
    for day, swaps in sorted(replaced.items()):
        path = tk.day_path(day)
        payload = tk.load_json(path, None)
        before = tk.validate_day(payload, day)
        for index, _outgoing, incoming in swaps:
            payload["questions"][index] = incoming
        new_errors = [e for e in tk.validate_day(payload, day) if e not in before]
        if new_errors or not tk.decodes_for_app(payload):
            print(f"REFUSING to write {path.name}:", file=sys.stderr)
            for e in new_errors or ["would no longer decode for the app"]:
                print(f"  - {e}", file=sys.stderr)
            return 1
        if not args.dry_run:
            tk.save_json(path, payload)
        written += 1
        for index, outgoing, incoming in swaps:
            print(f"  {day} q{index}: {outgoing['prompt'][:52]}")
            print(f"          -> {incoming['prompt'][:52]}")

    if not args.dry_run:
        tk.save_bank([q for q in tk.load_bank()
                      if tk.qhash(q.get("prompt", "")) not in drawn_hashes])
        tk.save_used(used)
        newest = max(tk.published_dates())
        tk.save_json(tk.ROOT / "latest.json", tk.load_json(tk.day_path(newest), {}))

    verb = "would replace" if args.dry_run else "replaced"
    print(f"\n{verb} {len(findings)} question(s) across {written} day(s)")
    if not args.dry_run:
        print("run tools/facts.py --report: replacements arrive without a fact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
