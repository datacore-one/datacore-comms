"""Tests for linkedin_post.py — the guards and the request shape, not LinkedIn.

What must hold whatever LinkedIn does:
- nothing is posted without --approved (the owner's yes for that text);
- an unverifiable pilot/deployment claim is refused (guardrails, MEM-18);
- the commentary is escaped for LinkedIn's "little" text format, so a
  parenthesis or underscore cannot silently cut the post short;
- a dry run prints the exact request and never a token;
- an author must be configured: a person posts as urn:li:person, a page as
  urn:li:organization, and a page author without an org id is refused;
- deleting needs the post id typed twice;
- no token ever reaches the log.
"""
import json

import pytest

import linkedin_post as li


PERSON = {"kind": "person"}
ORG = {"kind": "organization", "org_id": "12345"}
TOKEN = {"access_token": "SECRET-TOKEN-VALUE", "person_urn": "urn:li:person:abc",
         "expires_at": 4102444800}


# ---------------------------------------------------------------- escaping

def test_reserved_characters_are_escaped():
    out = li.escape_commentary("A (note) with_under * and [x] ~ < > | { } @ \\")
    for ch in "()_*[]~<>|{}@":
        assert f"\\{ch}" in out
    assert "\\\\" in out


def test_hashtags_become_hashtag_markup():
    out = li.escape_commentary("Shipping today #AIAgents and #MCP.")
    assert "{hashtag|\\#|AIAgents}" in out
    assert "{hashtag|\\#|MCP}" in out


def test_a_lone_hash_is_escaped_not_a_hashtag():
    assert li.escape_commentary("issue # 5") == "issue \\# 5"


def test_urls_are_kept_readable():
    out = li.escape_commentary("Read https://plur.ai/blog/why-plur-chose-swarm/ now")
    assert "https://plur.ai/blog/why-plur-chose-swarm/" in out.replace("\\", "")


# ---------------------------------------------------------------- authors

def test_person_author_urn_comes_from_the_token():
    assert li.author_urn("gregor", PERSON, TOKEN) == "urn:li:person:abc"


def test_page_author_urn_comes_from_the_org_id():
    assert li.author_urn("plur", ORG, TOKEN) == "urn:li:organization:12345"


def test_page_author_without_org_id_is_refused():
    with pytest.raises(li.Refused, match="org_id"):
        li.author_urn("plur", {"kind": "organization"}, TOKEN)


def test_unknown_author_is_refused():
    with pytest.raises(li.Refused, match="not configured"):
        li.author_config("nobody", {"gregor": PERSON})


# ---------------------------------------------------------------- body

def test_body_shape_for_a_text_post():
    body = li.build_post_body("urn:li:person:abc", "Hello (world)")
    assert body["author"] == "urn:li:person:abc"
    assert body["commentary"] == "Hello \\(world\\)"
    assert body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED"
    assert body["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert "content" not in body


def test_body_with_article_link():
    body = li.build_post_body("urn:li:organization:1", "Read this",
                              article={"source": "https://plur.ai/x/", "title": "T",
                                       "description": "D", "thumbnail": "urn:li:image:9"})
    art = body["content"]["article"]
    assert art["source"] == "https://plur.ai/x/"
    assert art["title"] == "T" and art["thumbnail"] == "urn:li:image:9"


def test_body_with_image():
    body = li.build_post_body("urn:li:person:abc", "Pic", image_urn="urn:li:image:7")
    assert body["content"]["media"]["id"] == "urn:li:image:7"


def test_too_long_text_is_refused():
    with pytest.raises(li.Refused, match="3000"):
        li.check_text("x" * 3001)


def test_unverifiable_pilot_claim_is_refused():
    with pytest.raises(Exception) as exc:
        li.check_text("PLUR is in production at a national ministry.")
    assert "pilot" in str(exc.value).lower() or "claim" in str(exc.value).lower() \
        or exc.type.__name__ == "ClaimNotVerified"


# ---------------------------------------------------------------- approval

def test_posting_needs_owner_yes():
    with pytest.raises(li.Refused, match="owner"):
        li.require_approved(None)
    with pytest.raises(li.Refused, match="owner"):
        li.require_approved("   ")
    li.require_approved("owner yes in chat 2026-11-03")


def test_post_without_approved_is_refused_before_any_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(li, "_data_dir", lambda: tmp_path)
    (tmp_path / "linkedin_authors.yaml").write_text("gregor:\n  kind: person\n")
    f = tmp_path / "p.txt"; f.write_text("Hello")
    monkeypatch.setattr(li, "load_token", lambda a: pytest.fail("token read before approval"))
    assert li.main(["post", "--as", "gregor", "--text-file", str(f)]) == 2
    assert "owner" in capsys.readouterr().err


# ---------------------------------------------------------------- dry run

def test_dry_run_prints_request_and_never_the_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(li, "_data_dir", lambda: tmp_path)
    (tmp_path / "linkedin_authors.yaml").write_text("gregor:\n  kind: person\n")
    monkeypatch.setattr(li, "load_token", lambda a: dict(TOKEN))
    monkeypatch.setattr(li, "_send", lambda *a, **k: pytest.fail("network call in dry run"))
    f = tmp_path / "p.txt"; f.write_text("Can you prove it? (yes)")
    rc = li.main(["post", "--as", "gregor", "--text-file", str(f), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SECRET-TOKEN-VALUE" not in out
    assert "/rest/posts" in out and "LinkedIn-Version" in out
    body = json.loads(out[out.index('{\n "author"'):])
    assert body["author"] == "urn:li:person:abc"
    assert body["commentary"] == "Can you prove it? \\(yes\\)"


# ---------------------------------------------------------------- link preview

def test_open_graph_tags_are_read():
    html = ('<html><head><meta property="og:title" content="Why PLUR chose Swarm — PLUR Blog">'
            '<meta property="og:description" content="Sooner or later &amp; more">'
            '<meta property="og:image" content="https://plur.ai/blog/images/og.png"></head></html>')
    og = li.parse_open_graph(html)
    assert og["title"] == "Why PLUR chose Swarm — PLUR Blog"
    assert og["description"] == "Sooner or later & more"
    assert og["image"] == "https://plur.ai/blog/images/og.png"


# ---------------------------------------------------------------- delete, token, log

def test_delete_needs_repeated_id():
    with pytest.raises(li.Refused, match="--confirm"):
        li.check_delete_confirmation("urn:li:share:1", None)
    with pytest.raises(li.Refused, match="--confirm"):
        li.check_delete_confirmation("urn:li:share:1", "urn:li:share:2")
    li.check_delete_confirmation("urn:li:share:1", "urn:li:share:1")


def test_token_near_expiry_warns(capsys):
    li.warn_if_expiring("gregor", {"expires_at": li._now() + 3 * 86400})
    assert "expires" in capsys.readouterr().err
    li.warn_if_expiring("gregor", {"expires_at": li._now() + 40 * 86400})
    assert capsys.readouterr().err == ""


def test_expired_token_is_refused():
    with pytest.raises(li.Refused, match="expired"):
        li.check_token("gregor", {"access_token": "x", "expires_at": li._now() - 10})


def test_log_has_no_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(li, "_data_dir", lambda: tmp_path)
    li.log_event({"action": "post", "author": "gregor", "urn": "urn:li:share:1",
                  "approved": "yes"})
    text = (tmp_path / "linkedin-posts.jsonl").read_text()
    assert "SECRET" not in text and json.loads(text)["urn"] == "urn:li:share:1"


def test_dry_run_works_before_sign_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(li, "_data_dir", lambda: tmp_path)
    (tmp_path / "linkedin_authors.yaml").write_text(
        "gregor:\n  kind: person\nplur:\n  kind: organization\n  org_id: '42'\n")

    def no_token(author):
        raise li.Refused("the broker did not serve linkedin-token-x")
    monkeypatch.setattr(li, "load_token", no_token)
    f = tmp_path / "p.txt"; f.write_text("Hello")
    assert li.main(["post", "--as", "plur", "--text-file", str(f), "--dry-run"]) == 0
    assert '"author": "urn:li:organization:42"' in capsys.readouterr().out
    assert li.main(["post", "--as", "gregor", "--text-file", str(f), "--dry-run"]) == 0
    assert "after sign-in" in capsys.readouterr().out
