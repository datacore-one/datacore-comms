"""Autonomous posting mode — guardrail-gated auto-posting with Telegram notification.

When guardrails pass: post immediately, notify Telegram (info only).
When guardrails fail: escalate to Telegram for human approval.
Daily autonomous limit: after N auto-posts, all remaining escalate.
**Autonomous counter persists to SQLite** — survives systemd oneshot restarts.

Space-agnostic: loads config from comms-config.yaml.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import event_logger


class AutonomousPoster:
    def __init__(
        self,
        poster,  # XPoster instance
        notifier,  # object with notify_posted() and escalate_for_approval()
        voice_yaml_path: Optional[str],
        guardrails=None,
        max_autonomous_per_day: int = None,
        state_mod=None,  # engagement_state module for tracking
        state_file: Path = None,
        state_dir: str = None,
        config: dict = None,
    ):
        from comms_config import load_config
        if config is None:
            config = load_config()

        self.poster = poster
        self.notifier = notifier
        self.guardrails = guardrails
        limits = config.get("limits", {})
        self.max_autonomous_per_day = max_autonomous_per_day or limits.get("max_autonomous_per_day", 15)
        self.state_mod = state_mod
        self.state_file = state_file
        self.config = config

        # Persistent autonomous counter
        sp = state_dir or str(
            Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
            / ".datacore" / "state"
        )
        Path(sp).mkdir(parents=True, exist_ok=True)
        self._rate_db = sqlite3.connect(str(Path(sp) / "rate-limits.db"))
        self._rate_db.execute("PRAGMA journal_mode=WAL")
        self._rate_db.execute("""
            CREATE TABLE IF NOT EXISTS autonomous_counts (
                date TEXT PRIMARY KEY, count INTEGER DEFAULT 0
            )
        """)
        self._rate_db.commit()

    def _get_auto_count(self) -> int:
        today = time.strftime('%Y-%m-%d')
        row = self._rate_db.execute(
            "SELECT count FROM autonomous_counts WHERE date = ?", (today,)
        ).fetchone()
        return row[0] if row else 0

    def _increment_auto_count(self):
        today = time.strftime('%Y-%m-%d')
        self._rate_db.execute(
            """INSERT INTO autonomous_counts (date, count)
               VALUES (?, 1)
               ON CONFLICT(date) DO UPDATE SET count = count + 1""",
            (today,),
        )
        self._rate_db.commit()

    def process_draft(
        self,
        draft_text: str,
        target_tweet_id: str,
        target_author: str,
    ) -> dict:
        """Process a draft: auto-post if guardrails pass, else escalate."""
        if self._get_auto_count() >= self.max_autonomous_per_day:
            self.notifier.escalate_for_approval(
                draft_text=draft_text,
                target_tweet_id=target_tweet_id,
                target_author=target_author,
                reason="Daily autonomous limit reached",
            )
            event_logger.log_event("escalate", {
                "reason": "daily_limit",
                "target_author": target_author,
            })
            return {'action': 'escalated', 'reason': 'daily_limit'}

        result = self.guardrails.check(draft_text)
        if not result.passed:
            self.notifier.escalate_for_approval(
                draft_text=draft_text,
                target_tweet_id=target_tweet_id,
                target_author=target_author,
                reason=f"Guardrail violations: {', '.join(result.violations)}",
            )
            event_logger.log_event("escalate", {
                "reason": "guardrail_fail",
                "violations": result.violations,
                "target_author": target_author,
            })
            return {
                'action': 'escalated',
                'reason': 'guardrail_fail',
                'violations': result.violations,
            }

        post_result = self.poster.reply(draft_text, target_tweet_id)
        self._increment_auto_count()
        our_tweet_id = post_result.get('data', {}).get('id', '')

        if self.state_mod and self.state_file:
            st = self.state_mod.load(self.state_file)
            self.state_mod.mark_seen(st, target_tweet_id)
            self.state_mod._bump_stat(st, "posted")
            self.state_mod._bump_stat(st, "auto_posted")
            st.setdefault("posted", []).append({
                "target_tweet_id": target_tweet_id,
                "target_author": target_author,
                "our_tweet_id": our_tweet_id,
                "draft_reply": draft_text,
                "posted_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                "mode": "autonomous",
            })
            self.state_mod.save(st, self.state_file)

        self.notifier.notify_posted(
            draft_text=draft_text,
            target_author=target_author,
            our_tweet_id=our_tweet_id,
        )
        event_logger.log_event("post", {
            "mode": "autonomous",
            "target_author": target_author,
            "our_tweet_id": our_tweet_id,
        })

        return {
            'action': 'posted',
            'tweet_id': our_tweet_id,
        }
