#!/usr/bin/env python3
"""Discover engagement-worthy conversations on X via x.ai API.

Uses Grok's x_search tool to find recent privacy/decentralization conversations.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import List


XAI_URL = "https://api.x.ai/v1/responses"

# Query templates — run QUERIES_PER_CYCLE per engine run for broader coverage.
# Aligned with Fairdrop campaign target segments:
#   1. Surveillance & Big Tech (privacy-conscious professionals)
#   2. Journalists & press freedom (source protection, whistleblowing)
#   3. Data breaches & policy (GDPR, surveillance capitalism frustration)
#   4. AI/agent builders (agentic AI, file handling, private infra)
#   5. File sharing frustrations (WeTransfer, Google Drive, Dropbox)
#   6. Cypherpunk & privacy architecture (thoughtful crypto/privacy voices)
#   7. Crypto/web3 privacy builders (NOT token shills — real builders)
QUERIES_PER_CYCLE = 7  # Run all queries every cycle for maximum coverage

QUERIES = [
    "Find recent tweets (last 24h) about government surveillance, mass data collection, metadata tracking, or Big Tech privacy violations from people who are genuinely concerned (not promoting a product). Focus on tweets with real engagement from accounts with >1K followers. Exclude crypto token promotions and @FairDataSociety.",
    "Find recent tweets (last 24h) from journalists, reporters, or press freedom advocates discussing source protection, secure communication, whistleblower tools, or digital safety for journalists. Focus on tweets from verified journalists or press freedom organizations. Exclude @FairDataSociety.",
    "Find recent tweets (last 24h) about data breaches, GDPR enforcement, privacy policy outrage, or people frustrated with surveillance capitalism (Google tracking, Meta data selling, etc). Look for tweets expressing genuine frustration, not product promotions. Exclude @FairDataSociety.",
    "Find recent tweets (last 24h) about AI agents handling files, agentic AI needing private infrastructure, MCP servers for file sharing, or the problem of AI agents and data privacy. Focus on AI/ML builders and researchers. Exclude @FairDataSociety.",
    "Find recent tweets (last 24h) from people frustrated with WeTransfer, Google Drive, Dropbox, or iCloud — complaining about privacy, file size limits, data collection, or looking for alternatives. Real user complaints, not competitor marketing. Exclude @FairDataSociety.",
    "Find recent tweets (last 24h) about privacy by design, end-to-end encryption for files, zero-knowledge architectures, or self-sovereign data from thoughtful accounts. Look for conversations where someone is making a genuine argument about privacy architecture, not just promoting a product. Exclude @FairDataSociety.",
    "Find recent tweets (last 24h) from crypto/web3 builders or cypherpunk voices discussing privacy infrastructure, data sovereignty, decentralized identity, or censorship resistance. EXCLUDE token price discussions, airdrop promotions, and 'Good morning crypto' style tweets. Include only people making substantive technical or philosophical arguments. Exclude @FairDataSociety.",
]

SYSTEM_PROMPT = """You are a research assistant finding conversations worth engaging with for @FairDataSociety — a project building privacy-first file sharing (Fairdrop) and data sovereignty infrastructure.

IMPORTANT FILTERING RULES:
- EXCLUDE tweets that are just promoting a crypto token or DeFi project
- EXCLUDE "Good morning/night" style engagement farming tweets
- EXCLUDE tweets that are just retweeting with no original thought
- EXCLUDE tweets from bot-like accounts or low-quality engagement farmers
- EXCLUDE tweets with restricted replies (only mentioned users, only followers, only subscribers, only verified)
- ONLY include tweets where reply_settings is "everyone" — i.e. anyone can reply
- PREFER tweets from real people expressing genuine opinions, frustrations, or insights
- PREFER tweets with real engagement (replies, not just likes from bots)
- PREFER journalists, researchers, developers, privacy advocates, tech professionals
- PREFER accounts with under 100K followers — large accounts often restrict replies

For each relevant tweet found, return a JSON array of objects with these fields:
- tweet_id: the tweet's ID
- author: @handle of the tweet author
- content: the full tweet text
- url: the tweet URL (https://x.com/{author}/status/{tweet_id})
- views: estimated view count (number, or 0 if unknown)
- relevance: 1-10 score for how relevant this is to privacy, data sovereignty, or file sharing
- reply_settings: "everyone", "mentioned_users", "following", "subscribers", or "unknown"

Return ONLY the JSON array, no other text. If no relevant tweets found, return [].
Only include tweets with relevance >= 7 AND reply_settings of "everyone" or "unknown"."""


def discover(state: dict, xai_api_key: str = None) -> List[dict]:
    """Find conversations to engage with.

    Runs QUERIES_PER_CYCLE queries per call for broader coverage across
    different audience segments.

    Args:
        state: engagement state dict (for rotation index, seen list, config)
        xai_api_key: x.ai API key (or from env)

    Returns:
        List of conversation dicts: [{tweet_id, author, content, url, views, relevance}]
    """
    api_key = xai_api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY not set")

    config = state.get("config", {})
    seen = state.get("seen", {})
    blacklist = set(config.get("blacklist_authors", []))
    blacklist.add("@FairDataSociety")

    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_conversations = []
    seen_tweet_ids = set()  # Dedup across queries

    for _ in range(QUERIES_PER_CYCLE):
        idx = config.get("query_rotation_index", 0) % len(QUERIES)
        query = QUERIES[idx]
        config["query_rotation_index"] = idx + 1

        try:
            convs = _run_query(api_key, query, from_date)
        except Exception as e:
            print(f"    Query {idx} failed: {e}")
            continue

        # Filter and dedup
        for conv in convs:
            tid = str(conv.get("tweet_id", ""))
            author = conv.get("author", "")
            reply_settings = conv.get("reply_settings", "unknown")
            if tid in seen or tid in seen_tweet_ids:
                continue
            if author.lower() in {b.lower() for b in blacklist}:
                continue
            # Skip tweets with known reply restrictions
            if reply_settings in ("mentioned_users", "following", "subscribers", "verified"):
                continue
            seen_tweet_ids.add(tid)
            all_conversations.append(conv)

    # Sort by relevance (highest first)
    all_conversations.sort(key=lambda c: c.get("relevance", 0), reverse=True)
    return all_conversations


def _run_query(api_key: str, query: str, from_date: str) -> List[dict]:
    """Execute a single x.ai discovery query."""
    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "tools": [
            {
                "type": "x_search",
                "x_search": {
                    "from_date": from_date,
                    "excluded_x_handles": ["FairDataSociety"],
                },
            }
        ],
    }

    body = json.dumps(payload).encode()
    req = Request(XAI_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DatacoreEngagement/1.0")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"x.ai API error {e.code}: {error_body}")

    raw_text = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    raw_text += block.get("text", "")

    return _parse_conversations(raw_text)


def _parse_conversations(text: str) -> List[dict]:
    """Extract JSON array from LLM response text."""
    # Find JSON array in text
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find [...] in text
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []
