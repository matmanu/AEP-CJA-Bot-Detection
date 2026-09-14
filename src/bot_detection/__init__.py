"""
bot_detection — an ensemble User-Agent bot classifier, with an optional
Adobe Experience Platform integration layer.

Standalone use (no AEP, no extra dependencies beyond pandas + the detection
libraries in requirements.txt):

    from bot_detection import load_detectors, load_bot_categorizer, classify

    detectors = load_detectors()
    categorizer = load_bot_categorizer()
    df = classify(["curl/8.4.0", "Mozilla/5.0 ..."], detectors, min_votes=1, categorizer=categorizer)
    print(df)

This top-level package intentionally re-exports only `bot_detection.core`.
It never imports `bot_detection.aep_pipeline` or `bot_detection.cli` here,
so `import bot_detection` works even without `psycopg2` or `paramiko`
installed. If you need the AEP integration, import it explicitly:

    from bot_detection import aep_pipeline
    from bot_detection.cli import main
"""

from .core import (
    ALLOWLIST_PATTERN,
    BOT_PATTERN,
    Detector,
    classify,
    classify_parallel,
    load_bot_categorizer,
    load_detectors,
)

__all__ = [
    "ALLOWLIST_PATTERN",
    "BOT_PATTERN",
    "Detector",
    "classify",
    "classify_parallel",
    "load_bot_categorizer",
    "load_detectors",
]

__version__ = "0.4.0"
