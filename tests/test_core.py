"""
Regression tests for the core detection engine (bot_detection.core).

These need nothing beyond pandas + the detection libraries — no AEP, no
SFTP, no environment variables. Mostly useful when you update the detection
libraries: if an upstream regex update breaks a known case, you'll notice
here instead of after uploading the wrong CSV to AEP.

    pytest tests/test_core.py -v
"""

import pytest

from bot_detection import (
    ALLOWLIST_PATTERN,
    BOT_PATTERN,
    classify,
    load_bot_categorizer,
    load_detectors,
)

DETECTORS = load_detectors()
CATEGORIZER = load_bot_categorizer()


def is_bot(ua: str, min_votes: int = 1) -> bool:
    return bool(classify([ua], DETECTORS, min_votes)["is_bot"].iloc[0])


BOTS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
    "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)",
    "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)",
    "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)",
    "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)",
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Mozilla/5.0 (compatible; Claude-User/1.0; +https://www.anthropic.com/claude-user)",
    "Mozilla/5.0 (compatible; Claude-SearchBot/1.0; +https://www.anthropic.com/claude-searchbot)",
    "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "Mozilla/5.0 (compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
    "Mozilla/5.0 (compatible; meta-externalagent/1.1; "
    "+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
    "Applebot/0.1 (+http://www.apple.com/go/applebot) Applebot-Extended",
    "Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)",
    "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)",
    "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "python-requests/2.31.0",
    "curl/8.4.0",
    "Go-http-client/1.1",
    "okhttp/4.9.3",
    "axios/1.6.2",
    "Java/1.8.0_181",
    "Scrapy/2.11.0 (+https://scrapy.org)",
    "Wget/1.21.3",
    "PostmanRuntime/7.36.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Chrome-Lighthouse",
    # OneTrust CMP Scanner: real UA confirmed via Cloudflare Radar. Disguises
    # itself as a normal Chrome browser and only appends ";OneTrust;" at the
    # end: without the dedicated pattern in BOT_PATTERN, the ensemble only
    # catches it thanks to a single detector (crawler-user-agents) out of three.
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.6099.199 Safari/537.36;OneTrust;",
]

HUMANS = [
    # Real desktop and mobile browsers
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Mobile Safari/537.36",
    # The historical false positive: CUBOT is a smartphone manufacturer
    "Mozilla/5.0 (Linux; Android 11; CUBOT NOTE 20 PRO) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/95.0.4638.74 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; CUBOT X30) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/88.0.4324.181 Mobile Safari/537.36",
]


@pytest.mark.parametrize("ua", BOTS)
def test_bots_are_recognized(ua):
    assert is_bot(ua), f"not recognized as a bot: {ua}"


@pytest.mark.parametrize("ua", HUMANS)
def test_humans_are_not_flagged(ua):
    assert not is_bot(ua), f"false positive: {ua}"


def test_allowlist_takes_precedence():
    """Even if a detector would vote bot, the allowlist wins."""
    ua = "Mozilla/5.0 (Linux; Android 11; CUBOT NOTE 20 PRO) Mobile Safari/537.36"
    assert ALLOWLIST_PATTERN.search(ua)
    row = classify([ua], DETECTORS, min_votes=1).iloc[0]
    assert row["is_bot"] is False or not row["is_bot"]
    assert row["detected_by"] == "allowlist"


def test_regex_fallback_covers_http_clients():
    """The regex alone must hold up even with no libraries installed."""
    for ua in ["python-requests/2.31.0", "curl/8.4.0", "Go-http-client/1.1"]:
        assert BOT_PATTERN.search(ua), ua


def test_output_has_the_expected_columns():
    out = classify(["curl/8.4.0"], DETECTORS, min_votes=1)
    assert list(out.columns) == ["User_agent", "is_bot", "votes", "detected_by", "category"]


# Expected category for the major AI bots (empirically verified: 12/13 AI
# bots known as of early 2026 get 3/3 consensus from the ensemble; the
# category below comes from device_detector, the only one of the three that
# carries a taxonomy beyond a plain bot/non-bot boolean).
AI_BOT_CATEGORIES = {
    "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)": "AI Data Scraper",
    "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)": "AI Assistant",
    "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)": "AI Search Crawler",
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)": "AI Data Scraper",
    "Mozilla/5.0 (compatible; Claude-User/1.0; +https://www.anthropic.com/claude-user)": "AI Assistant",
    "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)": "AI Search Crawler",
}


@pytest.mark.parametrize("ua,expected_category", AI_BOT_CATEGORIES.items())
def test_ai_bot_category(ua, expected_category):
    got = CATEGORIZER(ua)
    assert got == expected_category, f"expected {expected_category!r}, got {got!r} for {ua}"


def test_category_empty_for_non_bots():
    """Category is only computed for UAs that are bots: stays empty for a human."""
    human_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    assert CATEGORIZER(human_ua) == ""
