"""devto_post: the guards and the conversion, not dev.to."""
import pytest

import devto_post as dv


def test_posting_needs_owner_yes(tmp_path, capsys):
    f = tmp_path / "a.md"; f.write_text('---\ntitle: T\ndescription: D\n---\nBody')
    rc = dv.main(["post", "--account", "datafund", "--file", str(f), "--canonical", "https://datafund.io/x/",
                  "--site", "https://datafund.io", "--tags", "ai"])
    assert rc == 2 and "owner" in capsys.readouterr().err


def test_unknown_account_is_refused():
    with pytest.raises(dv.Refused, match="account"):
        dv.credential_id("nobody")


def test_each_account_has_its_own_key():
    assert dv.credential_id("plur") == "devto-api-key"
    assert dv.credential_id("datafund") == "devto-api-key-datafund"


def test_relative_links_and_images_become_absolute_but_not_in_code():
    out = dv.to_markdown('See [a](/b/) ![c](/d.png)\n\n```\n[x](/y)\n```', "https://datafund.io")
    assert "[a](https://datafund.io/b/)" in out and "(https://datafund.io/d.png)" in out and "[x](/y)" in out


def test_tweet_and_youtube_become_liquid_tags():
    body = ('<blockquote class="twitter-tweet"><a href="https://x.com/O/status/123">x</a></blockquote>\n'
            '<div><iframe src="https://www.youtube-nocookie.com/embed/abc_1" title="t"></iframe></div>')
    out = dv.to_markdown(body, "https://plur.ai")
    assert "{% twitter 123 %}" in out and "{% youtube abc_1 %}" in out


def test_leftover_html_is_refused():
    with pytest.raises(dv.Refused, match="<video"):
        dv.to_markdown('<video src="/x.mp4"></video>', "https://plur.ai")


def test_unverifiable_claim_is_refused():
    with pytest.raises(Exception) as exc:
        dv.check_claims("PLUR is in production at a national ministry.")
    assert exc.type.__name__ == "ClaimNotVerified"


def test_duplicate_is_found_by_canonical():
    arts = [{"canonical_url": "https://a/", "url": "https://dev.to/x"}]
    assert dv.existing(arts, "https://a/") == "https://dev.to/x"
    assert dv.existing(arts, "https://b/") is None
