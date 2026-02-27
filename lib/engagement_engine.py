#!/usr/bin/env python3
"""Engagement Engine — hourly cron entry point.

Discovers conversations, drafts replies, sends to Telegram for approval.
Cleans up expired pending items.

Usage:
    python3 .datacore/modules/comms/lib/engagement_engine.py
    python3 .datacore/modules/comms/lib/engagement_engine.py --dry-run
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
                if key not in os.environ:  # Don't override existing
                    os.environ[key] = val


def run(dry_run: bool = False):
    """Run one cycle of the engagement engine."""
    load_env()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] Engagement engine starting...")

    # Load state
    st = state_mod.load(STATE_FILE)

    # Check daily limits
    posted_today = state_mod.today_count(st, "posted")
    max_per_day = st.get("config", {}).get("max_per_day", 30)
    max_per_hour = st.get("config", {}).get("max_per_hour", 20)

    if posted_today >= max_per_day:
        print(f"  Daily limit reached ({posted_today}/{max_per_day}). Skipping.")
        state_mod.save(st, STATE_FILE)
        return

    # Clean expired pending
    expired = state_mod.expire_old_pending(st)
    if expired:
        print(f"  Expired {expired} pending drafts")

    # Phase 1: Discover
    print("  Phase 1: Discovering conversations...")
    try:
        conversations = discover_mod.discover(st)
        print(f"  Found {len(conversations)} new conversations")
    except Exception as e:
        print(f"  Discovery failed: {e}", file=sys.stderr)
        state_mod.save(st, STATE_FILE)
        return

    # Phase 1b: Monitor replies to our posted tweets
    print("  Phase 1b: Monitoring replies to our tweets...")
    try:
        follow_ups = monitor_mod.monitor(st)
        if follow_ups:
            print(f"  Found {len(follow_ups)} follow-up conversations")
            conversations = follow_ups + conversations  # Prioritise follow-ups
        else:
            print("  No follow-ups found")
    except Exception as e:
        print(f"  Monitor failed: {e}", file=sys.stderr)

    if not conversations:
        print("  No new conversations. Done.")
        state_mod.save(st, STATE_FILE)
        return

    # Phase 2+3: Draft and notify (up to max_per_hour)
    pending_count = len(st.get("pending", []))
    slots = min(max_per_hour, len(conversations), max_per_day - posted_today)

    # Also limit total pending to avoid flooding Telegram
    max_pending = 30
    slots = min(slots, max_pending - pending_count)

    if slots <= 0:
        print(f"  Too many pending ({pending_count}). Skipping drafting.")
        state_mod.save(st, STATE_FILE)
        return

    for i, conv in enumerate(conversations[:slots]):
        author = conv.get("author", "unknown")
        tweet_id = conv.get("tweet_id", "")

        # Skip if recently replied to this author
        cooldown = st.get("config", {}).get("cooldown_hours", 24)
        if state_mod.recently_replied_to(st, author, cooldown):
            print(f"  [{i+1}] Skipping {author} (replied within {cooldown}h)")
            state_mod.mark_seen(st, tweet_id)
            continue

        # Draft
        print(f"  [{i+1}] Drafting reply to {author}...")
        try:
            draft = draft_mod.draft_reply(conv)
            print(f"       Draft ({len(draft)} chars): {draft[:80]}...")
        except Exception as e:
            print(f"       Draft failed: {e}", file=sys.stderr)
            state_mod.mark_seen(st, tweet_id)
            continue

        # Mark seen
        state_mod.mark_seen(st, tweet_id)
        state_mod._bump_stat(st, "drafted")

        if dry_run:
            print(f"       [DRY RUN] Would send to Telegram")
            continue

        # Add pending FIRST to get real draft_id
        url = conv.get("url", f"https://x.com/{author.lstrip('@')}/status/{tweet_id}")
        draft_id = state_mod.add_pending(
            st,
            target_tweet_id=tweet_id,
            target_author=author,
            target_content=conv.get("content", ""),
            target_url=url,
            draft_reply=draft,
        )

        # Then notify with real draft_id
        try:
            msg_id = notify_mod.send_draft_for_approval(
                draft_id=draft_id,
                target_author=author,
                target_content=conv.get("content", ""),
                target_url=url,
                draft_reply=draft,
            )
            # Update pending item with telegram message ID
            pending_item = state_mod.get_pending(st, draft_id)
            if pending_item:
                pending_item["telegram_message_id"] = msg_id
            print(f"       Sent to Telegram (draft {draft_id}, msg {msg_id})")
        except Exception as e:
            print(f"       Notify failed: {e}", file=sys.stderr)
            # Keep draft in pending — can still be reviewed via MCP tools

        # Rate limit between drafts
        if i < slots - 1:
            time.sleep(1)

    # Save state
    state_mod.save(st, STATE_FILE)

    # Summary
    print(f"\n  Summary:")
    print(f"    Discovered: {len(conversations)}")
    print(f"    Drafted: {slots}")
    print(f"    Pending: {len(st.get('pending', []))}")
    print(f"    Posted today: {posted_today}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
