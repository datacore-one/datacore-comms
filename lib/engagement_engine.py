#!/usr/bin/env python3
"""Engagement Engine — hourly cron entry point.

Discovers conversations, drafts replies, sends to Telegram for approval.
Cleans up expired pending items.

Space-agnostic: loads all brand/limits/voice config from comms-config.yaml.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))

import engagement_state as state_mod
import engagement_discover as discover_mod
import engagement_draft as draft_mod
import engagement_notify as notify_mod
import engagement_monitor as monitor_mod
import event_logger
from comms_config import load_config

# Paths
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
ENV_FILE = DATA_DIR / ".datacore" / "env" / ".env"


def load_env():
    """Load env vars from .env file."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key not in os.environ:
                    os.environ[key] = val


def run(dry_run: bool = False):
    """Run one cycle of the engagement engine."""
    load_env()
    config = load_config()
    limits = config.get("limits", {})
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] Engagement engine starting...")
    event_logger.log_event("engine_start", {"dry_run": dry_run})

    # Load state
    st = state_mod.load(STATE_FILE)

    # Sync config limits into state
    st.setdefault("config", {}).update({
        "max_per_hour": limits.get("max_per_hour", 5),
        "max_per_day": limits.get("max_per_day", 15),
        "cooldown_hours": limits.get("cooldown_hours", 24),
    })

    # Check daily limits
    posted_today = state_mod.today_count(st, "posted")
    max_per_day = st.get("config", {}).get("max_per_day", 15)
    max_per_hour = st.get("config", {}).get("max_per_hour", 5)

    if posted_today >= max_per_day:
        print(f"  Daily limit reached ({posted_today}/{max_per_day}). Skipping.")
        event_logger.log_event("rate_limit", {"reason": "daily_limit", "count": posted_today})
        state_mod.save(st, STATE_FILE)
        return

    # Clean expired pending
    expired = state_mod.expire_old_pending(st)
    if expired:
        print(f"  Expired {expired} pending drafts")
        event_logger.log_event("expired", {"count": expired})

    # Phase 1: Discover
    print("  Phase 1: Discovering conversations...")
    try:
        conversations = discover_mod.discover(st, config=config)
        print(f"  Found {len(conversations)} new conversations")
    except Exception as e:
        print(f"  Discovery failed: {e}", file=sys.stderr)
        event_logger.log_event("error", {"stage": "discover", "error": str(e)})
        state_mod.save(st, STATE_FILE)
        return

    # Phase 1b: Monitor replies to our posted tweets
    print("  Phase 1b: Monitoring replies to our tweets...")
    try:
        follow_ups = monitor_mod.monitor(st, config=config)
        if follow_ups:
            print(f"  Found {len(follow_ups)} follow-up conversations")
            conversations = follow_ups + conversations
        else:
            print("  No follow-ups found")
    except Exception as e:
        print(f"  Monitor failed: {e}", file=sys.stderr)
        event_logger.log_event("error", {"stage": "monitor", "error": str(e)})

    if not conversations:
        print("  No new conversations. Done.")
        state_mod.save(st, STATE_FILE)
        return

    # Phase 2+3: Draft and notify
    pending_count = len(st.get("pending", []))
    slots = min(max_per_hour, len(conversations), max_per_day - posted_today)
    max_pending = limits.get("max_pending", 30)
    slots = min(slots, max_pending - pending_count)

    if slots <= 0:
        print(f"  Too many pending ({pending_count}). Skipping drafting.")
        state_mod.save(st, STATE_FILE)
        return

    for i, conv in enumerate(conversations[:slots]):
        author = conv.get("author", "unknown")
        tweet_id = conv.get("tweet_id", "")

        cooldown = st.get("config", {}).get("cooldown_hours", 24)
        if state_mod.recently_replied_to(st, author, cooldown):
            print(f"  [{i+1}] Skipping {author} (replied within {cooldown}h)")
            state_mod.mark_seen(st, tweet_id)
            continue

        print(f"  [{i+1}] Drafting reply to {author}...")
        try:
            draft_result = draft_mod.draft_reply(conv, config=config)
            draft = draft_result["text"]
            print(f"       Draft ({len(draft)} chars, model={draft_result['model']}): {draft[:80]}...")
        except Exception as e:
            print(f"       Draft failed: {e}", file=sys.stderr)
            event_logger.log_event("error", {"stage": "draft", "author": author, "error": str(e)})
            state_mod.mark_seen(st, tweet_id)
            continue

        state_mod.mark_seen(st, tweet_id)
        state_mod._bump_stat(st, "drafted")

        if dry_run:
            print(f"       [DRY RUN] Would send to Telegram")
            continue

        url = conv.get("url", f"https://x.com/{author.lstrip('@')}/status/{tweet_id}")
        draft_id = state_mod.add_pending(
            st,
            target_tweet_id=tweet_id,
            target_author=author,
            target_content=conv.get("content", ""),
            target_url=url,
            draft_reply=draft,
        )

        try:
            msg_id = notify_mod.send_draft_for_approval(
                draft_id=draft_id,
                target_author=author,
                target_content=conv.get("content", ""),
                target_url=url,
                draft_reply=draft,
            )
            pending_item = state_mod.get_pending(st, draft_id)
            if pending_item:
                pending_item["telegram_message_id"] = msg_id
            print(f"       Sent to Telegram (draft {draft_id}, msg {msg_id})")
            event_logger.log_event("draft", {
                "draft_id": draft_id,
                "author": author,
                "chars": len(draft),
                "model": draft_result.get("model"),
                "prompt_version": draft_result.get("prompt_version"),
            })
        except Exception as e:
            print(f"       Notify failed: {e}", file=sys.stderr)
            event_logger.log_event("error", {"stage": "notify", "draft_id": draft_id, "error": str(e)})

        if i < slots - 1:
            time.sleep(1)

    state_mod.save(st, STATE_FILE)

    print(f"\n  Summary:")
    print(f"    Discovered: {len(conversations)}")
    print(f"    Drafted: {slots}")
    print(f"    Pending: {len(st.get('pending', []))}")
    print(f"    Posted today: {posted_today}")

    event_logger.log_event("engine_done", {
        "discovered": len(conversations),
        "drafted": slots,
        "pending": len(st.get("pending", [])),
        "posted_today": posted_today,
    })


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
