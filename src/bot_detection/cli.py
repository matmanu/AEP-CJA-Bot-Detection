#!/usr/bin/env python3
"""
Bot detection on Adobe Experience Platform (AEP) — command-line entry point.

Extracts the user-agents seen in the last N days across your clickstream
datasets, keeps only the ones NOT already present in your bot table
(anti-join done server-side in AEP), classifies them with an ensemble of
open-source libraries (see `bot_detection.core`), and uploads the delta
over SFTP atomically.

*** Before running this against your own AEP instance, read the
"Adapting this to your environment" section in README.md, and edit the
configuration block at the top of `bot_detection/aep_pipeline.py`
(XDM_NAMESPACE, DATASETS, DEFAULT_BOT_TABLE) to match your own schema. ***

Usage:
    python -m bot_detection.cli                       # normal run
    python -m bot_detection.cli --days 30              # different lookback window
    python -m bot_detection.cli --dry-run              # classify but don't upload
    python -m bot_detection.cli --audit-out audit.csv  # dump with each detector's vote
    python -m bot_detection.cli --workers 4             # parallel classification
                                                          # (only worth it with tens of
                                                          # thousands of unique UAs or more)

Configuration: everything via environment variables (see .env.example),
except the AEP schema specifics, which live in aep_pipeline.py (see above).

If you don't need AEP/SFTP at all — e.g. you just want to classify a list
of user-agents you already have — you don't need this module. Use
`bot_detection.core` directly instead; it has no AEP dependency.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Sequence

import psycopg2

from .aep_pipeline import (
    DATASETS,
    DEFAULT_BOT_TABLE,
    MAX_EXPECTED_ROWS,
    UPLOAD_COLUMNS,
    AepConfig,
    SftpConfig,
    fetch_new_user_agents,
    notify,
    resolve_bot_table,
    sftp_client,
    upload_atomic,
)
from .core import classify, classify_parallel, load_bot_categorizer, load_detectors

LOG = logging.getLogger("bot_detection.cli")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=20, help="lookback window, in days")
    parser.add_argument(
        "--min-votes",
        type=int,
        default=1,
        help="how many detectors must agree to flag a UA as a bot",
    )
    parser.add_argument("--dry-run", action="store_true", help="classify but don't upload to SFTP")
    parser.add_argument(
        "--audit-out",
        default=None,
        help="local path for the full CSV with each detector's individual vote",
    )
    parser.add_argument(
        "--datasets",
        default=None,
        help="comma-separated subset of datasets (default: all of DATASETS)",
    )
    parser.add_argument(
        "--bot-table",
        default=None,
        help="override the bot table name instead of resolving DEFAULT_BOT_TABLE",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "parallel processes for classification (default: 1, serial). "
            "Only worth it with tens of thousands of unique UAs or more, and "
            "multiple cores available: below that, the process startup cost "
            "(~0.44s each) outweighs the gain."
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        stream=sys.stdout,
    )

    try:
        from dotenv import find_dotenv, load_dotenv
        import os

        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path)
            LOG.info("Loaded variables from %s", env_path)
        else:
            LOG.warning(
                "No .env file found walking up from %s: relying on environment "
                "variables already present in the process.",
                os.getcwd(),
            )
    except ImportError:
        LOG.warning(
            "python-dotenv not installed: .env won't be loaded automatically, "
            "environment variables need to already be exported."
        )

    started = datetime.now(timezone.utc)

    datasets = (
        tuple(d.strip() for d in args.datasets.split(",") if d.strip())
        if args.datasets
        else DATASETS
    )

    try:
        aep = AepConfig.from_env()
        sftp_cfg = SftpConfig.from_env()
    except RuntimeError as exc:
        LOG.error(str(exc))
        return 2

    detectors = load_detectors()
    categorizer = load_bot_categorizer()

    with psycopg2.connect(
        host=aep.host,
        port=aep.port,
        dbname=aep.dbname,
        user=aep.user,
        password=aep.password,
        sslmode="require",
    ) as conn:
        bot_table = args.bot_table or resolve_bot_table(conn, expected=DEFAULT_BOT_TABLE)
        user_agents = fetch_new_user_agents(conn, datasets, args.days, bot_table)

    LOG.info("New user-agents to classify: %s", f"{len(user_agents):,}")
    if not user_agents:
        notify("No new user-agents in the last %s days." % args.days)
        return 0

    if args.workers > 1:
        LOG.info("Classifying in parallel across %s processes...", args.workers)
        classified = classify_parallel(user_agents, args.min_votes, n_workers=args.workers)
    else:
        classified = classify(user_agents, detectors, args.min_votes, categorizer)

    if args.audit_out:
        classified.to_csv(args.audit_out, index=False, encoding="utf-8")
        LOG.info("Audit written to %s", args.audit_out)

    df_final = classified.loc[classified["is_bot"], ["User_agent", "category"]].copy()
    df_final["BOT_DETECTION"] = 1
    df_final = df_final[UPLOAD_COLUMNS].sort_values("User_agent")

    LOG.info(
        "Bots detected: %s out of %s new user-agents (%.1f%%)",
        f"{len(df_final):,}",
        f"{len(classified):,}",
        100 * len(df_final) / max(len(classified), 1),
    )

    # On-screen breakdown by category, on top of what's already in the
    # uploaded CSV: useful to see at a glance, without opening anything, how
    # many "good" AI crawlers (AI Data Scraper / AI Assistant / AI Search
    # Crawler) showed up in this run.
    categorized = classified.loc[classified["is_bot"] & (classified["category"] != "")]
    if not categorized.empty:
        counts = categorized["category"].value_counts()
        LOG.info(
            "Categories among detected bots: %s",
            ", ".join(f"{cat}={n}" for cat, n in counts.items()),
        )

    if df_final.empty:
        notify("No new bots to upload: skipping upload.")
        return 0

    if len(df_final) > MAX_EXPECTED_ROWS:
        notify(
            f"WARNING: {len(df_final):,} rows, above the expected threshold "
            f"({MAX_EXPECTED_ROWS:,}). Review before this flows downstream.",
            ok=False,
        )

    if args.dry_run:
        LOG.info("--dry-run enabled: upload skipped.\n%s", df_final.head(20).to_string())
        return 0

    payload = df_final.to_csv(index=False, encoding="utf-8").encode("utf-8")
    with sftp_client(sftp_cfg) as sftp:
        upload_atomic(sftp, sftp_cfg.remote_path, payload)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    notify(
        f"Bot detection complete: {len(df_final):,} new user-agents uploaded "
        f"to {sftp_cfg.remote_path} in {elapsed:.0f}s."
    )
    return 0


def _entry_point() -> None:
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        LOG.exception("Run failed")
        notify(f"Bot detection FAILED: {exc}", ok=False)
        sys.exit(1)


if __name__ == "__main__":
    _entry_point()
