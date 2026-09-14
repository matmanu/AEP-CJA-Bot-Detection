# bot-detection-aep

[![Tests](https://github.com/<your-org>/bot-detection-aep/actions/workflows/tests.yml/badge.svg)](https://github.com/<your-org>/bot-detection-aep/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

*(Replace `<your-org>` above with your actual GitHub org/username once this is pushed — the badge
won't render correctly until then.)*

An ensemble User-Agent bot classifier — usable as a **standalone Python library** with zero
Adobe-specific dependencies, plus an **optional integration layer** for Adobe Experience Platform
(AEP) and Customer Journey Analytics (CJA), for teams who want to extract clickstream user-agents,
classify them, and feed the result back as an exclusion key.

```python
from bot_detection import load_detectors, load_bot_categorizer, classify

detectors = load_detectors()
categorizer = load_bot_categorizer()
df = classify(["curl/8.4.0", "Mozilla/5.0 (compatible; ClaudeBot/1.0; ...)"], detectors, min_votes=1, categorizer=categorizer)
print(df)
```

That's the whole standalone use case — no AEP account, no SFTP, no environment variables. Four
open-source libraries vote independently on each user-agent (majority vote, not a single point of
failure), and one of them also categorizes bots (`AI Data Scraper`, `AI Assistant`,
`AI Search Crawler`, `Search bot`, ...) beyond a plain yes/no.

## Two ways to use this

| | What you get | What you need |
|---|---|---|
| **`bot_detection.core`** | The detection engine alone: `classify()`, `classify_parallel()`, the detector ensemble. | `pip install bot-detection-aep[detectors]` |
| **`bot_detection.aep_pipeline` + `bot_detection.cli`** | The full AEP pipeline: query extraction, upload over SFTP, Teams notifications. | `pip install bot-detection-aep[aep]`, plus your own AEP/SFTP credentials |

The two layers are fully decoupled: `bot_detection.core` has no idea AEP exists, and
`bot_detection.aep_pipeline` has no idea how classification works — it just calls whatever function
you hand it. Swap either one out independently if your needs differ.

## How the detection engine works

Four independent detectors vote on every user-agent:

- **`device_detector`** — port of Matomo's device-detector, adds a bot category on top of the flag
- **`crawler-user-agents`** — pattern list maintained independently by the community
- **`crawlerdetect`** — port of the CrawlerDetect PHP library
- **A reinforcement regex** — catches HTTP clients, headless browsers, and consent-management
  scanners (OneTrust, Cookiebot, ...) the libraries sometimes miss

A majority vote (`min_votes`, default 1) decides the final flag, so a gap in one library's pattern
list doesn't silently become a false negative. Known false positives (e.g. CUBOT, a smartphone
brand whose name happens to contain "bot") are allow-listed explicitly.

## The AEP pipeline, if you need it

```
AEP (clickstream dataset)
        │  Query Service extracts hits from the last N days
        ▼
   bot_detection.core
        │  ensemble classification
        ▼
AEP (your bot table)
        │  detected identifiers get written back
        ▼
   CJA (Data View)
        │  the table is used as a key to exclude that traffic from reporting
```

### ⚠️ Before you run this against your own AEP instance

**This part is a template, not a plug-and-play tool.** Every AEP customer has their own XDM schema,
field names, and dataset structure. Before running anything here, you need to:

1. Edit the configuration block at the top of `src/bot_detection/aep_pipeline.py`:
   - `XDM_NAMESPACE` — your tenant's custom field-group namespace (e.g. `_acmecorp`). Find it in
     AEP UI → Schemas → your schema → any custom field group.
   - `DATASETS` — the names of the datasets you want to query.
   - `DEFAULT_BOT_TABLE` — the name of your own bot-tracking table/dataset in AEP.
2. Adjust the field paths inside the SQL query builders (`build_union_query`,
   `build_single_dataset_query`, `fetch_known_bots`) to match your actual XDM schema. Verify them
   with `SHOW COLUMNS IN <dataset>` in the AEP Query Service UI before trusting any output.
3. Do the same for `notebooks/behavioral_detection.ipynb`, which additionally references
   `{namespace}.id`, `{namespace}.pagedetails.full_url`, `{namespace}.pagedetails.dom_referrer`, and
   `{namespace}.events.events_cartadd` — all placeholders for fields you'll need to map to your own
   schema.

None of this is exotic — it's the normal amount of adaptation any AEP Query Service SQL needs when
moving between tenants — but skipping it will produce SQL errors or, worse, silently wrong results.

## Repository structure

```
.
├── src/bot_detection/
│   ├── __init__.py         # re-exports core.py's public API only — no AEP import here
│   ├── core.py              # detection engine — pandas + detection libs, nothing else
│   ├── aep_pipeline.py       # AEP Query Service + SFTP integration (needs the 'aep' extra)
│   └── cli.py                 # command-line entry point tying core + aep_pipeline together
├── examples/
│   └── classify_csv.py      # standalone example — no AEP, works out of the box
├── tests/
│   ├── test_core.py         # regression tests for the detector ensemble
│   └── test_aep_pipeline.py  # lightweight tests (validation, SQL composition) — no live connection
├── notebooks/
│   ├── bot_detection_exploration.ipynb    # same flow as the CLI, cell by cell
│   └── behavioral_detection.ipynb          # session-level behavioral detection (exploratory)
├── docs/
│   └── crawlerdetect_patterns.csv         # export of the patterns used by crawlerdetect
├── pyproject.toml
├── requirements.txt
├── .env.example
├── LICENSE
└── CONTRIBUTING.md
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# Standalone detection engine only:
pip install -e ".[detectors]"

# ...or everything, including the AEP pipeline and the notebooks:
pip install -e ".[all]"

cp .env.example .env               # only needed for the AEP pipeline — fill in AEP_USER / AEP_PASSWORD / SFTP_*
```

AEP credentials are a **short-lived token**, not a fixed password — regenerate them from the Adobe
Experience Platform UI (Queries → Credentials) when they expire.

## Try it in 30 seconds

No AEP, no config — this runs on a small built-in sample of user-agents:

```bash
pip install -e ".[detectors]"
python examples/classify_csv.py
```

Or against your own data:

```bash
python examples/classify_csv.py --input my_user_agents.csv --column user_agent --output result.csv
```

See `examples/classify_csv.py` for the full source — it's about 15 lines of actual logic on top of
argument parsing, a good starting point for wiring this into your own pipeline.

## Usage — AEP pipeline

```bash
# See what it would do, without uploading anything
python -m bot_detection.cli --dry-run --audit-out audit.csv --verbose

# Real run
python -m bot_detection.cli

# Also available as a console script after `pip install -e .`:
bot-detection-aep --dry-run
```

Main options:

| Flag | Description | Default |
|---|---|---|
| `--days` | Lookback window, in days | `20` |
| `--min-votes` | How many detectors must agree to flag a UA as a bot | `1` |
| `--datasets` | Comma-separated subset of datasets | all of `DATASETS` |
| `--bot-table` | Override the resolved bot table name | resolved from `DEFAULT_BOT_TABLE` |
| `--workers` | Parallel processes for classification (see below) | `1` (serial) |
| `--dry-run` | Classify but don't upload to SFTP | — |
| `--audit-out` | Save a CSV with every individual detector's vote | — |

First run from a new environment: register the SFTP host key with
`ssh-keyscan -p 22 <your-sftp-host> >> ~/.ssh/known_hosts`, or run the dedicated cell in the
notebook, which does the same thing without leaving Python.

### Parallel classification (`--workers`)

Classification is CPU-bound (regex, pattern matching) — threads won't help (the GIL serializes them
anyway), but processes will. `--workers N` splits the work across `N` processes. It's only worth
turning on above roughly 20-30k unique user-agents, and only if the machine actually has multiple
CPU cores: each worker has a measured fixed startup cost of ~0.44s (compiling crawlerdetect's regex
patterns and loading device_detector's YAML files), which needs to be amortized over enough work.

## Tests

```bash
pip install -e ".[detectors,aep,dev]"
pytest tests/ -v
```

`test_core.py` covers known regression cases (declared bots, HTTP clients, headless browsers, major
AI crawlers, the historical CUBOT false positive) and needs nothing beyond the `detectors` extra.
`test_aep_pipeline.py` needs the `aep` extra (for `psycopg2`) but doesn't require a live AEP
connection — it only tests pure logic (dataset-name validation, SQL composition). Re-run the suite
after every `pip install --upgrade` of the detection libraries, since their pattern lists change
over time.

## Notebooks

- **`bot_detection_exploration.ipynb`** — same flow as the CLI, cell by cell, to inspect results
  before uploading them (vote distribution across detectors, disputed cases, a sample of the
  non-bots).
- **`behavioral_detection.ipynb`** — session-level behavioral detection: reconstructs sessions (AEP
  has no native `session_id`), engineers features (hit rate, path entropy, interaction ratio, ...),
  flags anomalies with ECOD (unsupervised), and classifies with XGBoost in PU learning, using the
  UA-confirmed bots from the core engine as positive examples. Produces a review CSV, **no
  automatic upload** — this part is exploratory and probability-based, unlike the near-certainty of
  UA-based detection.

Both notebooks import directly from `bot_detection`/`bot_detection.aep_pipeline`, so editing the
configuration in `src/bot_detection/aep_pipeline.py` is enough — you don't need to duplicate
anything inside the notebooks.

## Status and roadmap

- [x] Standalone detection engine, decoupled from AEP
- [x] AEP pipeline: query optimization, deduplication, AI-crawler categorization
- [x] Behavioral pipeline validated on synthetic data
- [ ] Behavioral pipeline validated against real AEP traffic
- [ ] Threshold tuning (ECOD contamination, XGBoost probability cutoff)

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup
instructions and the conventions this repo follows (especially around keeping `bot_detection.core`
free of any AEP dependency).

## License

MIT — see [LICENSE](LICENSE). The AEP-specific configuration values in
`src/bot_detection/aep_pipeline.py` (`XDM_NAMESPACE`, `DATASETS`, `DEFAULT_BOT_TABLE`) are
placeholders for you to fill in; nothing in this repository identifies any specific organization's
real environment.

## Notes

- Credentials should never be committed: `.env` is excluded via `.gitignore`, only `.env.example`
  (with no real values) is tracked.
- The name of your bot table in AEP might change over time in some environments (e.g. recreated
  periodically under a new name). If that happens, `resolve_bot_table()` tries to find it
  automatically by searching for tables matching the same prefix.
