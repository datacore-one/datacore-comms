#!/usr/bin/env python3
"""Content scheduler for original posts (not replies).

Reads a content calendar from the space and queues original tweets,
threads, and long-form content. Respects rate limits and posts
via x_poster.py.

Usage:
    python3 content_scheduler.py --today      # post today's scheduled content
    python3 content_scheduler.py --dry-run    # show what would be posted
    python3 content_scheduler.py --queue      # list queued posts
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))

from x_poster import XPoster
from event_logger import log_event
from comms_config import load_config

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
SCHEDULE_FILE = DATA_DIR / "1-tracks" / "comms" / "content-schedule.json"
STATE_FILE = DATA_DIR / ".datacore" / "state" / "content-scheduler-state.json"


def _default_state() -> dict:
    return {
        "posted_ids": [],
        "last_run": None,
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return _default_state()


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_schedule() -> List[Dict]:
    """Load content schedule from space."""
    if not SCHEDULE_FILE.exists():
        return []
    try:
        data = json.loads(SCHEDULE_FILE.read_text())
        return data.get("posts", [])
    except (json.JSONDecodeError, KeyError):
        return []


def filter_todays_posts(posts: List[Dict]) -> List[Dict]:
    """Return posts scheduled for today that haven't been posted."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = load_state()
    posted_ids = set(state.get("posted_ids", []))

    result = []
    for post in posts:
        if post.get("id") in posted_ids:
            continue
        if post.get("date") == today or post.get("date") == "daily":
            result.append(post)
    return result


def post_content(post: Dict, poster: XPoster, dry_run: bool = False) -> Dict:
    """Post a single content item.

    Supports: tweet, thread, quote_rt
    """
    post_type = post.get("type", "tweet")
    text = post.get("text", "")

    if dry_run:
        print(f"  [DRY RUN] Would post {post_type}: {text[:60]}...")
        return {"status": "dry_run", "type": post_type}

    try:
        if post_type == "tweet":
            result = poster.post(text)
        elif post_type == "reply":
            result = poster.reply(text, post.get("reply_to_id", ""))
        elif post_type == "quote_rt":
            result = poster.quote_rt(text, post.get("quote_tweet_id", ""))
        elif post_type == "thread":
            # Post first tweet, then reply to self for each subsequent tweet
            tweets = post.get("tweets", [])
            if not tweets:
                return {"status": "error", "reason": "empty thread"}
            result = poster.post(tweets[0])
            last_id = result.get("data", {}).get("id", "")
            for t in tweets[1:]:
                time.sleep(2)
                result = poster.reply(t, last_id)
                last_id = result.get("data", {}).get("id", "")
        else:
            return {"status": "error", "reason": f"unknown type: {post_type}"}

        tweet_id = result.get("data", {}).get("id", "")
        log_event("post", {"type": post_type, "id": tweet_id, "mode": "scheduled"})
        return {"status": "posted", "tweet_id": tweet_id, "type": post_type}

    except Exception as e:
        log_event("error", {"stage": "scheduler_post", "post_id": post.get("id"), "error": str(e)})
        return {"status": "error", "reason": str(e)}


def run(account: str = "default", dry_run: bool = False):
    """Run the content scheduler."""
    config = load_config()
    posts = load_schedule()
    todays = filter_todays_posts(posts)

    if not todays:
        print("No scheduled posts for today.")
        return

    print(f"Found {len(todays)} posts scheduled for today.")

    try:
        poster = XPoster(account=account, config=config)
    except Exception as e:
        print(f"Failed to initialize poster: {e}")
        return

    state = load_state()
    posted_ids = state.get("posted_ids", [])

    for post in todays:
        print(f"  Posting: {post.get('text', '')[:60]}...")
        result = post_content(post, poster, dry_run=dry_run)
        print(f"  Result: {result['status']}")

        if result["status"] == "posted":
            posted_ids.append(post["id"])

        # Rate limit between posts
        if post != todays[-1]:
            time.sleep(5)

    state["posted_ids"] = posted_ids
    save_state(state)
    print(f"Done. {len([p for p in posted_ids if p in [x.get('id') for x in todays]])} posted today.")


def queue_status():
    """Show upcoming scheduled posts."""
    posts = load_schedule()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = load_state()
    posted_ids = set(state.get("posted_ids", []))

    upcoming = [p for p in posts if p.get("id") not in posted_ids]
    print(f"Upcoming posts ({len(upcoming)} total):")
    for post in upcoming[:20]:
        marker = "[POSTED]" if post.get("id") in posted_ids else ""
        print(f"  {post.get('date', '???')} {marker} {post.get('type', 'tweet')}: {post.get('text', '')[:60]}...")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    account = "default"
    for arg in sys.argv:
        if arg.startswith("--account="):
            account = arg.split("=", 1)[1]

    if "--queue" in sys.argv:
        queue_status()
    else:
        run(account=account, dry_run=dry_run)
