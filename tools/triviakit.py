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

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "bank" / "questions.json"
USED_PATH = ROOT / "bank" / "used.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_text(value: str) -> str:
    """Undo the encodings the free trivia APIs ship and trim stray whitespace.

    OpenTDB returns HTML entities and sometimes a trailing space (its "Augustus "
    is a real example), which would render as a visibly misaligned option.
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
