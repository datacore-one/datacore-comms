#!/usr/bin/env python3
"""Process Telegram callback approvals for engagement drafts.

Handles inline button callbacks from engagement_notify.py:
  eng:approve:{draft_id}  → post reply, mark posted
  eng:reject:{draft_id}   → mark rejected
  eng:edit:{draft_id}     → request edit (not implemented — user edits manually)
  eng:redraft:{draft_id}  → regenerate draft (not implemented)

Usage:
    python3 engagement_approve.py --poll   # poll Telegram for callbacks
    python3 engagement_approve.py --webhook # run as webhook receiver
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Add lib to path
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))

import engagement_state as state_mod
import event_logger

STATE_FILE = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))) \
    / ".datacore" / "state" / "engagement-state.json"


def get_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def get_updates(offset: int = None) -> list:
    """Poll Telegram for callback queries."""
    token = get_bot_token()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"limit": 100}
    if offset:
        params["offset"] = offset

    body = json.dumps(params).encode()
    req = Request(url, data=body, method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return result.get("result", [])
            return []
    except HTTPError as e:
        print(f"Telegram API error: {e.code}")
        return []


def answer_callback(callback_id: str, text: str = None):
    """Acknowledge callback to Telegram."""
    token = get_bot_token()
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError:
        pass


def edit_message(chat_id: int, message_id: int, new_text: str):
    """Edit the original Telegram message to show status."""
    token = get_bot_token()
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "HTML",
    }
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError:
        pass


def process_approval(callback_data: str, message_id: int, chat_id: int) -> dict:
    """Process an approval callback.

    Returns: {action, draft_id, result}
    """
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "eng":
        return {"action": "unknown", "draft_id": None, "result": "invalid format"}

    action_type, draft_id = parts[1], parts[2]
    st = state_mod.load(STATE_FILE)
    pending_item = state_mod.get_pending(st, draft_id)

    if not pending_item:
        return {"action": action_type, "draft_id": draft_id,
                "result": "draft not found or expired"}

    if action_type == "approve":
        # Post the reply
        from x_poster import XPoster
        # Use default account from config or 'default'
        try:
            poster = XPoster(account="default")
            result = poster.reply(
                pending_item["draft_reply"],
                pending_item["target_tweet_id"],
            )
            our_tweet_id = result.get("data", {}).get("id", "")
            state_mod.approve_pending(st, draft_id, our_tweet_id)
            state_mod.save(st, STATE_FILE)
            event_logger.log_event("approve", {
                "draft_id": draft_id,
                "target_author": pending_item["target_author"],
                "our_tweet_id": our_tweet_id,
            }, account="default")
            return {"action": "approve", "draft_id": draft_id,
                    "result": "posted", "our_tweet_id": our_tweet_id}
        except Exception as e:
            event_logger.log_event("error", {
                "draft_id": draft_id,
                "error": str(e),
                "stage": "post_on_approve",
            }, account="default")
            return {"action": "approve", "draft_id": draft_id,
                    "result": f"post failed: {e}"}

    elif action_type == "reject":
        state_mod.reject_pending(st, draft_id)
        state_mod.save(st, STATE_FILE)
        event_logger.log_event("reject", {
            "draft_id": draft_id,
            "target_author": pending_item["target_author"],
        }, account="default")
        return {"action": "reject", "draft_id": draft_id, "result": "rejected"}

    elif action_type == "edit":
        return {"action": "edit", "draft_id": draft_id,
                "result": "manual edit requested — edit in engagement queue then re-approve"}

    elif action_type == "redraft":
        return {"action": "redraft", "draft_id": draft_id,
                "result": "redraft not yet automated — delete pending and re-run engine"}

    return {"action": action_type, "draft_id": draft_id, "result": "unknown action"}


def poll_loop():
    """Continuously poll Telegram for callback queries."""
    print("Starting engagement approval polling...")
    offset = None
    while True:
        updates = get_updates(offset=offset)
        for update in updates:
            offset = update["update_id"] + 1
            callback = update.get("callback_query")
            if not callback:
                continue

            callback_data = callback.get("data", "")
            callback_id = callback["id"]
            message = callback.get("message", {})
            message_id = message.get("message_id")
            chat_id = message.get("chat", {}).get("id")

            print(f"  Processing: {callback_data}")
            result = process_approval(callback_data, message_id, chat_id)
            print(f"  Result: {result}")

            # Acknowledge
            answer_callback(callback_id, text=result["result"])

            # Update message
            if message_id and chat_id:
                pending_item = None
                parts = callback_data.split(":")
                if len(parts) == 3:
                    st = state_mod.load(STATE_FILE)
                    pending_item = state_mod.get_pending(st, parts[2])

                status_emoji = {
                    "approve": "✅",
                    "reject": "❌",
                    "edit": "✏️",
                    "redraft": "🔄",
                }.get(result["action"], "❓")

                author = pending_item["target_author"] if pending_item else "unknown"
                new_text = (
                    f"<b>{status_emoji} {result['action'].upper()}</b>\n\n"
                    f"Author: {author}\n"
                    f"Draft: {parts[2] if len(parts) == 3 else 'unknown'}\n"
                    f"Result: {result['result']}"
                )
                edit_message(chat_id, message_id, new_text)

        if not updates:
            time.sleep(5)


if __name__ == "__main__":
    if "--poll" in sys.argv:
        poll_loop()
    else:
        print("Usage: python3 engagement_approve.py --poll")
        sys.exit(1)
