"""
Adobe Experience Platform integration: extracting user-agents via Query
Service and delivering results over SFTP.

This module has no knowledge of *how* user-agents get classified — that's
`bot_detection.core`'s job. It only knows how to get candidate user-agents
out of AEP and how to ship a result back. You can swap `bot_detection.core`
for your own classification logic and reuse everything here unchanged, or
vice versa: use `bot_detection.core` with a completely different data
source and skip this module entirely.

*** Every AEP tenant has its own schema. Read the "Adapting this to your
environment" section in README.md before using this against a real AEP
instance: at minimum you need to set XDM_NAMESPACE, DATASETS, and BOT_TABLE
below to match your own environment. ***
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Sequence

import paramiko
import psycopg2
from psycopg2 import sql

LOG = logging.getLogger("bot_detection.aep_pipeline")

# --------------------------------------------------------------------------- #
# Configuration — EDIT THIS SECTION FOR YOUR OWN AEP TENANT
# --------------------------------------------------------------------------- #

# Every AEP customer has their own custom field-group namespace: a tenant ID
# that prefixes every custom (non-standard) field path in your XDM schema,
# e.g. "_acmecorp" for a tenant called "acmecorp". Find yours in AEP UI ->
# Schemas -> your schema -> any custom field group, or ask whoever set up
# your XDM schema. Every field path below (e.g. "{ns}.Device.useragent")
# is built from this constant.
XDM_NAMESPACE = "_yourtenantid"

# Names of the datasets to query, WITHOUT any shared suffix (that goes in
# DATASET_SUFFIX below). Replace with your own dataset identifiers — these
# are typically one per site, brand, or data source you collect clickstream
# data for. Validated later against [a-z0-9_] only.
DATASETS: tuple[str, ...] = (
    "example_dataset_1",
    "example_dataset_2",
)

# Suffix shared by all dataset names above, if any (e.g. your datasets might
# be named "site1_clickstream_data", "site2_clickstream_data", ...). Leave
# empty ("") if your dataset names in DATASETS are already complete.
DATASET_SUFFIX = ""

# Default name of your bot-tracking table/dataset in AEP (the one that holds
# already-confirmed bot user-agents, used both to skip already-known bots
# and as the destination the uploaded CSV eventually feeds). If this table
# gets recreated periodically under a different name in your environment,
# see resolve_bot_table() below, which tries to find it automatically by
# prefix if the exact name stops matching.
DEFAULT_BOT_TABLE = "bot_detection"

# AEP Query Service silently caps results at 50,000 rows if you don't specify
# an explicit LIMIT. Set high on purpose so a legitimate large result never
# gets truncated without you noticing.
EXTRACTION_LIMIT = 100_000_000

# Columns actually uploaded over SFTP: the CSV schema must never drift from
# changes to your XDM schema on the AEP side without you noticing.
# "category" (AI Data Scraper / AI Assistant / AI Search Crawler / Search bot
# / ...) requires that field to already exist in your bot table's XDM schema.
# If your schema uses a different field name than "category", rename the
# string here (the CSV header must match your XDM mapping exactly).
UPLOAD_COLUMNS = ["User_agent", "BOT_DETECTION", "category"]

# Sanity-check threshold: above this the caller should warn before letting
# the result flow downstream, instead of silently uploading a suspicious file.
MAX_EXPECTED_ROWS = 50_000


@dataclass(frozen=True)
class AepConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "AepConfig":
        return cls(
            host=_require_env("AEP_HOST"),
            port=int(os.getenv("AEP_PORT", "80")),
            dbname=os.getenv("AEP_DBNAME", "prod:all"),
            user=_require_env("AEP_USER"),
            password=_require_env("AEP_PASSWORD"),
        )


@dataclass(frozen=True)
class SftpConfig:
    host: str
    port: int
    user: str
    password: str | None
    key_path: str | None
    remote_path: str
    strict_host_key: bool

    @classmethod
    def from_env(cls) -> "SftpConfig":
        return cls(
            host=_require_env("SFTP_HOST"),
            port=int(os.getenv("SFTP_PORT", "22")),
            user=_require_env("SFTP_USER"),
            password=os.getenv("SFTP_PASSWORD") or None,
            key_path=os.getenv("SFTP_KEY_PATH") or None,
            remote_path=os.getenv("SFTP_REMOTE_PATH", "bot_detection/bot.csv"),
            strict_host_key=os.getenv("SFTP_STRICT_HOST_KEY", "1") != "0",
        )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. "
            "Set it in your .env file or secret store before running the script."
        )
    return value


# --------------------------------------------------------------------------- #
# AEP Query Service
# --------------------------------------------------------------------------- #

def _validate_dataset_name(name: str) -> str:
    cleaned = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", cleaned):
        raise ValueError(f"Invalid dataset name: {name!r}")
    return cleaned


def resolve_bot_table(conn, expected: str = DEFAULT_BOT_TABLE, prefix: str | None = None) -> str:
    """Checks that `expected` actually exists as a table. If it doesn't
    (e.g. it gets recreated under a new name periodically in your
    environment), looks for the single table starting with `prefix` and
    returns that instead, logging a warning. Only raises if no match is
    found or if there's more than one (ambiguous). Returns the resolved
    table name — pass it explicitly to the other functions in this module,
    there's no hidden global state here."""
    prefix = prefix or expected
    with conn.cursor() as cur:
        try:
            cur.execute(sql.SQL("SELECT 1 FROM {t} LIMIT 1").format(t=sql.Identifier(expected)))
            return expected
        except psycopg2.Error:
            conn.rollback()

    LOG.warning("Table '%s' not found. Trying to resolve it via prefix '%s*'.", expected, prefix)
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (f"{prefix}%",))
        candidates = [row[0] for row in cur]

    if len(candidates) == 1:
        LOG.warning("Found an updated table: '%s'.", candidates[0])
        return candidates[0]
    if not candidates:
        raise RuntimeError(
            f"No table found with prefix '{prefix}'. "
            "Check the exact name in the AEP UI and pass it explicitly."
        )
    raise RuntimeError(
        f"Found {len(candidates)} tables with prefix '{prefix}': {candidates}. "
        "Ambiguous name, pass the correct one explicitly."
    )


def build_union_query(datasets: Sequence[str], days: int, bot_table: str) -> sql.Composed:
    """A single query: UNION across all datasets + anti-join against your
    bot table. This way only user-agents NEVER seen before come out of AEP."""
    if days <= 0:
        raise ValueError("days must be positive")

    ns = sql.SQL(XDM_NAMESPACE)

    selects = [
        sql.SQL(
            "(SELECT DISTINCT {ns}.Device.useragent AS useragent "
            "FROM {dataset} "
            "WHERE timestamp >= current_date - interval {days} "
            "LIMIT {limit})"
        ).format(
            ns=ns,
            dataset=sql.Identifier(f"{_validate_dataset_name(d)}{DATASET_SUFFIX}"),
            days=sql.Literal(f"{days} day"),
            limit=sql.Literal(EXTRACTION_LIMIT),
        )
        for d in datasets
    ]

    return sql.SQL(
        """
        WITH all_ua AS (
        {unions}
        ),
        known_bots AS (
            SELECT DISTINCT lower(trim({ns}.BOT.user_agent)) AS ua_key
            FROM {bot_table}
            LIMIT {limit}
        )
        SELECT MIN(a.useragent) AS useragent
        FROM all_ua a
        LEFT JOIN known_bots k ON lower(trim(a.useragent)) = k.ua_key
        WHERE k.ua_key IS NULL
          AND a.useragent IS NOT NULL
          AND trim(a.useragent) <> ''
        GROUP BY lower(trim(a.useragent))
        LIMIT {limit}
        """
    ).format(
        unions=sql.SQL("\n            UNION\n        ").join(selects),
        ns=ns,
        bot_table=sql.Identifier(bot_table),
        limit=sql.Literal(EXTRACTION_LIMIT),
    )


def build_single_dataset_query(dataset: str, days: int) -> sql.Composed:
    ns = sql.SQL(XDM_NAMESPACE)
    return sql.SQL(
        "SELECT DISTINCT {ns}.Device.useragent AS useragent "
        "FROM {dataset} "
        "WHERE timestamp >= current_date - interval {days} "
        "AND {ns}.Device.useragent IS NOT NULL "
        "LIMIT {limit}"
    ).format(
        ns=ns,
        dataset=sql.Identifier(f"{_validate_dataset_name(dataset)}{DATASET_SUFFIX}"),
        days=sql.Literal(f"{days} day"),
        limit=sql.Literal(EXTRACTION_LIMIT),
    )


def fetch_known_bots(conn, bot_table: str) -> set[str]:
    ns = sql.SQL(XDM_NAMESPACE)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT DISTINCT lower(trim({ns}.BOT.user_agent)) "
                "FROM {bot_table} "
                "LIMIT {limit}"
            ).format(ns=ns, bot_table=sql.Identifier(bot_table), limit=sql.Literal(EXTRACTION_LIMIT))
        )
        return {row[0] for row in cur if row[0]}


def fetch_new_user_agents(conn, datasets: Sequence[str], days: int, bot_table: str) -> list[str]:
    """Tries the single unified query first. If it fails (renamed dataset,
    permissions, timeout), falls back to one query per dataset, skipping the
    ones that error out instead of killing the whole run."""
    try:
        with conn.cursor() as cur:
            LOG.info("Running the unified query across %s datasets...", len(datasets))
            cur.execute(build_union_query(datasets, days, bot_table))
            return [row[0] for row in cur if row[0]]
    except psycopg2.Error as exc:
        conn.rollback()
        LOG.warning("Unified query failed (%s). Falling back to per-dataset queries.", exc)

    known = fetch_known_bots(conn, bot_table)
    LOG.info("Bots already known in AEP: %s", f"{len(known):,}")

    # Normalized key (lower+trim) -> original UA. Avoids the same user-agent,
    # differing only by a trailing space or a different case between one
    # dataset and another, ending up duplicated in the final result.
    seen: dict[str, str] = {}
    for dataset in datasets:
        try:
            with conn.cursor() as cur:
                cur.execute(build_single_dataset_query(dataset, days))
                for (ua,) in cur:
                    if not ua:
                        continue
                    key = ua.strip().lower()
                    if key not in known and key not in seen:
                        seen[key] = ua
        except psycopg2.Error as exc:
            conn.rollback()
            LOG.error("Dataset %s skipped: %s", dataset, exc)
    return sorted(seen.values())


# --------------------------------------------------------------------------- #
# SFTP
# --------------------------------------------------------------------------- #

@contextmanager
def sftp_client(cfg: SftpConfig) -> Iterator[paramiko.SFTPClient]:
    """SSHClient instead of a bare Transport, so the host key gets verified.
    First time: ssh-keyscan -p 22 <host> >> ~/.ssh/known_hosts"""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(
        paramiko.RejectPolicy() if cfg.strict_host_key else paramiko.AutoAddPolicy()
    )
    if not cfg.strict_host_key:
        LOG.warning("Host key verification disabled: use only for testing.")

    try:
        client.connect(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.user,
            password=cfg.password,
            key_filename=cfg.key_path,
            look_for_keys=bool(cfg.key_path),
            allow_agent=False,
            timeout=30,
        )
        sftp = client.open_sftp()
        try:
            yield sftp
        finally:
            sftp.close()
    finally:
        client.close()


def upload_atomic(sftp: paramiko.SFTPClient, remote_path: str, payload: bytes) -> None:
    """Writes to a temp file, then renames: downstream ingestion can never
    read a half-written CSV."""
    tmp_path = f"{remote_path}.tmp"
    sftp.putfo(io.BytesIO(payload), tmp_path, confirm=True)
    try:
        sftp.posix_rename(tmp_path, remote_path)
    except (IOError, AttributeError):
        # Some servers don't support posix_rename: remove and rename instead.
        try:
            sftp.remove(remote_path)
        except IOError:
            pass
        sftp.rename(tmp_path, remote_path)
    LOG.info("Upload complete: %s (%s bytes)", remote_path, f"{len(payload):,}")


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

def notify(message: str, ok: bool = True) -> None:
    """Notifies on Teams if the webhook is configured, otherwise just logs.
    No GUI popups: those would block the process when run headless on a
    schedule."""
    LOG.info(message) if ok else LOG.error(message)

    webhook = os.getenv("TEAMS_WEBHOOK_URL")
    if not webhook:
        return
    payload = json.dumps(
        {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": "2ecc71" if ok else "e74c3c",
            "title": "Bot Detection AEP",
            "text": message,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:  # a failed notification must never fail the job
        LOG.warning("Teams notification failed: %s", exc)
