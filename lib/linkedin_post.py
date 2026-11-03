#!/usr/bin/env python3
"""Post to LinkedIn as Gregor, the Datafund page or the PLUR page (Posts API).

    linkedin_post.py auth   --as gregor
    linkedin_post.py whoami --as gregor
    linkedin_post.py post   --as gregor --text-file post.txt [--link URL | --image IMG]
                            --approved "<the owner's yes>" [--dry-run]
    linkedin_post.py delete --as gregor --post urn:li:share:N --confirm urn:li:share:N

Authors live in the comms data folder (comms_paths): linkedin_authors.yaml.

    gregor:   {kind: person, app: datafund-publisher}
    datafund: {kind: organization, org_id: "123", app: datafund-pages}
    plur:     {kind: organization, org_id: "456", app: plur-pages}

A person posts with "Share on LinkedIn" (w_member_social). A page posts with the
Community Management API (w_organization_social), signed in by a page admin.
LinkedIn will not put that product on an app that has any other product, so a
page uses its own app.

Guards (tests/test_linkedin_post.py):
- nothing posts without --approved "<the owner's yes>"; the note is logged;
- an unverifiable pilot/deployment claim is refused (guardrails, MEM-18);
- the text is escaped for LinkedIn's "little" format before it is sent;
- --dry-run prints the exact request and no token;
- tokens never appear on a command line, in output or in the log.

Credentials (broker ids; values only via `creds get`):
  linkedin-client-<app>    LINKEDIN_CLIENT_<APP>   JSON {"client_id", "client_secret"} (owner adds)
  linkedin-token-<author>  LINKEDIN_TOKEN_<AUTHOR> JSON {"access_token", "expires_at",
                                                         "person_urn"} (written by `auth`)

Access tokens last 60 days. `auth` again before then; every command warns in
the last week.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
CORE_LIB = Path(os.environ.get("DATACORE_LIB") or LIB.parent.parent.parent / "lib")
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))
from datacore.ledger import attests  # noqa: E402

API = "https://api.linkedin.com"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
# LinkedIn retires a version after about a year; a 426 NONEXISTENT_VERSION means bump this.
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202610")
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/linkedin/callback"
SCOPES = {"person": "openid profile w_member_social",
          "organization": "w_organization_social"}
CONSUMER = "linkedin_post"
TEXT_MAX = 3000
WARN_DAYS = 7


class Refused(Exception):
    """A guard said no. The message says why and what to do."""


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- text

# LinkedIn's "little" text format treats these as markup; unescaped, the post
# is cut off or mangled at the first one.
_RESERVED = set("\\|{}@[]()<>#*_~")
_HASHTAG = re.compile(r"(?<![\w#])#([A-Za-z][A-Za-z0-9_]*)")


def escape_commentary(text: str) -> str:
    out, i = [], 0
    for m in _HASHTAG.finditer(text):
        out.append(_escape(text[i:m.start()]))
        out.append("{hashtag|\\#|" + m.group(1) + "}")
        i = m.end()
    out.append(_escape(text[i:]))
    return "".join(out)


def _escape(s: str) -> str:
    return "".join("\\" + c if c in _RESERVED else c for c in s)


def check_text(text: str) -> None:
    if not text.strip():
        raise Refused("the post text is empty")
    if len(text) > TEXT_MAX:
        raise Refused(f"the post is {len(text)} characters; LinkedIn allows {TEXT_MAX}")
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from guardrails import ClaimNotVerified, claim_violations  # noqa: PLC0415
    found = claim_violations(text)
    if found:
        raise ClaimNotVerified("; ".join(found))


def require_approved(approved: str | None) -> None:
    if not (approved or "").strip():
        raise Refused("posting needs the owner's yes for this exact text: pass "
                      "--approved \"<who said yes, when>\"")


def check_delete_confirmation(post: str, confirm: str | None) -> None:
    if confirm != post:
        raise Refused("deleting needs the post id twice: --post ID --confirm ID")


# ---------------------------------------------------------------- authors

def _data_dir() -> Path:
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from comms_paths import data_dir  # noqa: PLC0415
    return data_dir()


def load_authors() -> dict:
    p = _data_dir() / "linkedin_authors.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def author_config(name: str, authors: dict) -> dict:
    cfg = authors.get(name)
    if not isinstance(cfg, dict) or cfg.get("kind") not in ("person", "organization"):
        raise Refused(f"author {name!r} is not configured in linkedin_authors.yaml "
                      f"(kind: person|organization)")
    return cfg


def author_urn(name: str, cfg: dict, token: dict) -> str:
    if cfg["kind"] == "organization":
        org = str(cfg.get("org_id") or "").strip()
        if not org.isdigit():
            raise Refused(f"page author {name!r} needs a numeric org_id in "
                          f"linkedin_authors.yaml (from the page's admin URL)")
        return f"urn:li:organization:{org}"
    urn = token.get("person_urn") or ""
    if not urn.startswith("urn:li:person:"):
        raise Refused(f"the token for {name!r} has no person id; run `auth --as {name}`")
    return urn


# ---------------------------------------------------------------- credentials

def read_credential(cred_id: str) -> str:
    """Value from the broker. Captured, never printed; nothing secret on argv."""
    creds_bin = shutil.which("creds") or str(CORE_LIB.parent / "bin" / "creds")
    r = subprocess.run([creds_bin, "get", cred_id, "--consumer", CONSUMER],
                       capture_output=True, text=True)
    value = (r.stdout or "").strip()
    if r.returncode != 0 or not value:
        raise Refused(f"the broker did not serve {cred_id}. If it was never added: "
                      f"`creds add {cred_id} ...` then `creds sync`. Do not look for it on disk.")
    return value


def _var(prefix: str, name: str) -> str:
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}"


def load_client(app: str) -> dict:
    data = json.loads(read_credential(f"linkedin-client-{app}"))
    if not data.get("client_id") or not data.get("client_secret"):
        raise Refused(f"linkedin-client-{app} must be JSON with client_id and client_secret")
    return data


def load_token(author: str) -> dict:
    return json.loads(read_credential(f"linkedin-token-{author}"))


def save_token(author: str, token: dict) -> None:
    """Write the token in-process (never argv) through the broker."""
    from creds import CredentialManager  # noqa: PLC0415
    mgr = CredentialManager()
    cred_id = f"linkedin-token-{author}"
    index = yaml.safe_load(mgr.index_path.read_text()) or {}
    exists = any(isinstance(r, dict) and r.get("id") == cred_id
                 for r in index.get("credentials") or [])
    rc = mgr.cmd_add(cred_id, var_name=_var("LINKEDIN_TOKEN", author),
                     value=json.dumps(token), scope="global", tier="high",
                     category="social", provider="linkedin",
                     description=f"LinkedIn access token for {author} (linkedin_post.py auth; 60 days)",
                     overwrite=exists)
    if rc != 0:
        try:
            served = load_token(author)
        except Refused:
            served = None
        if served != token:
            raise Refused(f"the broker refused to store {cred_id} (exit {rc})")
        print(f"WARNING: {cred_id} is saved on this host but did not reach the other "
              f"hosts; fix the sync and run `creds sync`.", file=sys.stderr)


def check_token(author: str, token: dict) -> None:
    if not token.get("access_token"):
        raise Refused(f"no LinkedIn token for {author}; run `auth --as {author}`")
    if int(token.get("expires_at") or 0) <= _now():
        raise Refused(f"the LinkedIn token for {author} has expired; run `auth --as {author}`")


def warn_if_expiring(author: str, token: dict) -> None:
    left = int(token.get("expires_at") or 0) - _now()
    if 0 < left < WARN_DAYS * 86400:
        print(f"WARNING: the LinkedIn token for {author} expires in {left // 86400} days; "
              f"run `auth --as {author}`", file=sys.stderr)


# ---------------------------------------------------------------- log

def log_event(event: dict) -> None:
    event = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **event}
    with (_data_dir() / "linkedin-posts.jsonl").open("a") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- request

def build_post_body(author: str, text: str, article: dict | None = None,
                    image_urn: str | None = None) -> dict:
    body = {
        "author": author,
        "commentary": escape_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if article:
        body["content"] = {"article": {k: v for k, v in article.items() if v}}
    elif image_urn:
        body["content"] = {"media": {"id": image_urn}}
    return body


def _headers(token: str | None, extra: dict | None = None) -> dict:
    h = {"LinkedIn-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0",
         "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return {**h, **(extra or {})}


def _send(method: str, url: str, token: str, body: dict | None = None,
          data: bytes | None = None, headers: dict | None = None):
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=payload, method=method,
                                 headers=headers or _headers(token))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise Refused(f"LinkedIn answered {exc.code} for {method} {url.split('?')[0]}: {detail}") from None


@attests("linkedin.post", ref=lambda r: r or "", detail="")
def publish(token: str, body: dict) -> str:
    """Send the post; returns its URN. Attested after LinkedIn accepted it."""
    status, headers, _ = _send("POST", f"{API}/rest/posts", token, body)
    urn = headers.get("x-restli-id") or headers.get("X-RestLi-Id") or ""
    if status not in (200, 201) or not urn:
        raise Refused(f"LinkedIn answered {status} without a post id")
    return urn


def upload_image(token: str, owner: str, path: Path) -> str:
    _, _, r = _send("POST", f"{API}/rest/images?action=initializeUpload", token,
                    {"initializeUploadRequest": {"owner": owner}})
    value = r.get("value") or {}
    url, urn = value.get("uploadUrl"), value.get("image")
    if not url or not urn:
        raise Refused("LinkedIn did not return an image upload URL")
    _send("PUT", url, token, data=path.read_bytes(),
          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
    return urn


# ---------------------------------------------------------------- link preview

_META = re.compile(r"<meta\s+[^>]*?(?:property|name)=[\"']og:(title|description|image)[\"'][^>]*?"
                   r"content=[\"']([^\"']*)[\"']", re.I)


def parse_open_graph(page: str) -> dict:
    """LinkedIn's API does not scrape link previews: we read the page's og: tags."""
    og = {}
    for key, value in _META.findall(page):
        og.setdefault(key.lower(), html.unescape(value))
    return og


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (linkedin_post)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def article_for(link: str, token: str | None, owner: str, dry_run: bool) -> dict:
    og = parse_open_graph(_fetch(link).decode("utf-8", "replace"))
    article = {"source": link, "title": og.get("title", ""),
               "description": og.get("description", "")}
    if og.get("image"):
        if dry_run:
            article["thumbnail"] = f"(upload of {og['image']})"
        else:
            tmp = _data_dir() / "linkedin-thumb.tmp"
            tmp.write_bytes(_fetch(og["image"]))
            try:
                article["thumbnail"] = upload_image(token, owner, tmp)
            finally:
                tmp.unlink(missing_ok=True)
    return article


# ---------------------------------------------------------------- auth

def _authorize(client_id: str, scope: str) -> str:
    """Open the browser, wait for LinkedIn's redirect, return the code."""
    state = secrets.token_urlsafe(24)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, *a):  # quiet
            pass

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "state": state, "scope": scope})
    srv = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    print("Opening the browser. Sign in to LinkedIn as the owner and allow access.",
          file=sys.stderr)
    webbrowser.open(url)
    srv.handle_request()
    srv.server_close()
    if got.get("state") != state:
        raise Refused("the sign-in answer did not match this request; nothing saved")
    if "code" not in got:
        raise Refused(f"LinkedIn did not sign in: {got.get('error_description') or got.get('error')}")
    return got["code"]


def cmd_auth(a) -> int:
    cfg = author_config(a.author, load_authors())
    client = load_client(cfg.get("app") or "")
    code = _authorize(client["client_id"], SCOPES[cfg["kind"]])
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": REDIRECT_URI,
                                   "client_id": client["client_id"],
                                   "client_secret": client["client_secret"]}).encode()
    req = urllib.request.Request(TOKEN_URL, data=form,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read())
    token = {"access_token": tok["access_token"],
             "expires_at": _now() + int(tok.get("expires_in", 0))}
    if cfg["kind"] == "person":
        _, _, me = _send("GET", f"{API}/v2/userinfo", tok["access_token"],
                         headers={"Authorization": f"Bearer {tok['access_token']}"})
        token["person_urn"] = f"urn:li:person:{me['sub']}"
    save_token(a.author, token)
    log_event({"action": "auth", "author": a.author, "expires_at": token["expires_at"]})
    days = (token["expires_at"] - _now()) // 86400
    print(f"{a.author}: signed in; token stored in the broker, valid {days} days")
    return 0


# ---------------------------------------------------------------- commands

def cmd_whoami(a) -> int:
    cfg = author_config(a.author, load_authors())
    token = load_token(a.author)
    check_token(a.author, token)
    warn_if_expiring(a.author, token)
    days = (int(token["expires_at"]) - _now()) // 86400
    print(f"{a.author}: {author_urn(a.author, cfg, token)} (token valid {days} more days)")
    return 0


def cmd_post(a) -> int:
    if not a.dry_run:
        require_approved(a.approved)
    text = Path(a.text_file).read_text().strip()
    check_text(text)
    cfg = author_config(a.author, load_authors())
    try:
        token = load_token(a.author)
    except Refused:
        if not a.dry_run:
            raise
        # A preview before the first sign-in: show the request with a placeholder author.
        token = {"access_token": "", "person_urn": "urn:li:person:(after sign-in)",
                 "expires_at": _now() + 86400}
    if token.get("access_token"):
        check_token(a.author, token)
        warn_if_expiring(a.author, token)
    elif not a.dry_run:
        check_token(a.author, token)
    owner = author_urn(a.author, cfg, token)
    article = article_for(a.link, token["access_token"], owner, a.dry_run) if a.link else None
    image = None
    if a.image and not article:
        image = "(upload of image)" if a.dry_run else upload_image(token["access_token"], owner, Path(a.image))
    body = build_post_body(owner, text, article=article, image_urn=image)
    if a.dry_run:
        print(f"POST {API}/rest/posts")
        print(json.dumps({k: v for k, v in _headers(None).items()}, indent=1))
        print(json.dumps(body, indent=1, ensure_ascii=False))
        return 0
    urn = publish(token["access_token"], body)
    log_event({"action": "post", "author": a.author, "urn": urn, "text": text[:200],
               "link": a.link, "approved": a.approved})
    print(f"posted as {a.author}: https://www.linkedin.com/feed/update/{urn}/")
    return 0


def cmd_delete(a) -> int:
    check_delete_confirmation(a.post, a.confirm)
    author_config(a.author, load_authors())
    token = load_token(a.author)
    check_token(a.author, token)
    _send("DELETE", f"{API}/rest/posts/{urllib.parse.quote(a.post, safe='')}",
          token["access_token"], headers=_headers(token["access_token"],
                                                  {"X-RestLi-Method": "DELETE"}))
    log_event({"action": "delete", "author": a.author, "urn": a.post})
    print(f"deleted {a.post} ({a.author})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def who(sp):
        sp.add_argument("--as", dest="author", required=True, help="gregor, datafund or plur")

    s = sub.add_parser("auth", help="one-time browser sign-in for an author (60-day token)")
    who(s); s.set_defaults(fn=cmd_auth)
    s = sub.add_parser("whoami", help="which LinkedIn identity an author posts as")
    who(s); s.set_defaults(fn=cmd_whoami)
    s = sub.add_parser("post", help="post one approved text")
    who(s)
    s.add_argument("--text-file", required=True)
    s.add_argument("--link", help="add a link preview built from the page's og: tags")
    s.add_argument("--image", help="attach one image (ignored with --link)")
    s.add_argument("--approved", help="the owner's yes for this exact text")
    s.add_argument("--dry-run", action="store_true", help="print the request; send nothing")
    s.set_defaults(fn=cmd_post)
    s = sub.add_parser("delete", help="delete one post (needs --confirm ID)")
    who(s)
    s.add_argument("--post", required=True)
    s.add_argument("--confirm")
    s.set_defaults(fn=cmd_delete)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
