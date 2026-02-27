#!/usr/bin/env python3
"""Send engagement drafts to Telegram for approval.

Uses Telegram Bot API directly (HTTP) — no python-telegram-bot dependency.
Sends inline keyboard with Approve/Reject buttons.
"""

import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def send_draft_for_approval(
    draft_id: str,
    target_author: str,
    target_content: str,
    target_url: str,
    draft_reply: str,
    bot_token: str = None,
    chat_id: str = None,
) -> int:
    """Send a draft reply to Telegram with inline approval buttons.

    Returns: Telegram message_id (for later editing on approve/reject)
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        raise ValueError("TELEGRAM_BOT_TOKEN and ENGAGEMENT_CHAT_ID required")

    # Format message
    snippet = target_content[:200]
    if len(target_content) > 200:
        snippet += "..."

    text = (
        f"<b>Engagement Draft</b>\n\n"
        f"<b>{_escape_html(target_author)}</b>\n"
        f"<i>{_escape_html(snippet)}</i>\n\n"
        f"<a href=\"{target_url}\">View original</a>\n\n"
        f"<b>Draft reply:</b>\n"
        f"{_escape_html(draft_reply)}\n\n"
        f"<code>{len(draft_reply)} chars</code>"
    )

    # Inline keyboard
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✓ Approve", "callback_data": f"eng:approve:{draft_id}"},
                {"text": "✎ Edit", "callback_data": f"eng:edit:{draft_id}"},
            ],
            [
                {"text": "↺ Redraft", "callback_data": f"eng:redraft:{draft_id}"},
                {"text": "✗ Reject", "callback_data": f"eng:reject:{draft_id}"},
            ],
        ]
    }

    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard,
        "disable_web_page_preview": True,
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return result["result"]["message_id"]
            raise Exception(f"Telegram error: {result}")
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"Telegram API error {e.code}: {error_body}")


def edit_message(message_id: int, new_text: str, bot_token: str = None, chat_id: str = None):
    """Edit a Telegram message (used after approve/reject)."""
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.environ.get("ENGAGEMENT_CHAT_ID")

    payload = {
        "chat_id": cid,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "HTML",
    }

    url = f"https://api.telegram.org/bot{token}/editMessageText"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError:
        pass  # Best effort


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
