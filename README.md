# Trivia Sprint content

Daily question sets for the Trivia Sprint iOS app, served from GitHub Pages.
The app fetches `https://mikesbuilds.github.io/trivia-sprint/<yyyy-MM-dd>.json`
once per day and decodes it into `TriviaSet`.

Publishing is automated. There is nothing to do by hand unless you want to add
questions in your own voice.

## How it works

Two GitHub Actions, both free (this repo is public, so Actions minutes are
unlimited) and neither needs an API key or a secret.

| Workflow | Schedule | What it does |
|---|---|---|
| `publish.yml` | daily | Tops the buffer back up to **30 days** of published day files |
| `refill.yml` | Mondays | Tops the question bank up from free trivia APIs if a category runs low |

The important property is that days are published **ahead of time**, not on the
morning they are needed. If a run fails — or Actions breaks entirely — you lose
buffer depth, not uptime. You have about a month to notice.

```
bank/seed/*.json  --merge_seed-->  bank/questions.json  --publish-->  YYYY-MM-DD.json
                                          ^
                                    refill (free APIs)
```

Questions are drawn in insertion order, so curated questions are always used
before anything scraped from an API.

## Adding your own questions

This is the one thing worth doing by hand, because it is what keeps the voice
consistent. Drop a file into `bank/seed/` shaped like this:

```json
{
  "questions": [
    {
      "category": "History",
      "prompt": "Which ancient wonder stood in the harbor of Alexandria?",
      "options": ["The Lighthouse of Pharos", "The Colossus of Rhodes",
                  "The Hanging Gardens", "The Temple of Artemis"],
      "answerIndex": 0
    }
  ]
}
```

Then:

```bash
python3 tools/merge_seed.py
```

Put the correct answer first with `"answerIndex": 0` if you like — the publisher
re-places it when it builds each day, so the long-run answer distribution stays
flat regardless of how you write them. Anything malformed, duplicated, or
already published gets dropped with a reason printed.

`category` must be one of: History, Science, Sports, Pop Culture, Geography,
Literature, Technology.

## Running things manually

```bash
python3 tools/publish.py --days 30 --dry-run   # preview what would publish
python3 tools/publish.py --days 30             # fill the buffer
python3 tools/refill.py  --min-per-category 60 # pull from the free APIs
python3 tools/merge_seed.py                    # ingest bank/seed/*.json
```

Both workflows also have a **Run workflow** button in the Actions tab.

## What the validator guards against

Every generated day is checked before it is written, and a failure aborts the
run rather than publishing something broken. This exists because
`2026-06-02.json` once shipped containing the literal text `test`, which the app
could not decode — every user that day got the error screen.

Checks: valid JSON, `date` matching the filename, exactly 7 questions, one per
category in order, exactly 4 options each, `answerIndex` an integer in 0–3, no
duplicate options within a question, no prompt repeated within a day or against
the 1,884 prompts already published.

Note there are two different bars in `tools/triviakit.py`:

- `decodes_for_app()` — the app's actual contract. Used to decide whether a date
  is already covered, so a hand-authored day is never overwritten just for
  drifting from house style.
- `validate_day()` — house style. Used as the gate on newly generated days.

About 98 older files predate the current seven-category naming (they use
`World History`, `Math`, `Movies`). The app renders whatever string is in
`category`, so they are fine and are left alone.

## Layout

```
tools/triviakit.py   shared schema, normalization, validation
tools/publish.py     builds day files from the bank
tools/refill.py      pulls from OpenTDB and The Trivia API
tools/merge_seed.py  ingests bank/seed/*.json into the bank
bank/questions.json  the pool
bank/used.json       every prompt ever published (dedupe ledger)
bank/seed/           curated question files, safe to add to
```
