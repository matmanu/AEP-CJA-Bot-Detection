# Contributing

Issues and pull requests are welcome — from small pattern fixes to larger changes.

## Setup

```bash
git clone https://github.com/matmanu/AEP-CJA-Bot-Detection.git
cd AEP-CJA-Bot-Detection
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
pytest tests/ -v
```

## The two things worth knowing before you change anything

1. **`bot_detection.core` has no AEP dependency, on purpose.** If you're fixing or extending the
   detection ensemble, you shouldn't need `psycopg2` or `paramiko` at all — if your change suddenly
   requires them, something's probably misplaced. `bot_detection.aep_pipeline` is the only module
   that's allowed to import those.
2. **`resolve_bot_table()` and friends take the table name as a parameter, not a module-level
   global.** If you're touching `aep_pipeline.py`, keep that pattern — no more hidden mutable state
   tied to call order than we already have.

## Adding or fixing a detection case

Most contributions will fall here: a user-agent that should be flagged as a bot and isn't (or vice
versa), or a new AI crawler worth categorizing correctly.

1. Add the case to `tests/test_core.py` — either to `BOTS`/`HUMANS` for a plain flag check, or to
   `AI_BOT_CATEGORIES` if it's about the category, not just the boolean.
2. Run `pytest tests/test_core.py -v` and see it fail for the right reason.
3. Fix it — usually in `BOT_PATTERN` or `ALLOWLIST_PATTERN` in `src/bot_detection/core.py`. If the
   fix instead belongs in one of the underlying libraries (`device_detector`,
   `crawler-user-agents`, `crawlerdetect`), consider filing it upstream too — this repo's
   `BOT_PATTERN` is meant to cover gaps between updates, not permanently duplicate what a library
   should really own.
4. Confirm the whole suite still passes: `pytest tests/ -v`.

## Adding a new detector to the ensemble

If you want to add a fourth (or fifth) library to the vote:

1. Add it as an optional dependency in `pyproject.toml`'s `detectors` extra.
2. Wrap it in `load_detectors()` (`src/bot_detection/core.py`) the same way the existing three are:
   `try`/`except ImportError`, cached with `@lru_cache`, appended as `(name, callable)` to the
   `detectors` list.
3. If it also carries a bot-category taxonomy, consider whether `load_bot_categorizer()` should
   prefer it or combine it with `device_detector`'s categories — that's a case-by-case call, open
   an issue to discuss before a large change here.

## Working on the AEP pipeline or the behavioral notebook

These need real AEP credentials to test end-to-end, which most contributors won't have. That's
fine — `tests/test_aep_pipeline.py` deliberately only covers pure logic (validation, SQL
composition) that doesn't need a live connection. If you're changing SQL query structure, please:

- Keep field paths built from `XDM_NAMESPACE`, never hardcode a namespace.
- Verify the query still composes without error (`build_union_query(...)` shouldn't raise) even if
  you can't run it against a real AEP instance.
- Call out in your PR description that the change is untested against live AEP, so a reviewer with
  access can verify before merging.

## Code style

No enforced formatter yet — match the existing style (mostly PEP 8, docstrings on every public
function, comments explain *why* a decision was made, not just *what* the code does). Keep
functions in `core.py` free of any AEP-specific assumption; keep `aep_pipeline.py` free of any
assumption about how classification works internally.
