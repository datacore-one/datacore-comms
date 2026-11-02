"""Tests for youtube_upload.py — the guards, not Google.

What must hold whatever Google does:
- an upload is private unless the caller passes --approved (the owner's yes);
- metadata YouTube would reject (angle brackets, over-long title, tag budget)
  is refused before any bytes leave the machine;
- a token from the wrong channel is refused (brand-account picker mistakes);
- no refresh token is ever printed, logged or put on a command line.
"""
import json
import subprocess

import pytest

import youtube_upload as yt


# ---------------------------------------------------------------- privacy

def test_default_privacy_is_private():
    body = yt.build_body({"title": "T", "description": "D"}, privacy=None, approved=None)
    assert body["status"]["privacyStatus"] == "private"


@pytest.mark.parametrize("privacy", ["public", "unlisted"])
def test_non_private_needs_owner_yes(privacy):
    with pytest.raises(yt.Refused, match="owner"):
        yt.build_body({"title": "T", "description": "D"}, privacy=privacy, approved=None)


def test_non_private_with_owner_yes_is_allowed():
    body = yt.build_body({"title": "T", "description": "D"}, privacy="public",
                         approved="owner yes in chat 2026-11-02")
    assert body["status"]["privacyStatus"] == "public"


def test_not_made_for_kids_and_category_default():
    body = yt.build_body({"title": "T", "description": "D"}, privacy=None, approved=None)
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"]["categoryId"] == "28"  # Science & Technology


# ---------------------------------------------------------------- metadata

def test_angle_brackets_refused():
    # `plur verify --manifest <reference>` is how the claim sheet writes it;
    # YouTube rejects < and > in titles and descriptions.
    with pytest.raises(yt.Refused, match="angle"):
        yt.build_body({"title": "T", "description": "plur verify --manifest <reference>"},
                      privacy=None, approved=None)


def test_title_too_long_refused():
    with pytest.raises(yt.Refused, match="100"):
        yt.build_body({"title": "x" * 101, "description": "D"}, privacy=None, approved=None)


def test_tag_budget_refused():
    tags = ["a" * 60] * 10  # 600 chars > 500
    with pytest.raises(yt.Refused, match="tags"):
        yt.build_body({"title": "T", "description": "D", "tags": tags},
                      privacy=None, approved=None)


def test_meta_file_loads(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("title: Hello\ndescription: |\n  line one\n  line two\ntags: [plur, memory]\n")
    meta = yt.load_meta(p)
    assert meta["title"] == "Hello"
    assert meta["description"] == "line one\nline two\n"
    assert meta["tags"] == ["plur", "memory"]


# ---------------------------------------------------------------- channel guard

class _FakeChannels:
    def __init__(self, cid, title):
        self._r = {"items": [{"id": cid, "snippet": {"title": title}}]}

    def list(self, **_):
        return self

    def execute(self):
        return self._r


class _FakeService:
    def __init__(self, cid, title):
        self._c = _FakeChannels(cid, title)

    def channels(self):
        return self._c


def test_wrong_channel_refused():
    svc = _FakeService("UCother", "Someone else")
    with pytest.raises(yt.Refused, match="UCexpected"):
        yt.check_channel(svc, "plur", {"plur": {"channel_id": "UCexpected"}})


def test_right_channel_passes():
    svc = _FakeService("UCexpected", "PLUR")
    cid, title = yt.check_channel(svc, "plur", {"plur": {"channel_id": "UCexpected"}})
    assert cid == "UCexpected" and title == "PLUR"


def test_unconfigured_channel_refused():
    svc = _FakeService("UCx", "X")
    with pytest.raises(yt.Refused, match="channel_id"):
        yt.check_channel(svc, "plur", {})


# ---------------------------------------------------------------- secrets

SECRET = "1//0gFAKE-refresh-token-value-xyz"


def test_read_credential_keeps_value_off_argv_and_output(monkeypatch, capsys):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        assert kw.get("capture_output") is True
        return subprocess.CompletedProcess(argv, 0, stdout=SECRET + "\n", stderr="")

    monkeypatch.setattr(yt.subprocess, "run", fake_run)
    assert yt.read_credential("youtube-refresh-plur") == SECRET
    assert SECRET not in " ".join(seen["argv"])
    assert seen["argv"][:2] == ["creds", "get"]
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_read_credential_missing_raises_without_value(monkeypatch):
    monkeypatch.setattr(yt.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, stdout="", stderr="not found"))
    with pytest.raises(yt.Refused, match="creds add"):
        yt.read_credential("youtube-refresh-plur")


def test_save_refresh_token_never_prints_it(monkeypatch, capsys):
    calls = []

    class FakeMgr:
        def __init__(self, data_dir=None):
            pass

        def _load_index(self):
            return None

        def cmd_add(self, cred_id, **kw):
            calls.append((cred_id, kw))
            print("Added YOUTUBE_REFRESH_TOKEN_PLUR to global.env")  # what the real one prints
            return 0

    monkeypatch.setattr(yt, "_credential_manager", lambda: FakeMgr())
    monkeypatch.setattr(yt, "credential_exists", lambda cid: False)
    yt.save_refresh_token("plur", SECRET)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    cred_id, kw = calls[0]
    assert cred_id == "youtube-refresh-plur"
    assert kw["value"] == SECRET and kw["var_name"] == "YOUTUBE_REFRESH_TOKEN_PLUR"
    assert kw["overwrite"] is False


def test_save_refresh_token_overwrites_existing(monkeypatch):
    calls = []

    class FakeMgr:
        def cmd_add(self, cred_id, **kw):
            calls.append((cred_id, kw))
            return 0

    monkeypatch.setattr(yt, "_credential_manager", lambda: FakeMgr())
    monkeypatch.setattr(yt, "credential_exists", lambda cid: True)
    yt.save_refresh_token("datafund", SECRET)
    assert calls[0][0] == "youtube-refresh-datafund"
    assert calls[0][1]["overwrite"] is True


class _FailingPushMgr:
    """cmd_add stored the value but its sync/push step failed (exit 1)."""

    def cmd_add(self, cred_id, **kw):
        return 1


def test_save_refresh_token_stored_but_not_pushed_is_a_warning(monkeypatch, capsys):
    # 2026-11-02: the token was stored and served on this host, but the push to
    # other hosts failed; the tool said "refused ... nothing else was changed".
    monkeypatch.setattr(yt, "_credential_manager", lambda: _FailingPushMgr())
    monkeypatch.setattr(yt, "credential_exists", lambda cid: False)
    monkeypatch.setattr(yt, "read_credential", lambda cid: SECRET)
    yt.save_refresh_token("plur", SECRET)  # must not raise
    out = capsys.readouterr()
    assert "other hosts" in out.err and SECRET not in out.out + out.err


def test_save_refresh_token_not_served_still_refused(monkeypatch):
    monkeypatch.setattr(yt, "_credential_manager", lambda: _FailingPushMgr())
    monkeypatch.setattr(yt, "credential_exists", lambda cid: False)

    def not_served(cid):
        raise yt.Refused("not served")

    monkeypatch.setattr(yt, "read_credential", not_served)
    with pytest.raises(yt.Refused, match="refused to store"):
        yt.save_refresh_token("plur", SECRET)


def test_save_refresh_token_old_value_served_still_refused(monkeypatch):
    monkeypatch.setattr(yt, "_credential_manager", lambda: _FailingPushMgr())
    monkeypatch.setattr(yt, "credential_exists", lambda cid: True)
    monkeypatch.setattr(yt, "read_credential", lambda cid: "an-older-token")
    with pytest.raises(yt.Refused, match="refused to store"):
        yt.save_refresh_token("plur", SECRET)


def test_upload_log_has_no_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(yt, "_log_path", lambda: tmp_path / "uploads.jsonl")
    yt.log_event({"action": "upload", "video_id": "abc", "channel": "plur"})
    line = json.loads((tmp_path / "uploads.jsonl").read_text().strip())
    assert line["video_id"] == "abc" and "ts" in line


def test_delete_needs_repeated_id():
    with pytest.raises(yt.Refused, match="confirm"):
        yt.check_delete_confirmation("abc123", "abc12")
    yt.check_delete_confirmation("abc123", "abc123")


# ---------------------------------------------------------------- update

def test_update_needs_owner_yes():
    with pytest.raises(yt.Refused, match="owner"):
        yt.build_update_body("vid1", {"title": "T", "description": "D"}, approved=None)


def test_update_changes_text_only_never_privacy():
    body = yt.build_update_body("vid1", {"title": "T", "description": "D", "tags": ["a"]},
                                approved="owner yes in chat 2026-11-02")
    assert body["id"] == "vid1"
    assert body["snippet"]["description"] == "D"
    assert body["snippet"]["tags"] == ["a"]
    assert "status" not in body


def test_update_applies_same_metadata_guards():
    with pytest.raises(yt.Refused, match="angle"):
        yt.build_update_body("vid1", {"title": "T", "description": "<x>"}, approved="yes")
