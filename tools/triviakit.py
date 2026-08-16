"""Shared helpers for the Trivia Sprint content pipeline.

The published day files are the app's only contract: it fetches
<pages-url>/<yyyy-MM-dd>.json and decodes TriviaSet. Everything here exists to
make sure a file that reaches that URL is always decodable and always sane.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path

# The app renders whatever string is in `category`, but the seven below are the
# set the current content uses. Order matters: one question per category, in
# this order, is the shape every published day has.
CATEGORIES = [
    "History",
    "Science",
    "Sports",
    "Pop Culture",
    "Geography",
    "Literature",
    "Technology",
]

OPTIONS_PER_QUESTION = 4
QUESTIONS_PER_DAY = len(CATEGORIES)

# `fact` is optional. It is shown on the app's post-round review screen, never
# during the timed round. Builds older than the review screen ignore the key
# entirely — Swift's synthesized Decodable skips keys it does not know — so
# adding it to a published day is safe for already-installed versions.
# The cap keeps a review row to a few lines on the narrowest supported phone.
MAX_FACT_CHARS = 240

# The earliest day the archive will ever offer. Everything before it was
# written against a generator that answered A or B most of the time and had no
# facts at all, which is exactly the experience the archive must not lead with.
# The app reads this from index.json rather than hard-coding it, so the floor
# can move later — backwards if that content is ever rewritten — without
# shipping a new build.
ARCHIVE_START = "2026-01-01"

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "bank" / "questions.json"
USED_PATH = ROOT / "bank" / "used.json"
INDEX_PATH = ROOT / "index.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_text(value: str) -> str:
    """Normalize text before it reaches a published file.

    Unescapes HTML entities and trims stray whitespace. A trailing space on an
    option renders as a visibly misaligned row in the app, and both are easy to
    introduce by pasting from a web page.
    """
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFC", text)
    text = text.replace("​", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def norm(value: str) -> str:
    """Aggressive normalization for matching prompts across rewordings.

    Strips punctuation and accents so "Who wrote 'Beloved'?" and "Who wrote
    Beloved" are recognized as the same question.
    """
    text = unicodedata.normalize("NFKD", clean_text(value).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_option(value: str) -> str:
    """Light normalization for comparing options within a question.

    Deliberately keeps punctuation: a question whose options are "#", "@", "&"
    and "%" is perfectly valid, and norm() would flatten all four to "".
    """
    return re.sub(r"\s+", " ", clean_text(value).casefold()).strip()


def qhash(prompt: str) -> str:
    return hashlib.sha1(norm(prompt).encode()).hexdigest()[:16]


# --- Repeat detection -------------------------------------------------------
#
# qhash alone only catches a prompt published verbatim. In practice repeats
# arrive reworded ("Which metal is liquid at room temperature?" vs "Which
# metallic element...") or inverted, where one question's answer is the other's
# subject ("What is the capital of Egypt?" / "Which country has Cairo as its
# capital?"). Both read as the same question to someone playing every day.

REPEAT_WINDOW_DAYS = 365
REWORD_OVERLAP = 0.5
# Inverted pairs need topical overlap too, otherwise "What is the chemical
# symbol for oxygen?" matches "Which gas do plants release?" on the word oxygen.
INVERTED_OVERLAP = 0.25

STOPWORDS = frozenset(
    "what which who whom whose is are was were the a an of in on at to for and or by with "
    "do does did how many much called known best used commonly name named it its this that "
    "from as be been has have had you your there their they them he she his her".split()
)


def content_tokens(text: str) -> set[str]:
    return {w for w in norm(text).split() if w not in STOPWORDS and len(w) > 2}


def answer_text(question: dict) -> str:
    options = question.get("options") or []
    index = question.get("answerIndex")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(options):
        return str(options[index])
    return ""


def compare_key(question: dict) -> dict:
    """Precomputed shape used for repeat comparisons."""
    prompt = str(question.get("prompt", ""))
    return {
        "hash": qhash(prompt),
        "prompt": norm(prompt),
        "tokens": content_tokens(prompt),
        "answer": norm(answer_text(question)),
        "category": question.get("category", ""),
    }


def _same_answer(a: str, b: str) -> bool:
    # "Khrushchev" and "Nikita Khrushchev" are the same answer.
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def duplicate_reason(a: dict, b: dict) -> str | None:
    """Why a and b test the same knowledge, or None. Both are compare_key()s."""
    if a["hash"] == b["hash"]:
        return "exact"

    union = a["tokens"] | b["tokens"]
    overlap = len(a["tokens"] & b["tokens"]) / len(union) if union else 0.0

    if overlap >= REWORD_OVERLAP and _same_answer(a["answer"], b["answer"]):
        return "reworded"
    if (a["answer"] and b["answer"]
            and a["answer"] in b["prompt"] and b["answer"] in a["prompt"]
            and overlap >= INVERTED_OVERLAP):
        return "inverted"
    return None


def first_duplicate(key: dict, others):
    """Return (reason, other) for the first match in others, or (None, None)."""
    for other in others:
        reason = duplicate_reason(key, other)
        if reason:
            return reason, other
    return None, None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_bank() -> list[dict]:
    return load_json(BANK_PATH, {"questions": []}).get("questions", [])


def save_bank(questions: list[dict]) -> None:
    save_json(BANK_PATH, {"questions": questions})


def load_used() -> dict[str, str]:
    return load_json(USED_PATH, {"published": {}}).get("published", {})


def save_used(published: dict[str, str]) -> None:
    save_json(USED_PATH, {"published": dict(sorted(published.items()))})


def normalize_question(raw: dict) -> dict | None:
    """Coerce a question into the app's schema, or return None if unusable."""
    try:
        category = clean_text(raw["category"])
        prompt = clean_text(raw["prompt"])
        options = [clean_text(o) for o in raw["options"]]
        answer_index = int(raw["answerIndex"])
    except (KeyError, TypeError, ValueError):
        return None

    if category not in CATEGORIES:
        return None
    if not prompt or len(options) != OPTIONS_PER_QUESTION:
        return None
    if not all(options) or not 0 <= answer_index < OPTIONS_PER_QUESTION:
        return None
    # Two options that read identically make the question unanswerable.
    if len({norm_option(o) for o in options}) != OPTIONS_PER_QUESTION:
        return None

    question = {
        "category": category,
        "prompt": prompt,
        "options": options,
        "answerIndex": answer_index,
    }
    # Dropped rather than rejected: a missing or overlong fact is a cosmetic
    # gap on one review row, not a reason to lose an otherwise good question.
    fact = raw.get("fact")
    if fact is not None:
        fact = clean_text(fact)
        if fact and len(fact) <= MAX_FACT_CHARS:
            question["fact"] = fact

    if raw.get("source"):
        question["source"] = raw["source"]
    return question


def validate_day(payload: dict, expected_date: str | None = None) -> list[str]:
    """Return a list of problems. Empty list means safe to publish.

    This is the gate that stops a `test` file — or anything else the app can't
    decode — from reaching the Pages URL.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return ["payload is not an object"]

    date = payload.get("date")
    if not isinstance(date, str) or not DATE_RE.match(date):
        errors.append(f"bad or missing date: {date!r}")
    elif expected_date and date != expected_date:
        errors.append(f"date {date!r} does not match filename {expected_date!r}")

    if not isinstance(payload.get("categories"), list) or not payload["categories"]:
        errors.append("categories must be a non-empty array")

    questions = payload.get("questions")
    if not isinstance(questions, list):
        return errors + ["questions must be an array"]
    if len(questions) != QUESTIONS_PER_DAY:
        errors.append(f"expected {QUESTIONS_PER_DAY} questions, got {len(questions)}")

    seen_prompts: set[str] = set()
    for i, q in enumerate(questions):
        where = f"q{i}"
        if not isinstance(q, dict):
            errors.append(f"{where}: not an object")
            continue

        for field in ("category", "prompt", "options", "answerIndex"):
            if field not in q:
                errors.append(f"{where}: missing {field}")
        if any(f not in q for f in ("category", "prompt", "options", "answerIndex")):
            continue

        # A non-int answerIndex decodes as a Swift error and kills the whole day.
        if not isinstance(q["answerIndex"], int) or isinstance(q["answerIndex"], bool):
            errors.append(f"{where}: answerIndex must be an int")
        elif not 0 <= q["answerIndex"] < OPTIONS_PER_QUESTION:
            errors.append(f"{where}: answerIndex {q['answerIndex']} out of range")

        if not isinstance(q["options"], list) or len(q["options"]) != OPTIONS_PER_QUESTION:
            errors.append(f"{where}: expected {OPTIONS_PER_QUESTION} options")
        else:
            if not all(isinstance(o, str) and o.strip() for o in q["options"]):
                errors.append(f"{where}: options must be non-empty strings")
            elif len({norm_option(o) for o in q["options"]}) != OPTIONS_PER_QUESTION:
                errors.append(f"{where}: duplicate options")

        if not isinstance(q["prompt"], str) or not q["prompt"].strip():
            errors.append(f"{where}: empty prompt")
        else:
            key = norm(q["prompt"])
            if key in seen_prompts:
                errors.append(f"{where}: duplicate prompt within the day")
            seen_prompts.add(key)

        # Optional, but if it is present it has to be renderable.
        if "fact" in q:
            fact = q["fact"]
            if not isinstance(fact, str) or not fact.strip():
                errors.append(f"{where}: fact must be a non-empty string when present")
            elif len(fact) > MAX_FACT_CHARS:
                errors.append(f"{where}: fact is {len(fact)} chars (max {MAX_FACT_CHARS})")

    got = [q.get("category") for q in questions if isinstance(q, dict)]
    if len(questions) == QUESTIONS_PER_DAY and got != CATEGORIES:
        errors.append(f"categories must be exactly {CATEGORIES} in order, got {got}")

    return errors


def decodes_for_app(payload) -> bool:
    """Whether Swift's TriviaSet can decode this payload.

    Deliberately looser than validate_day. That function encodes our house
    style (seven questions, our category names, our ordering); this one encodes
    the app's actual contract. Coverage must be judged by the latter, otherwise
    a hand-authored day that merely drifts from house style looks "missing" and
    gets silently overwritten.
    """
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("date"), str):
        return False
    cats = payload.get("categories")
    if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
        return False
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return False
    for q in questions:
        if not isinstance(q, dict):
            return False
        if not isinstance(q.get("category"), str) or not isinstance(q.get("prompt"), str):
            return False
        options = q.get("options")
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            return False
        # Swift decodes Int strictly; a bool or float here kills the whole day.
        if not isinstance(q.get("answerIndex"), int) or isinstance(q["answerIndex"], bool):
            return False
    return True


def day_path(date: str) -> Path:
    return ROOT / f"{date}.json"


def published_dates() -> set[str]:
    """Dates already on disk with a file the app can decode."""
    return {
        path.stem
        for path in ROOT.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")
        if decodes_for_app(load_json(path, None))
    }


def day_paths() -> list[Path]:
    return sorted(ROOT.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"))


def write_manifest() -> dict:
    """Publish index.json: which days exist, and how far back the archive goes.

    The archive cannot discover days by guessing URLs. Coverage is deliberately
    not contiguous — days fail to generate, and whole stretches get cleared
    before a rewrite — so probing would mean firing hundreds of requests at
    dates that are simply absent.

    `dates` lists everything on or after the floor, including days still ahead
    of today. Deciding what counts as past belongs to the client, which is the
    only side that knows the device's timezone.
    """
    dates = sorted(d for d in published_dates() if d >= ARCHIVE_START)
    manifest = {
        "archive_start": ARCHIVE_START,
        "count": len(dates),
        "dates": dates,
    }
    save_json(INDEX_PATH, manifest)
    return manifest


def load_days(start: str | None = None, end: str | None = None) -> dict[str, list[dict]]:
    """Questions per date, for dates within [start, end]."""
    days: dict[str, list[dict]] = {}
    for path in day_paths():
        if start and path.stem < start:
            continue
        if end and path.stem > end:
            continue
        payload = load_json(path, None)
        if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
            days[path.stem] = [q for q in payload["questions"] if isinstance(q, dict)]
    return days


def recent_keys(today: str, window_days: int = REPEAT_WINDOW_DAYS) -> list[dict]:
    """compare_key()s for everything aired in the window before `today`."""
    from datetime import date as _date, timedelta as _timedelta

    y, m, d = (int(part) for part in today.split("-"))
    cutoff = (_date(y, m, d) - _timedelta(days=window_days)).isoformat()
    keys = []
    for day, questions in load_days(start=cutoff, end=today).items():
        if day >= today:
            continue
        for question in questions:
            keys.append(compare_key(question))
    return keys
