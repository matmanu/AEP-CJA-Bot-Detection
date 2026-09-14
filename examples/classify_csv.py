#!/usr/bin/env python3
"""
Standalone example: classify a plain list of user-agents with no AEP, no
SFTP, no environment variables — just `pip install -e ".[detectors]"` from
the repo root and run this file.

    python examples/classify_csv.py
    python examples/classify_csv.py --input my_user_agents.csv --column ua

If --input isn't given, this runs on a small built-in sample so you can see
output immediately without preparing any data.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from bot_detection import classify, load_bot_categorizer, load_detectors

SAMPLE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)",
    "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)",
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "python-requests/2.31.0",
    "curl/8.4.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    # A known false positive worth keeping in any sample: CUBOT is a real
    # smartphone brand, not a bot, despite the name.
    "Mozilla/5.0 (Linux; Android 11; CUBOT NOTE 20 PRO) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/95.0.4638.74 Mobile Safari/537.36",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="CSV file with a column of user-agent strings")
    parser.add_argument("--column", default="user_agent", help="column name in --input (default: user_agent)")
    parser.add_argument("--min-votes", type=int, default=1, help="detectors that must agree (default: 1)")
    parser.add_argument("--output", default=None, help="where to save the result as CSV (optional)")
    args = parser.parse_args()

    if args.input:
        user_agents = pd.read_csv(args.input)[args.column].dropna().astype(str).tolist()
        print(f"Loaded {len(user_agents):,} user-agents from {args.input}")
    else:
        user_agents = SAMPLE_USER_AGENTS
        print(f"No --input given: running on {len(user_agents)} built-in sample user-agents.\n")

    detectors = load_detectors()
    categorizer = load_bot_categorizer()

    result = classify(user_agents, detectors, args.min_votes, categorizer)

    print(f"\n{result['is_bot'].sum()} out of {len(result)} flagged as bots "
          f"({100 * result['is_bot'].mean():.1f}%)\n")
    print(result.to_string(index=False))

    if args.output:
        result.to_csv(args.output, index=False)
        print(f"\nSaved to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
