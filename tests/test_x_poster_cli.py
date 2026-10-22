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


def test_unknown_account_fails():
    import x_poster
    assert x_poster.main(['--account', 'nobody', '--whoami']) != 0
