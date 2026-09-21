#!/usr/bin/env python3
"""X API v2 client for posting replies.

Uses OAuth 1.0a (user context) for tweeting.
Env vars: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""

import hashlib
import hmac
import json

# DIP-0047: was a hand-rolled sys.path guess, which resolved to a tree with no
# ledger package on plur-claw and silently recorded nothing for months.
from datacore.ledger import attests
import os
import time
import urllib.parse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import Optional
import base64
import secrets


TWEET_URL = "https://api.x.com/2/tweets"


def _load_credentials():
    """The FDS X OAuth1 set, under the names the credential index actually uses.

    This read X_API_KEY / X_API_SECRET, which exist in no store and in no index
    on any host: the indexed names are X_CONSUMER_KEY / X_CONSUMER_SECRET
    (fds-x-account). So every call raised KeyError: 'X_API_KEY' — verified on
    this machine 2026-08-19, meaning comms X posting had been dead rather than
    misconfigured, and silently, because a KeyError at import of credentials
    looks like any other traceback.

    The consumer pair accepts the legacy names as a fallback so a host that
    still defines them keeps working, but the indexed name is preferred. The
    token pair was already correct.
    """
    def pick(*names):
        for n in names:
            v = os.environ.get(n)
            if v:
                return v
        raise KeyError(
            f"none of {names} is set. These are declared by fds-x-account in "
            f"credential-index.yaml — resolve with "
            f"`python3 .datacore/lib/creds.py get fds-x-account`, and do not "
            f"search for another copy.")

    return {
        "consumer_key": pick("X_CONSUMER_KEY", "X_API_KEY"),
        "consumer_secret": pick("X_CONSUMER_SECRET", "X_API_SECRET"),
        "access_token": pick("X_ACCESS_TOKEN"),
        "access_token_secret": pick("X_ACCESS_TOKEN_SECRET"),
    }


def _percent_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth_signature(method: str, url: str, params: dict, consumer_secret: str, token_secret: str) -> str:
    """Generate OAuth 1.0a signature."""
    sorted_params = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(params.items())
    )
    base_string = f"{method}&{_percent_encode(url)}&{_percent_encode(sorted_params)}"
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    signature = hmac.new(
        signing_key.encode(), base_string.encode(), hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()


def _oauth_header(method: str, url: str, creds: dict, extra_params: dict = None) -> str:
    """Build OAuth Authorization header."""
    oauth_params = {
        "oauth_consumer_key": creds["consumer_key"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds["access_token"],
        "oauth_version": "1.0",
    }

    all_params = {**oauth_params}
    if extra_params:
        all_params.update(extra_params)

    signature = _oauth_signature(
        method, url, all_params,
        creds["consumer_secret"], creds["access_token_secret"]
    )
    oauth_params["oauth_signature"] = signature

    auth_header = "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return auth_header


@attests("x.reply",
         ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
         detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
def post_reply(text: str, in_reply_to_tweet_id: str) -> dict:
    """Post a reply tweet via X API v2.

    Returns: {"data": {"id": "...", "text": "..."}} on success
    Raises: Exception on failure
    """
    creds = _load_credentials()

    payload = {
        "text": text,
        "reply": {
            "in_reply_to_tweet_id": in_reply_to_tweet_id,
        },
    }

    body = json.dumps(payload).encode()
    auth = _oauth_header("POST", TWEET_URL, creds)

    req = Request(TWEET_URL, data=body, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"X API error {e.code}: {error_body}")

    # A reply is as public and as irreversible as a standalone post.
    return result


@attests("x.post",
         ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
         detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
def post_tweet(text: str) -> dict:
    """Post a standalone tweet (not a reply)."""
    creds = _load_credentials()

    payload = {"text": text}
    body = json.dumps(payload).encode()
    auth = _oauth_header("POST", TWEET_URL, creds)

    req = Request(TWEET_URL, data=body, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"X API error {e.code}: {error_body}")

    # ATTEST AFTER THE POST LANDS (DIP-0038). A tweet cannot be un-sent and
    # cannot be re-derived from local state, which makes it exactly the kind of
    # action the ledger should hold — yet publishing left no trace at all while
    # spend was metered to the cent. Recorded after the fact, never before: an
    # attestation of an intended post would read as authoritative about
    # something that may not have happened.
    return result


