#!/usr/bin/env python3
"""Discover engagement-worthy conversations on X via x.ai API.

Space-agnostic: loads queries and brand config from comms-config.yaml.
Target communities: AI agents, knowledge management, collective intelligence,
Crypto+AI intersection, local-first AI, MCP ecosystem, agent memory.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import List

import event_logger

XAI_URL = "https://api.x.ai/v1/responses"


def _get_system_prompt(config: dict) -> str:
    """Build system prompt from brand config."""
    brand = config.get("brand", {})
    handle = brand.get("handle", "@brand")
    name = brand.get("name", "Brand")

    return f"""You are a research assistant finding conversations worth engaging with for {handle} — {name}.

IMPORTANT FILTERING RULES:
- EXCLUDE tweets that are just promoting a crypto token or DeFi project
- EXCLUDE "Good morning/night" style engagement farming tweets
- EXCLUDE tweets that are just retweeting with no original thought
- EXCLUDE tweets from bot-like accounts or low-quality engagement farmers
- EXCLUDE tweets with restricted replies (only mentioned users, only followers, only subscribers, only verified)
- ONLY include tweets where reply_settings is "everyone" — i.e. anyone can reply
- PREFER tweets from real people expressing genuine opinions, frustrations, or insights
- PREFER tweets with real engagement (replies, not just likes from bots)
- PREFER developers, researchers, builders, privacy advocates, AI practitioners
- PREFER accounts with under 100K followers — large accounts often restrict replies

For each relevant tweet found, return a JSON array of objects with these fields:
- tweet_id: the tweet's ID
- author: @handle of the tweet author
- content: the full tweet text
- url: the tweet URL (https://x.com/{{author}}/status/{{tweet_id}})
- views: estimated view count (number, or 0 if unknown)
- relevance: 1-10 score for how relevant this is
- reply_settings: "everyone", "mentioned_users", "following", "subscribers", or "unknown"

Return ONLY the JSON array, no other text. If no relevant tweets found, return [].
Only include tweets with relevance >= 7 AND reply_settings of "everyone" or "unknown"."""


def discover(state: dict, xai_api_key: str = None, config: dict = None) -> List[dict]:
    """Find conversations to engage with.

    Args:
        state: engagement state dict
        xai_api_key: x.ai API key (or from env)
        config: comms config dict (or auto-loaded)

    Returns:
        List of conversation dicts
    """
    from comms_config import load_config
    if config is None:
        config = load_config()

    api_key = xai_api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY not set")

    discovery_cfg = config.get("discovery", {})
    queries = discovery_cfg.get("queries", [])
    queries_per_cycle = discovery_cfg.get("queries_per_cycle", 3)
    min_relevance = discovery_cfg.get("min_relevance", 7)

    brand = config.get("brand", {})
    excluded_handles = set(brand.get("excluded_handles", []))
    our_handle = brand.get("handle", "").lstrip("@")
    if our_handle:
        excluded_handles.add(our_handle)

    config_state = state.get("config", {})
    seen = state.get("seen", {})
    blacklist = set(config_state.get("blacklist_authors", []))
    blacklist.update(excluded_handles)

    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_conversations = []
    seen_tweet_ids = set()

    # Rotate queries
    rotation_idx = config_state.get("query_rotation_index", 0)
    total_queries = len(queries)
    if total_queries == 0:
        event_logger.log_event("error", {"stage": "discover", "reason": "no queries configured"})
        return []

    for i in range(min(queries_per_cycle, total_queries)):
        idx = (rotation_idx + i) % total_queries
        query = queries[idx]

        try:
            convs = _run_query(api_key, query, from_date, config)
        except Exception as e:
            event_logger.log_event("error", {"stage": "discover", "query_idx": idx, "error": str(e)})
            print(f"    Query {idx} failed: {e}")
            continue

        for conv in convs:
            tid = str(conv.get("tweet_id", ""))
            author = conv.get("author", "").lstrip("@")
            reply_settings = conv.get("reply_settings", "unknown")
            relevance = conv.get("relevance", 0)

            if tid in seen or tid in seen_tweet_ids:
                continue
            if author.lower() in {b.lower().lstrip("@") for b in blacklist}:
                continue
            if reply_settings in ("mentioned_users", "following", "subscribers", "verified"):
                continue
            if relevance < min_relevance:
                continue

            seen_tweet_ids.add(tid)
            all_conversations.append(conv)

    config_state["query_rotation_index"] = (rotation_idx + queries_per_cycle) % total_queries
    state["config"] = config_state

    all_conversations.sort(key=lambda c: c.get("relevance", 0), reverse=True)

    event_logger.log_event("discover", {
        "queries_run": min(queries_per_cycle, total_queries),
        "conversations_found": len(all_conversations),
    })

    return all_conversations


def _run_query(api_key: str, query: str, from_date: str, config: dict) -> List[dict]:
    """Execute a single x.ai discovery query."""
    brand = config.get("brand", {})
    excluded = list(brand.get("excluded_handles", []))
    our_handle = brand.get("handle", "").lstrip("@")
    if our_handle and our_handle not in excluded:
        excluded.append(our_handle)

    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": _get_system_prompt(config)},
            {"role": "user", "content": query},
        ],
        "tools": [
            {
                "type": "x_search",
                "x_search": {
                    "from_date": from_date,
                    "excluded_x_handles": excluded,
                },
            }
        ],
    }

    body = json.dumps(payload).encode()
    req = Request(XAI_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DatacoreEngagement/2.0")

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
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

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
