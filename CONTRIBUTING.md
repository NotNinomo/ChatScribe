# Contributing

Thanks for helping! The easiest and most useful contribution is **words**.

## Add or fix WoW words
- Edit the right file in `lexicon/` (places, instances, characters, abilities, items, slang).
- Format: `Canonical Spelling = misheard one; misheard two` - see `lexicon/README.txt`.
- Prefix `!` for abbreviations / normal English words (exact match only, never sound-matched).
- Prefix an alias with `>` if it should only apply when "Chat shorthand" is on.
- Run the tests: `.venv\Scripts\python -m pytest tests -q`

## Report a misheard phrase
Open an issue with **what you said**, **what ChatScribe wrote** (the "Heard:" line and the
final line from the window) and your mic type. Those make great test cases.

## Code changes
- `scripts\setup.bat` once, then `scripts\run-debug.bat` to run with a log.
- Add a case to `tests/test_text.py` for any text-pipeline change, and run
  `tools\benchmark.py` if you touch recognition or the lexicon.
- Keep the app fully local: no telemetry, no network calls besides the one-time model download.
