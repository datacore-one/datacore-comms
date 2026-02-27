#!/usr/bin/env python3
"""Monitor replies to our posted tweets and queue follow-up conversations.

Uses x.ai x_search to find replies to @FairDataSociety's recent tweets.
Drafts follow-up replies and sends to Telegram for approval.

Run as part of the hourly engagement engine cycle.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import List

import engagement_draft as draft_mod


XAI_URL = "https://api.x.ai/v1/responses"

FOLLOW_UP_SYSTEM_PROMPT = """You are drafting follow-up replies for @FairDataSociety in an ongoing conversation about privacy and data sovereignty.

CONTEXT: Someone replied to a tweet from @FairDataSociety. Draft a reply that continues the conversation naturally.

STYLE:
- Short and sharp (under 240 chars)
- Conversational — this is a back-and-forth, not a broadcast
- Deepen the argument or acknowledge their point
- If they push back, engage honestly — don't be defensive
- If they agree, add another layer or extend the thought
- Never just say "great point" or "thanks for engaging"
- No hashtags, no emojis

Return ONLY the reply text, nothing else."""


def find_replies_to_our_tweets(posted_items: list, xai_api_key: str = None) -> List[dict]:
    """Search for replies to our recent posted tweets.

    Args:
        posted_items: List of posted items from state (last 48h)
        xai_api_key: x.ai API key

    Returns:
        List of reply dicts: [{their_tweet_id, their_author, their_content,
                               our_tweet_id, our_content, url}]
    """
    api_key = xai_api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY not set")

    if not posted_items:
        return []

    # Only check tweets posted in last 48h
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    recent = [
        p for p in posted_items
        if datetime.fromisoformat(p["posted_at"]) > cutoff
        and p.get("our_tweet_id") and p["our_tweet_id"] != "manual"
    ]

    if not recent:
        return []

    # Build query: search for replies to our recent tweets
    our_urls = [
        f"https://x.com/FairDataSociety/status/{p['our_tweet_id']}"
        for p in recent[:10]  # Cap at 10 to keep query manageable
    ]

    query = (
        f"Find tweets from the last 48 hours that are direct replies to @FairDataSociety. "
        f"Focus on these specific tweet URLs: {', '.join(our_urls[:5])}. "
        f"Return only genuine replies where someone is engaging with the argument, "
        f"not just likes, quote-tweets with no substance, or spam."
    )

    system_prompt = """Find replies to the specified @FairDataSociety tweets.

For each reply found, return a JSON array with:
- their_tweet_id: ID of their reply tweet
- their_author: @handle of the person who replied
- their_content: full text of their reply
- our_tweet_id: ID of the @FairDataSociety tweet they replied to
- our_content: text of the original @FairDataSociety tweet (if findable)
- url: URL of their reply tweet

Return ONLY the JSON array. If no genuine replies found, return []."""

    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "tools": [
            {
                "type": "x_search",
                "x_search": {
                    "from_date": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            }
        ],
    }

    body = json.dumps(payload).encode()
    req = Request(XAI_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        raise Exception(f"x.ai API error {e.code}: {e.read().decode()}")

    raw_text = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    raw_text += block.get("text", "")

    return _parse_replies(raw_text)


def draft_follow_up(reply_item: dict, xai_api_key: str = None) -> str:
    """Draft a follow-up reply to someone who replied to us.

    Args:
        reply_item: {their_content, their_author, our_content}

    Returns:
        Draft reply text
    """
    api_key = xai_api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY not set")

    their_content = reply_item.get("their_content", "")
    their_author = reply_item.get("their_author", "")
    our_content = reply_item.get("our_content", "")

    user_prompt = (
        f"Our tweet: {our_content}\n\n"
        f"{their_author} replied: {their_content}\n\n"
        f"Write a follow-up reply to continue this conversation."
    )

    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": FOLLOW_UP_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    body = json.dumps(payload).encode()
    req = Request(XAI_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        raise Exception(f"x.ai API error {e.code}: {e.read().decode()}")

    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text", "").strip()

    raise Exception("No draft returned from x.ai")


def monitor(state: dict, xai_api_key: str = None) -> List[dict]:
    """Check for replies to our tweets and return new conversations to queue.

    Returns list of conversation items ready to add as pending drafts.
    Each item has the same shape as a discovered conversation but with
    conversation_context set.
    """
    api_key = xai_api_key or os.environ.get("XAI_API_KEY")
    posted = state.get("posted", [])
    already_seen_replies = {
        p.get("their_tweet_id")
        for p in state.get("conversations", [])
    }

    replies = find_replies_to_our_tweets(posted, api_key)

    new_conversations = []
    for reply in replies:
        their_id = str(reply.get("their_tweet_id", ""))
        if their_id in already_seen_replies or their_id in state.get("seen", {}):
            continue

        try:
            follow_up = draft_follow_up(reply, api_key)
        except Exception as e:
            print(f"    Follow-up draft failed for {reply.get('their_author')}: {e}")
            continue

        new_conversations.append({
            "tweet_id": their_id,
            "author": reply.get("their_author", ""),
            "content": reply.get("their_content", ""),
            "url": reply.get("url", f"https://x.com/{reply.get('their_author', '').lstrip('@')}/status/{their_id}"),
            "draft_reply": follow_up,
            "is_follow_up": True,
            "our_tweet_id": reply.get("our_tweet_id", ""),
            "our_content": reply.get("our_content", ""),
        })

    return new_conversations


def _parse_replies(text: str) -> List[dict]:
    """Extract JSON array from response."""
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
