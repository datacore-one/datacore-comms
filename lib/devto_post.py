#!/usr/bin/env python3
"""Publish one markdown article to dev.to, as the PLUR or the Datafund account.

    devto_post.py post --account datafund --file a.md --canonical URL --site https://datafund.io \
                       --tags ai,agents --approved "<the owner's yes>"
    devto_post.py find --account datafund --canonical URL      # exit 0 only if live (no credentials)

Guards (tests/test_devto_post.py): nothing posts without --approved; an
unverifiable pilot claim is refused; an article already on the account is not
posted again (dev.to does not refuse duplicates); site markup is converted, and
HTML dev.to would not render is refused. The key comes from the broker.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
CORE_LIB = LIB.parent.parent.parent / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))
from datacore.ledger import attests  # noqa: E402

API = "https://dev.to/api"
ACCOUNTS = {"plur": ("devto-api-key", "plur9"), "datafund": ("devto-api-key-datafund", "datafund")}


class Refused(Exception):
    """A guard said no. The message says why."""


def credential_id(account: str) -> str:
    if account not in ACCOUNTS:
        raise Refused(f"unknown account {account!r}; known: {sorted(ACCOUNTS)}")
    return ACCOUNTS[account][0]


def read_key(account: str) -> str:
    creds = shutil.which("creds") or str(CORE_LIB.parent / "bin" / "creds")
    cid = credential_id(account)
    r = subprocess.run([creds, "get", cid, "--consumer", "devto_post"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise Refused(f"the broker did not serve {cid}; do not look for it on disk")
    return r.stdout.strip()


def to_markdown(body: str, site: str) -> str:
    body = re.sub(r'<script[^>]*platform\.twitter\.com[^>]*></script>\s*', "", body)
    body = re.sub(r'<blockquote class="twitter-tweet">.*?/status/(\d+).*?</blockquote>',
                  r"{% twitter \1 %}", body, flags=re.S)
    body = re.sub(r'(?:<div[^>]*>\s*)?<iframe[^>]*youtube(?:-nocookie)?\.com/embed/([\w-]+)[^>]*></iframe>(?:\s*</div>)?',
                  r"{% youtube \1 %}", body, flags=re.S)

    def image(m):
        src = re.search(r'src="([^"]+)"', m.group(0)).group(1)
        alt = re.search(r'alt="([^"]*)"', m.group(0))
        return f"![{alt.group(1) if alt else ''}]({site + src if src.startswith('/') else src})"
    body = re.sub(r"<img\b[^>]*>", image, body)
    parts = re.split(r"(```.*?```|`[^`\n]*`)", body, flags=re.S)
    body = "".join(x if i % 2 else re.sub(r"\]\(/(?!/)", f"]({site}/", x) for i, x in enumerate(parts))
    prose = "".join(x for i, x in enumerate(parts) if not i % 2)
    left = re.search(r"</?(?:video|iframe|script|div|source)\b", prose)
    if left:
        raise Refused(f"markup dev.to would not render: {left.group(0)}")
    return body.strip()


def check_claims(text: str) -> None:
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from guardrails import ClaimNotVerified, claim_violations  # noqa: PLC0415
    found = claim_violations(text)
    if found:
        raise ClaimNotVerified("; ".join(found))


def existing(articles: list, canonical: str):
    for a in articles:
        if a.get("canonical_url") == canonical:
            return a.get("url")
    return None


def _request(url: str, key: str | None = None, body: dict | None = None):
    headers = {"User-Agent": "datacore-comms", "Accept": "application/vnd.forem.api-v1+json"}
    if key:
        headers["api-key"] = key
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@attests("social.post", ref=lambda r: r or "", detail="dev.to")
def publish(key: str, article: dict) -> str:
    return _request(f"{API}/articles", key, {"article": article})["url"]


def cmd_post(a) -> int:
    if not (a.approved or "").strip():
        raise Refused("posting needs the owner's yes: pass --approved \"<who said yes, when>\"")
    _, front, body = Path(a.file).read_text().split("---\n", 2)
    meta = yaml.safe_load(front)
    check_claims(meta["title"] + "\n" + body)
    markdown = to_markdown(body.strip(), a.site)
    key = read_key(a.account)
    already = existing(_request(f"{API}/articles/me/all?per_page=1000", key), a.canonical)
    if already:
        print(f"already on dev.to, not posted again: {already}")
        return 0
    url = publish(key, {"title": meta["title"], "description": meta.get("description", ""),
                        "body_markdown": markdown, "published": True,
                        "tags": [t for t in a.tags.split(",") if t][:4], "canonical_url": a.canonical})
    print(f"posted: {url}")
    return 0


def cmd_find(a) -> int:
    credential_id(a.account)
    user = ACCOUNTS[a.account][1]
    arts = _request(f"{API}/articles?username={urllib.parse.quote(user)}&per_page=1000")
    url = existing(arts, a.canonical)
    print(f"live: {url}" if url else f"not on dev.to/{user}: {a.canonical}")
    return 0 if url else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("post")
    s.add_argument("--account", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--canonical", required=True)
    s.add_argument("--site", required=True)
    s.add_argument("--tags", default="")
    s.add_argument("--approved")
    s.set_defaults(fn=cmd_post)
    s = sub.add_parser("find")
    s.add_argument("--account", required=True)
    s.add_argument("--canonical", required=True)
    s.set_defaults(fn=cmd_find)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
