#!/usr/bin/env python3
"""Engagement pipeline state management.

Handles seen conversations, pending approvals, posted replies, daily stats.
State file: .datacore/state/engagement-state.json
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


STATE_FILE = Path("~/.datacore-state/engagement-state.json")  # overridden by callers


def _default_state():
    return {
        "config": {
            "max_per_hour": 5,
            "max_per_day": 15,
            "cooldown_hours": 24,
            "blacklist_authors": [],
            "query_rotation_index": 0,
        },
        "seen": {},
        "pending": [],
        "posted": [],
        "rejected": [],
        "daily_stats": {},
        "last_run": None,
    }


def load(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text())
    return _default_state()


def save(state: dict, state_file: Path):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state_file.write_text(json.dumps(state, indent=2))


def is_seen(state: dict, tweet_id: str) -> bool:
    return tweet_id in state.get("seen", {})


def mark_seen(state: dict, tweet_id: str):
    state.setdefault("seen", {})[tweet_id] = datetime.now(timezone.utc).isoformat()


def add_pending(state: dict, target_tweet_id: str, target_author: str,
                target_content: str, target_url: str, draft_reply: str,
                telegram_message_id: int = None) -> str:
    draft_id = str(uuid.uuid4())[:8]
    state.setdefault("pending", []).append({
        "id": draft_id,
        "target_tweet_id": target_tweet_id,
        "target_author": target_author,
        "target_content_snippet": target_content[:120],
        "target_url": target_url,
        "draft_reply": draft_reply,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "telegram_message_id": telegram_message_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
    })
    return draft_id


def get_pending(state: dict, draft_id: str) -> Optional[dict]:
    for p in state.get("pending", []):
        if p["id"] == draft_id:
            return p
    return None


def approve_pending(state: dict, draft_id: str, our_tweet_id: str) -> Optional[dict]:
    pending = state.get("pending", [])
    for i, p in enumerate(pending):
        if p["id"] == draft_id:
            item = pending.pop(i)
            state.setdefault("posted", []).append({
                "our_tweet_id": our_tweet_id,
                "target_tweet_id": item["target_tweet_id"],
                "target_author": item["target_author"],
                "draft_reply": item["draft_reply"],
                "posted_at": datetime.now(timezone.utc).isoformat(),
            })
            _bump_stat(state, "posted")
            return item
    return None


def reject_pending(state: dict, draft_id: str) -> Optional[dict]:
    pending = state.get("pending", [])
    for i, p in enumerate(pending):
        if p["id"] == draft_id:
            item = pending.pop(i)
            state.setdefault("rejected", []).append({
                **item,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
            })
            _bump_stat(state, "rejected")
            return item
    return None


def expire_old_pending(state: dict) -> int:
    now = datetime.now(timezone.utc)
    pending = state.get("pending", [])
    expired = []
    remaining = []
    for p in pending:
        exp = datetime.fromisoformat(p["expires_at"])
        if now > exp:
            expired.append(p)
        else:
            remaining.append(p)
    state["pending"] = remaining
    for _ in expired:
        _bump_stat(state, "expired")
    return len(expired)


def today_count(state: dict, key: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return state.get("daily_stats", {}).get(today, {}).get(key, 0)


def _bump_stat(state: dict, key: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = state.setdefault("daily_stats", {}).setdefault(today, {})
    stats[key] = stats.get(key, 0) + 1


def recently_replied_to(state: dict, author: str, hours: int = 24) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for p in state.get("posted", []):
        if p.get("target_author") == author:
            posted_at = datetime.fromisoformat(p["posted_at"])
            if posted_at > cutoff:
                return True
    return False
