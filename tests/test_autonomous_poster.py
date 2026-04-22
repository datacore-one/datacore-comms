# .datacore/modules/comms/tests/test_autonomous_poster.py
"""Tests for autonomous posting mode."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


def _mock_config():
    return {
        "accounts": {"default": {"env_prefix": "X_", "daily_limit": 50}},
        "limits": {"max_autonomous_per_day": 15},
        "brand": {},
        "discovery": {},
        "voice": {},
        "guardrails": {},
    }


class TestAutonomousPoster:
    def test_passes_guardrails_posts_directly(self, tmp_path):
        from autonomous_poster import AutonomousPoster
        poster = MagicMock()
        poster.reply.return_value = {'data': {'id': '123'}}
        notifier = MagicMock()

        ap = AutonomousPoster(
            poster=poster,
            notifier=notifier,
            voice_yaml_path=None,
            guardrails=MagicMock(check=MagicMock(
                return_value=MagicMock(passed=True, violations=[])
            )),
            state_dir=str(tmp_path),
            config=_mock_config(),
        )
        result = ap.process_draft(
            draft_text="Clean content here",
            target_tweet_id="999",
            target_author="@someone",
        )
        assert result['action'] == 'posted'
        poster.reply.assert_called_once()
        notifier.notify_posted.assert_called_once()

    def test_fails_guardrails_escalates_to_telegram(self, tmp_path):
        from autonomous_poster import AutonomousPoster
        poster = MagicMock()
        notifier = MagicMock()

        ap = AutonomousPoster(
            poster=poster,
            notifier=notifier,
            voice_yaml_path=None,
            guardrails=MagicMock(check=MagicMock(
                return_value=MagicMock(passed=False, violations=["anti-pattern"])
            )),
            state_dir=str(tmp_path),
            config=_mock_config(),
        )
        result = ap.process_draft(
            draft_text="WAGMI friends!",
            target_tweet_id="999",
            target_author="@someone",
        )
        assert result['action'] == 'escalated'
        poster.reply.assert_not_called()
        notifier.escalate_for_approval.assert_called_once()

    def test_tracks_daily_autonomous_count(self, tmp_path):
        from autonomous_poster import AutonomousPoster
        poster = MagicMock()
        poster.reply.return_value = {'data': {'id': '1'}}
        notifier = MagicMock()

        ap = AutonomousPoster(
            poster=poster,
            notifier=notifier,
            voice_yaml_path=None,
            guardrails=MagicMock(check=MagicMock(
                return_value=MagicMock(passed=True, violations=[])
            )),
            max_autonomous_per_day=2,
            state_dir=str(tmp_path),
            config=_mock_config(),
        )
        ap.process_draft("a", "1", "@x")
        ap.process_draft("b", "2", "@y")
        result = ap.process_draft("c", "3", "@z")
        assert result['action'] == 'escalated'
