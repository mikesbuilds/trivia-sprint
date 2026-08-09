#!/usr/bin/env python3
"""Attach review-screen facts to questions, wherever they currently live.

A question exists in one of two places and never both: in `bank/questions.json`
if it has not aired yet, or inside a published `YYYY-MM-DD.json` if it has.
Writing a fact by hand would therefore mean hunting through 300+ day files, so
facts are authored once in `bank/facts.json` and applied from there.

    python3 tools/facts.py --report
    python3 tools/facts.py --apply --dry-run
    python3 tools/facts.py --apply

Matching is by prompt hash, so a fact keeps matching through light rewording of
the prompt. Facts are optional by design: a question without one simply shows
its answer on the review screen, and app builds older than that screen ignore
the key entirely.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import triviakit as tk

FACTS_PATH = tk.ROOT / "bank" / "facts.json"


def load_facts() -> dict[str, str]:
    """Authored facts, keyed by prompt hash."""
    raw = tk.load_json(FACTS_PATH, {"facts": []}).get("facts", [])
    facts: dict[str, str] = {}
    for entry in raw:
        prompt = entry.get("prompt")
        fact = entry.get("fact")
        if not isinstance(prompt, str) or not isinstance(fact, str):
            continue
        fact = tk.clean_text(fact)
        if not fact or len(fact) > tk.MAX_FACT_CHARS:
            print(f"skipping unusable fact for {prompt[:60]!r}", file=sys.stderr)
            continue
        facts[tk.qhash(prompt)] = fact
    return facts


def day_files(include_past: bool = False) -> list:
    """Day files worth touching.

    Past days default out. The app only ever fetches today, so a fact added to
    an aired day is invisible — and some 2025 files predate the current house
    style, so rewriting them would churn hundreds of files for no benefit. A
    question that will air again comes back through the bank, which does get
    facts.
    """
    paths = sorted(tk.ROOT.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"))
    if include_past:
        return paths
    today = date.today().isoformat()
    return [p for p in paths if p.stem >= today]


def report(facts: dict[str, str]) -> int:
    bank = tk.load_bank()
    bank_with = sum(1 for q in bank if q.get("fact"))
    print(f"authored facts      : {len(facts)}")
    print(f"bank questions      : {len(bank)} ({bank_with} with a fact)")

    today = date.today().isoformat()
    upcoming_total = upcoming_with = 0
    days_complete = days_partial = days_empty = 0

    for path in day_files():
        payload = tk.load_json(path, None)
        if not isinstance(payload, dict):
            continue
        questions = payload.get("questions") or []
        have = sum(1 for q in questions if isinstance(q, dict) and q.get("fact"))
        upcoming_total += len(questions)
        upcoming_with += have
        if not have:
            days_empty += 1
        elif have == len(questions):
            days_complete += 1
        else:
            days_partial += 1

    pct = (100 * upcoming_with / upcoming_total) if upcoming_total else 0
    print(f"upcoming questions  : {upcoming_total} ({upcoming_with} with a fact, {pct:.0f}%)")
    print(f"upcoming days       : {days_complete} full, {days_partial} partial, {days_empty} none")
    return 0


def apply(facts: dict[str, str], dry_run: bool, include_past: bool = False) -> int:
    if not facts:
        print("no facts authored yet - add some to bank/facts.json", file=sys.stderr)
        return 1

    matched: set[str] = set()
    changed_days: list[str] = []

    # 1. Questions still in the bank.
    bank = tk.load_bank()
    bank_changes = 0
    for question in bank:
        h = tk.qhash(question["prompt"])
        fact = facts.get(h)
        if not fact:
            continue
        matched.add(h)
        if question.get("fact") != fact:
            question["fact"] = fact
            bank_changes += 1

    # 2. Questions already published into a day file.
    for path in day_files(include_past):
        payload = tk.load_json(path, None)
        if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
            continue

        # Some legacy files already fail house-style validation for reasons that
        # have nothing to do with facts. Judge this run only on what it changes.
        errors_before = tk.validate_day(payload, path.stem)

        touched = 0
        for question in payload["questions"]:
            if not isinstance(question, dict) or not isinstance(question.get("prompt"), str):
                continue
            h = tk.qhash(question["prompt"])
            fact = facts.get(h)
            if not fact:
                continue
            matched.add(h)
            if question.get("fact") != fact:
                question["fact"] = fact
                touched += 1
        if not touched:
            continue

        # Never write a day the app might then fail to decode, and never let
        # this run be the thing that introduces a new validation error.
        new_errors = [e for e in tk.validate_day(payload, path.stem)
                      if e not in errors_before]
        if new_errors or not tk.decodes_for_app(payload):
            print(f"REFUSING to write {path.name}:", file=sys.stderr)
            for e in new_errors or ["payload would no longer decode for the app"]:
                print(f"  - {e}", file=sys.stderr)
            return 1

        if not dry_run:
            tk.save_json(path, payload)
        changed_days.append(path.stem)

    if not dry_run:
        if bank_changes:
            tk.save_bank(bank)
        if changed_days:
            newest = max(tk.published_dates())
            tk.save_json(tk.ROOT / "latest.json", tk.load_json(tk.day_path(newest), {}))

    verb = "would update" if dry_run else "updated"
    print(f"{verb} {bank_changes} bank question(s)")
    print(f"{verb} {len(changed_days)} day file(s)"
          + (f": {changed_days[0]} .. {changed_days[-1]}" if changed_days else ""))

    unmatched = set(facts) - matched
    if unmatched:
        print(f"{len(unmatched)} authored fact(s) matched no question "
              "(prompt reworded or retired)", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="show fact coverage")
    parser.add_argument("--apply", action="store_true",
                        help="write authored facts into the bank and day files")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply, report without writing")
    parser.add_argument("--include-past", action="store_true",
                        help="also rewrite day files that have already aired")
    args = parser.parse_args()

    facts = load_facts()
    if args.apply:
        return apply(facts, args.dry_run, args.include_past)
    return report(facts)


if __name__ == "__main__":
    sys.exit(main())
