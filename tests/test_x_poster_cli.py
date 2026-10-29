"""Command line for x_poster.py, so a post runs as the script itself.

The agent guard recognises a post by the script name, and the owner's standing
grant for Data names that script (approvals_policy.local.yaml). Importing the
class from inline Python posted without the guard seeing it (2026-10-22).
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

ENV = {'DATAFUND_X_API_KEY': 'k', 'DATAFUND_X_API_SECRET': 's',
       'DATAFUND_X_ACCESS_TOKEN': 't', 'DATAFUND_X_ACCESS_TOKEN_SECRET': 'ts'}


def test_whoami_prints_the_handle(capsys):
    import x_poster
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get', return_value={'data': {'username': 'DataFundProject', 'id': '9'}}):
        assert x_poster.main(['--account', 'datafund', '--whoami']) == 0
    assert '@DataFundProject' in capsys.readouterr().out


def test_post_file_posts_its_text_and_prints_the_url(tmp_path, capsys):
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("Hello world.\n")
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get', return_value={'data': {'username': 'DataFundProject', 'id': '9'}}), \
         patch.object(x_poster.XPoster, 'post', return_value={'data': {'id': '123', 'text': 'Hello world.'}}) as post:
        assert x_poster.main(['--account', 'datafund', '--post-file', str(f),
                              '--state-dir', str(tmp_path)]) == 0
    assert post.call_args[0][0] == "Hello world."
    assert 'https://x.com/DataFundProject/status/123' in capsys.readouterr().out


def test_empty_post_file_is_refused(tmp_path):
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("  \n")
    with patch.dict(os.environ, ENV), patch.object(x_poster.XPoster, 'post') as post:
        assert x_poster.main(['--account', 'datafund', '--post-file', str(f),
                              '--state-dir', str(tmp_path)]) != 0
    post.assert_not_called()


def test_missing_values_come_from_the_broker(monkeypatch, capsys):
    import x_poster
    for v in ENV:
        monkeypatch.delenv(v, raising=False)
    asked = []

    def fake_run(cmd, **kw):
        asked.append(cmd)
        class R:
            returncode = 0
            stdout = "value\n"
        return R()
    monkeypatch.setattr(x_poster.subprocess, "run", fake_run)
    with patch.object(x_poster.XPoster, '_oauth_get', return_value={'data': {'username': 'DataFundProject', 'id': '9'}}):
        assert x_poster.main(['--account', 'datafund', '--whoami']) == 0
    assert sorted(c[2] for c in asked) == sorted(ENV)
    assert all(c[0] == "creds" and c[1] == "get" and "--consumer" in c for c in asked)
    assert "value" not in capsys.readouterr().out


def test_media_is_uploaded_and_attached(tmp_path, capsys):
    # A post with an image card (2026-10-26): the order's P2-P7 carry one, and
    # the owner's grant covers posting only through this script.
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("Card post.\n")
    card = tmp_path / "card-3.png"
    card.write_bytes(b"\x89PNG fixture")
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get', return_value={'data': {'username': 'DataFundProject', 'id': '9'}}), \
         patch.object(x_poster.XPoster, 'upload_media', return_value='777') as up, \
         patch.object(x_poster.XPoster, 'post_with_media', return_value={'data': {'id': '124'}}) as pwm, \
         patch.object(x_poster.XPoster, 'post') as plain:
        assert x_poster.main(['--account', 'datafund', '--post-file', str(f), '--media', str(card),
                              '--state-dir', str(tmp_path)]) == 0
    assert up.call_args[0][0] == str(card)
    assert pwm.call_args[0] == ("Card post.", ['777'])
    plain.assert_not_called()
    assert 'https://x.com/DataFundProject/status/124' in capsys.readouterr().out


def test_a_missing_media_file_posts_nothing(tmp_path):
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("Card post.\n")
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, 'upload_media') as up, \
         patch.object(x_poster.XPoster, 'post_with_media') as pwm:
        assert x_poster.main(['--account', 'datafund', '--post-file', str(f),
                              '--media', str(tmp_path / "absent.png"), '--state-dir', str(tmp_path)]) != 0
    up.assert_not_called()
    pwm.assert_not_called()


def test_a_media_post_has_its_links_verified(tmp_path):
    import x_poster
    poster = x_poster.XPoster.__new__(x_poster.XPoster)
    calls = []
    poster._refuse_claims = lambda t: calls.append("claims")
    poster._verify_text_links = lambda t, skip_verification=False: calls.append("links")
    poster._check_rate_limit = lambda: None
    poster._increment_rate_count = lambda: None
    poster._oauth_post = lambda url, payload: {'data': {'id': '1'}}
    poster.account = 'datafund'
    x_poster.XPoster.post_with_media.__wrapped__(poster, "text https://example.org", ['1']) \
        if hasattr(x_poster.XPoster.post_with_media, '__wrapped__') \
        else x_poster.XPoster.post_with_media(poster, "text https://example.org", ['1'])
    assert calls == ["claims", "links"]


def test_unknown_account_fails():
    import x_poster
    assert x_poster.main(['--account', 'nobody', '--whoami']) != 0


# --- Duplicate guard and delete (2026-10-29) ---
# A ledger retry posted P7 three times: the task's check failed after each
# post, and each retry posted again. The poster now refuses a post whose text
# the account already published, so a retry cannot double-post.

ME = {'data': {'username': 'DataFundProject', 'id': '9'}}


def _recent(*texts):
    return {'data': [{'id': str(100 + i), 'text': t} for i, t in enumerate(texts)]}


def _gets(recent):
    return lambda url, *a, **k: recent if '/tweets' in url else ME


def test_a_post_the_account_already_published_is_refused(tmp_path, capsys):
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("Try it: dataprovenance.app\n\nPre-release. https://datafund.io/blog/x/\n")
    live = "Try it: https://t.co/a\n\nPre-release. https://t.co/b https://t.co/c"
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get', side_effect=_gets(_recent("other", live))), \
         patch.object(x_poster.XPoster, 'post') as post, \
         patch.object(x_poster.XPoster, 'post_with_media') as pwm:
        rc = x_poster.main(['--account', 'datafund', '--post-file', str(f), '--state-dir', str(tmp_path)])
    assert rc == 3
    post.assert_not_called()
    pwm.assert_not_called()
    assert 'already posted: https://x.com/DataFundProject/status/101' in capsys.readouterr().out


def test_a_new_post_still_goes_out_when_recent_posts_differ(tmp_path):
    import x_poster
    f = tmp_path / "post.txt"
    f.write_text("Something new.\n")
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get', side_effect=_gets(_recent("Something old."))), \
         patch.object(x_poster.XPoster, 'post', return_value={'data': {'id': '5'}}) as post:
        assert x_poster.main(['--account', 'datafund', '--post-file', str(f), '--state-dir', str(tmp_path)]) == 0
    post.assert_called_once()


def test_delete_removes_the_accounts_own_post(tmp_path, capsys):
    import x_poster
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get',
                      side_effect=lambda url, *a, **k: {'data': {'author_id': '9'}} if '/tweets/77' in url else ME), \
         patch.object(x_poster.XPoster, '_oauth_delete', return_value={'data': {'deleted': True}}) as d:
        assert x_poster.main(['--account', 'datafund', '--delete', '77', '--state-dir', str(tmp_path)]) == 0
    assert d.call_args[0][0] == "https://api.x.com/2/tweets/77"
    assert 'deleted: 77' in capsys.readouterr().out


def test_delete_refuses_a_post_by_another_account(tmp_path):
    import x_poster
    with patch.dict(os.environ, ENV), \
         patch.object(x_poster.XPoster, '_oauth_get',
                      side_effect=lambda url, *a, **k: {'data': {'author_id': '1'}} if '/tweets/77' in url else ME), \
         patch.object(x_poster.XPoster, '_oauth_delete') as d:
        assert x_poster.main(['--account', 'datafund', '--delete', '77', '--state-dir', str(tmp_path)]) != 0
    d.assert_not_called()
