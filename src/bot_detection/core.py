"""
Core bot-detection engine: an ensemble of open-source User-Agent classifiers
with majority voting, plus optional bot-category enrichment.

This module has **no dependency on Adobe Experience Platform or any other
external service**. It only needs `pandas` and, optionally, the three
detection libraries listed in `requirements.txt` (it degrades gracefully —
with a warning — if any of them is missing). You can use it standalone on
any list of user-agent strings, regardless of where they came from:

    from bot_detection.core import load_detectors, load_bot_categorizer, classify

    detectors = load_detectors()
    categorizer = load_bot_categorizer()
    df = classify(["curl/8.4.0", "Mozilla/5.0 ..."], detectors, min_votes=1, categorizer=categorizer)

See `bot_detection.aep_pipeline` and `bot_detection.cli` for the parts that
integrate this engine with Adobe Experience Platform specifically — those
require `psycopg2` and `paramiko`, which this module does not.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from typing import Callable, Sequence

import pandas as pd

LOG = logging.getLogger("bot_detection.core")

# Fallback / reinforcement pattern. Covers HTTP clients and headless browsers
# that the libraries sometimes classify as "client" rather than "bot".
BOT_PATTERN = re.compile(
    r"""
    bot\b | \bbots\b | crawl | spider | scrap | slurp | fetcher | archiver
    | curl/ | \bwget\b | python-requests | python-urllib | aiohttp | httpx
    | go-http-client | okhttp | axios | node-fetch | got\s*\( | \bbun/ | \bdeno/
    | java/\d | apache-httpclient | libwww-perl | guzzlehttp | restsharp
    | scrapy | selenium | phantomjs | headlesschrome | puppeteer | playwright
    | lighthouse | pagespeed | gtmetrix | pingdom | uptime | statuscake
    | postmanruntime | insomnia | powershell | winhttp | zgrab | masscan
    | facebookexternalhit | whatsapp | telegrambot | discordbot | slackbot
    | preview | validator | monitoring | healthcheck
    | onetrust | cookiebot | usercentrics | trustarc | osano | didomi | termly
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Known false positives: real devices or apps whose name happens to contain
# "bot". CUBOT is a smartphone manufacturer; the rest are cautious additions.
ALLOWLIST_PATTERN = re.compile(r"cubot|abbott|botim|robot\s*vacuum", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Detector ensemble
# --------------------------------------------------------------------------- #

Detector = tuple[str, Callable[[str], bool]]


def load_detectors() -> list[Detector]:
    """Load the available detectors. Missing libraries are skipped with a
    warning: the script stays runnable even in a minimal environment (in the
    worst case, with only the reinforcement regex active)."""
    detectors: list[Detector] = []

    try:
        # NB: uses the Bot parser directly instead of DeviceDetector(ua).parse().
        # The full parse(), even when a UA is already recognized as a bot,
        # keeps going and also resolves OS/client/device (wasted work for our
        # purposes): ~220x slower for the same result (benchmark: 7.5ms vs
        # 0.03ms/UA).
        from device_detector.parser.device.bot import Bot  # type: ignore

        @lru_cache(maxsize=250_000)
        def _device_detector(ua: str) -> bool:
            try:
                return bool(Bot(ua, None).parse().is_bot())
            except Exception:  # malformed regex/yaml on exotic UAs
                return False

        detectors.append(("device_detector", _device_detector))
    except ImportError:
        LOG.warning("device_detector not installed (pip install device_detector)")

    try:
        import crawleruseragents  # type: ignore

        @lru_cache(maxsize=250_000)
        def _crawler_user_agents(ua: str) -> bool:
            try:
                return bool(crawleruseragents.is_crawler(ua))
            except Exception:
                return False

        detectors.append(("crawler_user_agents", _crawler_user_agents))
    except ImportError:
        LOG.warning("crawler-user-agents not installed (pip install crawler-user-agents)")

    try:
        from crawlerdetect import CrawlerDetect  # type: ignore

        _cd = CrawlerDetect()

        @lru_cache(maxsize=250_000)
        def _crawlerdetect(ua: str) -> bool:
            try:
                return bool(_cd.isCrawler(ua))
            except Exception:
                return False

        detectors.append(("crawlerdetect", _crawlerdetect))
    except ImportError:
        LOG.warning("crawlerdetect not installed (pip install crawlerdetect)")

    detectors.append(("regex", lambda ua: BOT_PATTERN.search(ua) is not None))

    if len(detectors) == 1:
        LOG.warning(
            "No detection library available: falling back to the reinforcement "
            "regex only. Coverage will be noticeably worse."
        )
    LOG.info("Active detectors: %s", ", ".join(name for name, _ in detectors))
    return detectors


def load_bot_categorizer() -> Callable[[str], str]:
    """Returns a function that, for a UA already recognized as a bot, tries
    to classify it by category (e.g. 'AI Data Scraper', 'AI Assistant',
    'AI Search Crawler', 'Search bot'...). Uses device_detector, the only one
    of the three detectors that carries a taxonomy beyond a plain boolean.
    If the library isn't installed, always returns an empty string:
    categorization is an enrichment, not a hard dependency.

    The categories 'AI Data Scraper' / 'AI Assistant' / 'AI Search Crawler'
    map, in vendor language (OpenAI, Anthropic, Perplexity...), to: a training
    crawler, an on-demand fetch triggered by a user inside the AI product, and
    a crawler that feeds the AI product's own search index, respectively.
    It's the same distinction their robots.txt publish with separate tokens
    (e.g. GPTBot vs ChatGPT-User vs OAI-SearchBot)."""
    try:
        from device_detector.parser.device.bot import Bot  # type: ignore
    except ImportError:
        return lambda ua: ""

    @lru_cache(maxsize=250_000)
    def _category(ua: str) -> str:
        try:
            bot = Bot(ua, None).parse()
            if not bot.is_bot():
                return ""
            return str(bot.ua_data.get("category", "") or "")
        except Exception:
            return ""

    return _category


def classify(
    user_agents: Sequence[str],
    detectors: list[Detector],
    min_votes: int,
    categorizer: Callable[[str], str] | None = None,
) -> pd.DataFrame:
    """Classify a list of distinct UAs. Returns a DataFrame with the flag,
    the number of votes, the names of the detectors that voted 'bot', and —
    if a categorizer is passed — the bot's category (empty for non-bots and
    for bots no detector can categorize)."""
    rows = []
    total = len(user_agents)
    for i, ua in enumerate(user_agents, start=1):
        if i % 10_000 == 0:
            LOG.info("Classified %s/%s user-agents", f"{i:,}", f"{total:,}")

        if ALLOWLIST_PATTERN.search(ua):
            rows.append((ua, False, 0, "allowlist", ""))
            continue

        voters = [name for name, fn in detectors if fn(ua)]
        is_bot = len(voters) >= min_votes
        category = categorizer(ua) if (categorizer and is_bot) else ""
        rows.append((ua, is_bot, len(voters), ",".join(voters), category))

    return pd.DataFrame(
        rows, columns=["User_agent", "is_bot", "votes", "detected_by", "category"]
    )


# --------------------------------------------------------------------------- #
# Parallel classification (optional)
# --------------------------------------------------------------------------- #
#
# classify() is CPU-bound (regex, pattern matching): no network, no I/O.
# Threads wouldn't help (the GIL serializes them anyway); processes do.
#
# The functions below are deliberately module-level, not local closures,
# so that multiprocessing's 'spawn' start method (the default and only
# option on Windows) can pickle and re-import them in worker processes.
#
# Measured fixed cost to start a worker: ~0.44s (compiling crawlerdetect's
# ~1,462 regexes and loading device_detector's YAML files). Only worth it
# above ~20-30k unique rows, and only if the machine actually has multiple
# cores: on a single core, multiprocessing is pure overhead.

_worker_detectors: list[Detector] | None = None
_worker_categorizer: Callable[[str], str] | None = None
_worker_min_votes: int = 1


def _init_worker(min_votes: int) -> None:
    """Runs once when each worker process starts: builds the detectors once
    per worker, not once per call."""
    global _worker_detectors, _worker_categorizer, _worker_min_votes
    _worker_detectors = load_detectors()
    _worker_categorizer = load_bot_categorizer()
    _worker_min_votes = min_votes


def _classify_chunk(user_agents: list[str]) -> list[tuple]:
    """Classify a subset of UAs inside a worker process, reusing the
    detectors already built by _init_worker."""
    rows = []
    for ua in user_agents:
        if ALLOWLIST_PATTERN.search(ua):
            rows.append((ua, False, 0, "allowlist", ""))
            continue
        voters = [name for name, fn in _worker_detectors if fn(ua)]
        is_bot = len(voters) >= _worker_min_votes
        category = _worker_categorizer(ua) if is_bot else ""
        rows.append((ua, is_bot, len(voters), ",".join(voters), category))
    return rows


def classify_parallel(
    user_agents: Sequence[str],
    min_votes: int,
    n_workers: int | None = None,
    chunk_size: int = 2000,
) -> pd.DataFrame:
    """Same interface/output as classify(), but spread across multiple
    processes. n_workers=None uses os.cpu_count(). On small lists, the
    single-process classify() stays faster: there's no automatic threshold
    check here, the decision whether to use this is left to the caller."""
    n_workers = n_workers or os.cpu_count() or 4
    user_agents = list(user_agents)
    chunks = [user_agents[i : i + chunk_size] for i in range(0, len(user_agents), chunk_size)]

    rows: list[tuple] = []
    with ProcessPoolExecutor(
        max_workers=n_workers, initializer=_init_worker, initargs=(min_votes,)
    ) as pool:
        for i, result in enumerate(pool.map(_classify_chunk, chunks), start=1):
            rows.extend(result)
            if i % 10 == 0 or i == len(chunks):
                LOG.info("Chunks classified: %s/%s", i, len(chunks))

    return pd.DataFrame(
        rows, columns=["User_agent", "is_bot", "votes", "detected_by", "category"]
    )
