"""Integration tests — verify components wire together correctly."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


def _mock_config():
    return {
        "accounts": {
            "fds": {"env_prefix": "FDS_X_", "daily_limit": 50},
            "default": {"env_prefix": "X_", "daily_limit": 50},
        },
        "limits": {"max_autonomous_per_day": 15},
        "brand": {"handle": "@test"},
        "discovery": {},
        "voice": {},
        "guardrails": {
            "anti_patterns": ["WAGMI", "moon"],
            "max_exclamations": 0,
        },
        "model": {},
    }


class TestEndToEndAutonomous:
    def test_full_autonomous_pipeline(self, tmp_path):
        import engagement_state as state_mod
        from autonomous_poster import AutonomousPoster
        from guardrails import ContentGuardrails
        from x_poster import XPoster

        state_file = tmp_path / "state.json"
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }

        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', state_dir=str(tmp_path),
                           config=_mock_config())

            guardrails = ContentGuardrails(
                anti_patterns=["WAGMI", "moon"],
                max_length=280,
                max_exclamations=0,
            )

            notifier = MagicMock()
            ap = AutonomousPoster(
                poster=poster, notifier=notifier,
                voice_yaml_path=None, guardrails=guardrails,
                state_mod=state_mod, state_file=state_file,
                state_dir=str(tmp_path),
                config=_mock_config(),
            )

            with patch.object(poster, '_oauth_post',
                            return_value={'data': {'id': 'tweet_001'}}):
                result = ap.process_draft(
                    "Privacy by architecture, not by promise.",
                    "target_1", "@someone",
                )
            assert result['action'] == 'posted'

            result = ap.process_draft(
                "WAGMI friends!", "target_2", "@other",
            )
            assert result['action'] == 'escalated'

            st = state_mod.load(state_file)
            assert len(st.get('posted', [])) == 1

    def test_rate_limit_blocks_excess_posts(self, tmp_path):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        import pytest
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', daily_limit=2,
                           state_dir=str(tmp_path),
                           config=_mock_config())
            with patch.object(poster, '_oauth_post',
                            return_value={'data': {'id': '1'}}):
                poster.post("first")
                poster.post("second")
                with pytest.raises(RuntimeError, match="rate limit"):
                    poster.post("third")

    def test_follow_pipeline(self, tmp_path):
        from follow_db import FollowDB
        from follow_manager import FollowManager

        db = FollowDB(tmp_path / "test.db")
        for i in range(5):
            db.add_account(
                user_id=str(i), username=f"user{i}",
                cluster="dev" if i < 3 else "privacy",
                source_anchor="@EFF", overlap_count=1,
                followers=1000, following=500,
            )

        poster = MagicMock()
        poster.follow.return_value = {'data': {'following': True}}

        fm = FollowManager(db=db, poster=poster, daily_budget=3)
        result = fm.execute_daily_follows()
        assert result['followed'] == 3
        assert poster.follow.call_count == 3

        stats = db.stats()
        assert stats['by_status'].get('followed', 0) == 3


class TestDraftReply:
    """Test OpenRouter-based draft generation."""

    def test_draft_reply_returns_structured_result(self):
        from engagement_draft import draft_reply

        with patch("engagement_draft._call_openrouter") as mock_call:
            mock_call.return_value = {
                "choices": [{
                    "message": {"content": "This is a test reply."},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            }

            result = draft_reply(
                {"author": "@test", "content": "Hello world", "url": "https://x.com/test/1"},
                config=_mock_config(),
            )

            assert result["text"] == "This is a test reply."
            assert result["tokens_used"] == 120
            assert result["prompt_version"] == "default"
            assert result["finish_reason"] == "stop"

    def test_draft_reply_uses_fallback_on_failure(self):
        from engagement_draft import draft_reply

        with patch("engagement_draft._call_openrouter") as mock_call:
            # Primary fails, fallback succeeds
            def side_effect(*args, **kwargs):
                if kwargs.get("model") == "anthropic/claude-sonnet-4":
                    raise Exception("Primary model error")
                return {
                    "choices": [{
                        "message": {"content": "Fallback reply."},
                        "finish_reason": "stop",
                    }],
                    "usage": {"total_tokens": 80},
                }

            mock_call.side_effect = side_effect

            result = draft_reply(
                {"author": "@test", "content": "Hello", "url": ""},
                config=_mock_config(),
            )
            assert result["text"] == "Fallback reply."
            assert result["model"] == "google/gemini-2.5-flash-preview"
