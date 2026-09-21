#!/usr/bin/env python3
"""Unified draft pipeline: evaluate → register in state → send to Telegram.

Single entry point for both engagement_engine.py and Chrome-based agents.
Ensures drafts are always registered in state before Telegram callbacks arrive.

Usage:
    from draft_pipeline import process_draft_pipeline

    result = process_draft_pipeline(
        draft_reply="The real fix is architecture.",
        target_author="@someone",
        target_content="Privacy is hard...",
        target_url="https://x.com/someone/status/123",
        target_tweet_id="123",
    )

    # result = {
    #     'action': 'posted' | 'sent_to_telegram' | 'auto_rejected' | 'error',
    #     'draft_id': 'a1b2c3d4',
    #     'evaluation': DraftEvaluation,
    #     'message_id': 12345,  # Telegram message ID (if sent)
    # }
"""

import os
import sys
from pathlib import Path

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"

# Load env vars so Telegram + server sync work when called from Chrome agent
try:
    sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))
    from env_utils import load_env_files
    load_env_files()
except Exception:
    pass


def process_draft_pipeline(
    draft_reply: str,
    target_author: str,
    target_content: str,
    target_url: str,
    target_tweet_id: str = "unknown",
    evaluate: bool = True,
    source: str = "engine",
    account: str = "fds",
) -> dict:
    """Full draft pipeline: evaluate → register → send to Telegram.

    Args:
        draft_reply: The draft tweet text
        target_author: Who we're replying to
        target_content: The tweet content being replied to
        target_url: URL to the target tweet
        target_tweet_id: Tweet ID (may be 'unknown' for Chrome-sourced)
        evaluate: Whether to run evaluators (default True)
        source: Origin label — 'engine', 'chrome', etc.

    Returns:
        dict with action, draft_id, evaluation, message_id
    """
    import engagement_state as state_mod
    import engagement_notify as notify_mod

    result = {
        'action': 'error',
        'draft_id': None,
        'evaluation': None,
        'message_id': None,
        'source': source,
    }

    # Phase 0: Guard fake/test tweet IDs and check for duplicates
    is_real_tweet_id = (
        target_tweet_id
        and target_tweet_id not in ('unknown', '', '12345')
        and target_tweet_id.isdigit()
        and len(target_tweet_id) >= 10
    )

    if is_real_tweet_id:
        # Dedup: skip if we already have a pending draft for this tweet
        st_check, _ = state_mod.load(STATE_FILE)
        if state_mod.has_pending_for_tweet(st_check, target_tweet_id):
            print(f"  SKIPPED: Already have pending draft for tweet {target_tweet_id}")
            result['action'] = 'duplicate_skipped'
            return result

        # Check reply permissions
        try:
            from x_poster import XPoster
            user_id_key = f'{account.upper()}_X_USER_ID' if account != 'fds' else 'FDS_X_USER_ID'
            checker = XPoster(account=account, user_id=os.environ.get(user_id_key))
            if not checker.can_reply(target_tweet_id):
                print(f"  SKIPPED: Replies restricted on tweet {target_tweet_id}")
                result['action'] = 'replies_restricted'
                return result
        except Exception:
            pass  # Fail open — 403 will be caught at post time
    elif not is_real_tweet_id:
        print(f"  SKIPPED: Invalid tweet ID '{target_tweet_id}' — must be a real numeric ID (10+ digits)")
        result['action'] = 'invalid_id_skipped'
        return result

    # Phase 1: Evaluate (if enabled)
    if evaluate:
        try:
            from draft_evaluator import evaluate_draft
            evaluation = evaluate_draft(
                draft_reply=draft_reply,
                target_author=target_author,
                target_content=target_content,
            )
            result['evaluation'] = evaluation
            print(f"  Evaluation: {evaluation.summary_line}")

            if evaluation.decision == 'rejected':
                print(f"  AUTO-REJECTED by evaluators (consensus {evaluation.consensus:.0%})")
                print(evaluation.feedback_block)
                result['action'] = 'auto_rejected'
                # Still register in state for tracking
                st, baseline = state_mod.load(STATE_FILE)
                state_mod.mark_seen(st, target_tweet_id)
                state_mod._bump_stat(st, "eval_rejected")
                state_mod.save(st, STATE_FILE, baseline=baseline)
                return result
        except Exception as e:
            print(f"  Evaluation failed ({e})")
            # Engine-sourced drafts: auto-reject on eval failure (fail-safe)
            # Chrome-sourced drafts: still go to Telegram for human review
            if source == "engine":
                print(f"  AUTO-REJECTED (eval exception, engine mode)")
                result['action'] = 'auto_rejected'
                st, baseline = state_mod.load(STATE_FILE)
                state_mod.mark_seen(st, target_tweet_id)
                state_mod._bump_stat(st, "eval_error")
                state_mod.save(st, STATE_FILE, baseline=baseline)
                state_mod.log_error("draft_pipeline", "eval_exception", str(e))
                return result
            result['evaluation'] = None

    # Phase 2: For engine source with passing evaluation — post directly, skip pending state
    AUTONOMOUS_THRESHOLD = 0.75
    from datetime import datetime, timezone, timedelta
    import uuid as _uuid

    if source == 'engine' and result.get('evaluation') and result['evaluation'].consensus >= AUTONOMOUS_THRESHOLD:
        draft_id = _uuid.uuid4().hex[:8]
        result['draft_id'] = draft_id
        try:
            from x_poster import XPoster
            poster = XPoster(account=account)
            post_result = poster.reply(text=draft_reply, reply_to_id=str(target_tweet_id))
            our_tweet_id = post_result.get("data", {}).get("id", "unknown")
            print(f"  AUTO-POSTED: {our_tweet_id}")

            # Register directly as posted — never touches pending
            st, baseline = state_mod.load(STATE_FILE)
            now = datetime.now(timezone.utc)
            st.setdefault("posted", []).append({
                "id": draft_id,
                "target_tweet_id": target_tweet_id,
                "target_author": target_author,
                "target_content": target_content[:280],
                "target_url": target_url,
                "draft_reply": draft_reply,
                "our_tweet_id": our_tweet_id,
                "posted_at": now.isoformat(),
                "analyze_at": (now + timedelta(hours=24)).isoformat(),
                "analyzed": False,
                "mode": "autonomous",
                "source": source,
                "eval_consensus": result['evaluation'].consensus,
            })
            state_mod.mark_seen(st, target_tweet_id)
            state_mod._bump_stat(st, "posted")
            state_mod.save(st, STATE_FILE, baseline=baseline)

            result['action'] = 'posted'
            result['our_tweet_id'] = our_tweet_id
            return result
        except Exception as e:
            print(f"  Auto-post failed: {e}")
            result['action'] = 'error'
            return result

    # Phase 2b: Register in pending state (for Telegram approval flow)
    try:
        st, baseline = state_mod.load(STATE_FILE)
        draft_id = state_mod.add_pending(
            st,
            target_tweet_id=target_tweet_id,
            target_author=target_author,
            target_content=target_content,
            target_url=target_url,
            draft_reply=draft_reply,
            account=account,
        )
        result['draft_id'] = draft_id
        if target_tweet_id and target_tweet_id not in ('unknown', ''):
            state_mod.mark_seen(st, target_tweet_id)
        state_mod.save(st, STATE_FILE, baseline=baseline)
    except Exception as e:
        print(f"  State registration failed: {e}")
        result['action'] = 'error'
        return result

    # Phase 3: Send to Telegram for approval (borderline consensus or chrome source)
    try:
        eval_context = ""
        if result.get('evaluation'):
            ev = result['evaluation']
            eval_context = f"\n\n<b>Evaluators ({ev.consensus:.0%}):</b>\n"
            for r in ev.results:
                icon = "+" if r.score >= 0.7 else "-"
                eval_context += f"  {icon} {r.evaluator}: {r.score:.0%} — {r.feedback[:60]}\n"

        msg_id = notify_mod.send_draft_for_approval(
            draft_id=draft_id,
            target_author=target_author,
            target_content=target_content,
            target_url=target_url,
            draft_reply=draft_reply,
        )

        # Update pending with telegram message ID
        st, baseline = state_mod.load(STATE_FILE)
        pending_item = state_mod.get_pending(st, draft_id)
        if pending_item:
            pending_item["telegram_message_id"] = msg_id
            pending_item["source"] = source
            if result.get('evaluation'):
                pending_item["eval_consensus"] = result['evaluation'].consensus
                pending_item["eval_decision"] = result['evaluation'].decision
        state_mod.save(st, STATE_FILE, baseline=baseline)

        result['message_id'] = msg_id
        result['action'] = 'sent_to_telegram'
        print(f"  Sent to Telegram (draft {draft_id}, msg {msg_id})")

        # Send evaluation details as a follow-up message if available
        if eval_context:
            _send_eval_followup(msg_id, eval_context)

    except Exception as e:
        print(f"  Telegram send failed: {e}")
        # Draft is still registered in state — can be reviewed via MCP tools
        result['action'] = 'registered_no_telegram'

    # Always sync chrome drafts to server, regardless of TG send outcome
    # Server sync must happen so Telegram bot callbacks can find the draft
    if source == 'chrome':
        st, _ = state_mod.load(STATE_FILE)
        _sync_draft_to_server(st, draft_id)

    return result


def _send_eval_followup(reply_to_msg_id: int, eval_text: str):
    """Send evaluation details as a reply to the draft message."""
    import json
    from urllib.request import Request, urlopen

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        return

    payload = {
        "chat_id": cid,
        "text": eval_text.strip(),
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_msg_id,
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=10) as resp:
            pass
    except Exception:
        pass  # Best effort


def _sync_draft_to_server(state: dict, draft_id: str):
    """Sync a single draft to the nightshift server's engagement state.

    Pipes JSON via stdin to avoid shell escaping issues with quotes in content.
    """
    import subprocess
    import json as _json

    pending = None
    for p in state.get("pending", []):
        if p["id"] == draft_id:
            pending = p
            break
    if not pending:
        return

    entry_json = _json.dumps(pending)

    # Pipe the full script to python3 via SSH stdin.
    # The JSON entry is embedded as a raw string assignment at the top.
    remote_script = (
        "import json, fcntl, sys\n"
        "from pathlib import Path\n"
        f"entry = json.loads({entry_json!r})\n"
        "STATE_FILE = Path.home() / 'Data/.datacore/state/engagement-state.json'\n"
        "LOCK_FILE = STATE_FILE.with_suffix('.lock')\n"
        "STATE_FILE.parent.mkdir(parents=True, exist_ok=True)\n"
        "lf = open(LOCK_FILE, 'w')\n"
        "fcntl.flock(lf, fcntl.LOCK_EX)\n"
        "try:\n"
        "    st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {'pending': []}\n"
        "    if not any(p.get('id') == entry['id'] for p in st.get('pending', [])):\n"
        "        st.setdefault('pending', []).append(entry)\n"
        "        STATE_FILE.write_text(json.dumps(st, indent=2))\n"
        "        print('Synced draft ' + entry['id'] + ' to server')\n"
        "    else:\n"
        "        print('Draft ' + entry['id'] + ' already on server')\n"
        "finally:\n"
        "    fcntl.flock(lf, fcntl.LOCK_UN)\n"
        "    lf.close()\n"
    )

    try:
        result = subprocess.run(
            ["ssh", "nightshift", "python3"],
            input=remote_script, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"  Server sync: {result.stdout.strip()}")
        else:
            print(f"  Server sync failed: {result.stderr.strip()}")
    except Exception as e:
        print(f"  Server sync error: {e}")


if __name__ == "__main__":
    """Quick test: evaluate and send a sample draft."""
    from env_utils import load_env_files
    load_env_files()

    result = process_draft_pipeline(
        draft_reply="Good stack. Missing one layer: sovereign storage. Where does the data live after compute?",
        target_author="@maxdesalle",
        target_content="The sovereign stack: @zcash for storing value, @nym for connectivity, @arcium for compute",
        target_url="https://x.com/maxdesalle/status/12345",
        target_tweet_id="12345",
        source="test",
    )
    print(f"\nResult: {result['action']} (draft {result['draft_id']})")
