# Changelog

## 0.4.0

- Restructured into an installable package (`src/bot_detection/`), split into `core.py`
  (detection engine, zero AEP dependency), `aep_pipeline.py` (Adobe integration), and `cli.py`
  (entry point tying both together).
- `bot_detection.core` is now usable standalone — verified to import and run with `psycopg2` and
  `paramiko` absent from the environment.
- `resolve_bot_table()` and the query builders now take the bot-table name as an explicit
  parameter instead of relying on module-level mutable state.
- Added `examples/classify_csv.py`, a standalone entry point with no AEP dependency.
- Added `pyproject.toml` optional extras (`detectors`, `aep`, `behavioral`, `dev`, `all`) and a
  console script (`bot-detection-aep`).
- Added MIT license, CONTRIBUTING.md.
- Genericized all AEP/XDM specifics behind `XDM_NAMESPACE`, `DATASETS`, and `DEFAULT_BOT_TABLE`
  placeholders — no environment-specific values ship in this repository.

## 0.3.0

- Added bot categorization (`AI Data Scraper` / `AI Assistant` / `AI Search Crawler` / ...) via
  `device_detector`'s taxonomy, surfaced in both the audit output and the uploaded CSV.
- Added optional parallel classification (`--workers`, `classify_parallel()`) for large batches.
- Added `.env` auto-loading via `python-dotenv`.

## 0.2.0

- Query optimizations: single unified query with a server-side anti-join (instead of one query per
  dataset), explicit `LIMIT` on every extraction, deduplication on a case/whitespace-normalized key.
- Switched to calling the bot-only parser directly instead of the full device-parsing pipeline
  (~220x faster for the same result).
- Added the exploratory behavioral-detection notebook (session reconstruction, ECOD, XGBoost
  PU learning).

## 0.1.0

- Initial ensemble: three open-source User-Agent detection libraries plus a reinforcement regex,
  combined with majority voting.
