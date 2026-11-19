"""Reposting a brand post from another brand account (owner asked, 18 Nov 2026)."""
import pytest

import x_poster


def _poster(calls):
    p = x_poster.XPoster.__new__(x_poster.XPoster)
    p._oauth_post = lambda url, payload: calls.append((url, payload)) or {"data": {"retweeted": True}}
    return p


def test_repost_calls_the_retweet_endpoint_for_this_account():
    calls = []
    assert _poster(calls).repost("123", user_id="42") == {"data": {"retweeted": True}}
    assert calls == [("https://api.x.com/2/users/42/retweets", {"tweet_id": "123"})]


def test_repost_obeys_the_posting_kill_switch(tmp_path, monkeypatch):
    switch = tmp_path / "kill"
    switch.write_text("halted")
    monkeypatch.setattr(x_poster, "KILL_SWITCH_PATH", switch)
    calls = []
    with pytest.raises(x_poster.KillSwitchActive):
        _poster(calls).repost("123", user_id="42")
    assert calls == []
