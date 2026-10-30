# .datacore/modules/comms/lib/x_poster.py
"""Multi-account X poster with OAuth 1.0a signing and persistent rate limiting.

Supports: post, reply, quote RT, like, follow, unfollow.
Reusable across campaigns: FDS, Datacore (@datacore, @mr_data_dc).

**Rate limits persist to SQLite** — survives systemd oneshot restarts.
**Kill switch** — checks for .datacore/state/campaign-kill-switch file.

Account credentials use env var prefix convention:
  {PREFIX}_X_API_KEY, {PREFIX}_X_API_SECRET,
  {PREFIX}_X_ACCESS_TOKEN, {PREFIX}_X_ACCESS_TOKEN_SECRET

Usage:
    poster = XPoster(account='fds')
    poster.post("Privacy by architecture, not by promise.")
    poster.reply("Exactly.", tweet_id="123456")
    poster.like("123456")
    poster.follow("67890")
"""
import base64
import hashlib
import hmac
import json

# DIP-0047. A SECOND live X posting path, found by egress_scan after a manual
# audit concluded publishing funnelled through x_api alone. It does not: this
# class is wired in module.yaml and run_follow_daily.py and posted unattested.
from datacore.ledger import attests
import os
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TWEET_URL = "https://api.x.com/2/tweets"
MEDIA_URL = "https://api.x.com/2/media/upload"
VIDEO_CHUNK = 4 * 1024 * 1024  # X accepts up to 5 MB per append

# Map account names to env var prefixes
ACCOUNT_PREFIXES = {
    'fds': 'FDS_X_',
    'jssr': 'JSSR_X_',
    'datacore': 'DATACORE_X_',
    'mr_data_dc': 'MRDATA_X_',
    'plur': 'PLUR_X_',
    'datafund': 'DATAFUND_X_',
}

# Kill switch — if this file exists, all posting is halted
KILL_SWITCH_PATH = Path(os.environ.get(
    "DATACORE_ROOT", os.path.expanduser("~/Data")
)) / ".datacore" / "state" / "campaign-kill-switch"


class KillSwitchActive(RuntimeError):
    """Raised when kill switch file exists."""
    pass


class XPoster:
    def __init__(self, account: str, daily_limit: int = 50,
                 user_id: str = None, state_dir: str = None,
                 verify_links: bool = True):
        prefix = ACCOUNT_PREFIXES.get(account)
        if not prefix:
            raise ValueError(
                f"Unknown account: {account}. "
                f"Known: {list(ACCOUNT_PREFIXES)}"
            )
        self.account = account
        self.api_key = os.environ[f'{prefix}API_KEY']
        self.api_secret = os.environ[f'{prefix}API_SECRET']
        self.access_token = os.environ[f'{prefix}ACCESS_TOKEN']
        self.access_token_secret = os.environ[f'{prefix}ACCESS_TOKEN_SECRET']
        self.user_id = user_id  # needed for like/follow endpoints
        self.daily_limit = daily_limit

        # Persistent rate limit DB
        state_path = state_dir or str(
            Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
            / ".datacore" / "state"
        )
        Path(state_path).mkdir(parents=True, exist_ok=True)
        db_file = Path(state_path) / "rate-limits.db"
        self._rate_db = sqlite3.connect(str(db_file))
        self._rate_db.execute("PRAGMA journal_mode=WAL")
        self._rate_db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                account TEXT, date TEXT, post_count INTEGER DEFAULT 0,
                PRIMARY KEY (account, date)
            )
        """)
        self._rate_db.commit()

        # Link verification gate
        self.verify_links = verify_links
        self._verifier = None
        if verify_links:
            try:
                import sys
                sys.path.insert(0, str(Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))) / ".datacore" / "lib"))
                from link_verifier import LinkVerifier
                self._verifier = LinkVerifier()
            except ImportError:
                import sys
                print("Warning: link_verifier not found, link verification disabled", file=sys.stderr)
                self.verify_links = False

    # --- Public API ---

    def _refuse_claims(self, text: str):
        """Stop an unverifiable pilot/deployment claim before the network (MEM-18).

        Not skippable: skip_verification is about links; a claim that an
        institution runs our product needs a human, every time.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from guardrails import ClaimNotVerified, claim_violations
        found = claim_violations(text)
        if found:
            raise ClaimNotVerified("; ".join(found))

    def _verify_text_links(self, text: str, skip_verification: bool = False):
        """Verify all links in text before posting. Raises LinkVerificationError on failure."""
        if skip_verification or not self.verify_links or not self._verifier:
            return
        self._verifier.verify_or_raise(text)

    @attests("x.post",
             ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
             detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
    def post(self, text: str, skip_verification: bool = False) -> dict:
        """Post a standalone tweet."""
        self._refuse_claims(text)
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {'text': text})
        self._increment_rate_count()
        return result

    @attests("x.reply",
             ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
             detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
    def reply(self, text: str, reply_to_id: str, skip_verification: bool = False) -> dict:
        """Reply to a tweet."""
        self._refuse_claims(text)
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {
            'text': text,
            'reply': {'in_reply_to_tweet_id': str(reply_to_id)},
        })
        self._increment_rate_count()
        return result

    @attests("x.post",
             ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
             detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
    def quote_rt(self, text: str, tweet_id: str, skip_verification: bool = False) -> dict:
        """Quote retweet."""
        self._refuse_claims(text)
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {
            'text': text,
            'quote_tweet_id': tweet_id,
        })
        self._increment_rate_count()
        return result

    def like(self, tweet_id: str) -> dict:
        """Like a tweet. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/likes"
        return self._oauth_post(url, {'tweet_id': tweet_id})

    def follow(self, target_user_id: str) -> dict:
        """Follow a user. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/following"
        return self._oauth_post(url, {'target_user_id': target_user_id})

    def unfollow(self, target_user_id: str) -> dict:
        """Unfollow a user. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/following/{target_user_id}"
        return self._oauth_delete(url)

    def can_reply(self, tweet_id: str) -> bool:
        """Check if we can reply to a tweet (reply_settings allows it).

        Returns True if replies are open to everyone, False if restricted.
        Returns True on API errors (fail open — let the reply attempt reveal 403).
        """
        try:
            url = f"https://api.x.com/2/tweets/{tweet_id}?tweet.fields=reply_settings"
            result = self._oauth_get(url)
            settings = result.get("data", {}).get("reply_settings", "everyone")
            return settings == "everyone"
        except Exception:
            return True  # Fail open — 403 will be caught at reply time

    def upload_media(self, image_path: str) -> str:
        """Upload an image and return media_id_string. Uses v1.1 media/upload."""
        upload_url = "https://upload.twitter.com/1.1/media/upload.json"
        image_data = Path(image_path).read_bytes()
        boundary = secrets.token_hex(16)
        filename = Path(image_path).name
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media_data"\r\n\r\n'
            f"{base64.b64encode(image_data).decode()}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        sig = self._oauth_signature("POST", upload_url, oauth_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )
        req = Request(upload_url, data=body, method="POST")
        req.add_header("Authorization", header)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                return result["media_id_string"]
        except HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"Media upload error {e.code}: {error_body}")

    def upload_video(self, video_path: str) -> str:
        """Upload an MP4 in chunks and return its media id once X has processed it.

        X API v2: initialize, append each chunk, finalize, then poll until the
        processing state is succeeded. A standard account takes up to 140 s.
        """
        data = Path(video_path).read_bytes()
        init = self._oauth_post(f"{MEDIA_URL}/initialize", {
            'media_type': 'video/mp4', 'total_bytes': len(data), 'media_category': 'tweet_video'})
        media_id = (init.get('data') or {})['id']
        for index, start in enumerate(range(0, len(data), VIDEO_CHUNK)):
            self._oauth_append(f"{MEDIA_URL}/{media_id}/append", data[start:start + VIDEO_CHUNK], index)
        info = (self._oauth_post(f"{MEDIA_URL}/{media_id}/finalize", {}).get('data') or {}).get('processing_info')
        while info and info.get('state') in ('pending', 'in_progress'):
            time.sleep(int(info.get('check_after_secs', 2)))
            r = self._oauth_get(f"{MEDIA_URL}?command=STATUS&media_id={media_id}")
            info = (r.get('data') or {}).get('processing_info')
        if info and info.get('state') != 'succeeded':
            raise Exception(f"Video processing {info.get('state')}: {(info.get('error') or {}).get('message', '')}")
        return media_id

    def _oauth_append(self, url: str, chunk: bytes, index: int) -> None:
        boundary = secrets.token_hex(16)
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"segment_index\"\r\n\r\n{index}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"chunk\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + chunk + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body, method="POST")
        req.add_header("Authorization", self._oauth_header("POST", url))
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urlopen(req, timeout=120):
                pass
        except HTTPError as e:
            raise Exception(f"Video append error {e.code}: {e.read().decode()}")

    @attests("x.post",
             ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
             detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
    def post_with_media(self, text: str, media_ids: list) -> dict:
        """Post a tweet with media attachments."""
        self._refuse_claims(text)
        self._verify_text_links(text)
        self._check_rate_limit()
        payload = {'text': text, 'media': {'media_ids': media_ids}}
        result = self._oauth_post(TWEET_URL, payload)
        self._increment_rate_count()
        return result

    @attests("x.reply",
             ref=lambda r: ((r or {}).get("data") or {}).get("id", ""),
             detail=lambda r: ((r or {}).get("data") or {}).get("text", "")[:200])
    def reply_with_media(self, text: str, reply_to_id: str, media_ids: list) -> dict:
        """Reply to a tweet with media attachments."""
        self._refuse_claims(text)
        self._check_rate_limit()
        payload = {
            'text': text,
            'reply': {'in_reply_to_tweet_id': str(reply_to_id)},
            'media': {'media_ids': media_ids},
        }
        result = self._oauth_post(TWEET_URL, payload)
        self._increment_rate_count()
        return result

    def get_tweet(self, tweet_id: str, fields: str = "reply_settings,author_id,public_metrics") -> dict:
        """Fetch tweet details via X API v2."""
        url = f"https://api.x.com/2/tweets/{tweet_id}?tweet.fields={fields}"
        return self._oauth_get(url)

    # --- Kill switch ---

    def _check_kill_switch(self):
        if KILL_SWITCH_PATH.exists():
            reason = KILL_SWITCH_PATH.read_text().strip() or "No reason given"
            raise KillSwitchActive(
                f"Kill switch active: {reason}. "
                f"Remove {KILL_SWITCH_PATH} to resume."
            )

    # --- Persistent rate limiting (SQLite) ---

    def _check_rate_limit(self):
        self._check_kill_switch()
        today = time.strftime('%Y-%m-%d')
        row = self._rate_db.execute(
            "SELECT post_count FROM rate_limits WHERE account = ? AND date = ?",
            (self.account, today),
        ).fetchone()
        count = row[0] if row else 0
        if count >= self.daily_limit:
            raise RuntimeError(
                f"Daily rate limit ({self.daily_limit}) reached for @{self.account}"
            )

    def _increment_rate_count(self):
        today = time.strftime('%Y-%m-%d')
        self._rate_db.execute(
            """INSERT INTO rate_limits (account, date, post_count)
               VALUES (?, ?, 1)
               ON CONFLICT(account, date) DO UPDATE SET post_count = post_count + 1""",
            (self.account, today),
        )
        self._rate_db.commit()

    # --- OAuth 1.0a signing (from x_api.py) ---

    def _percent_encode(self, s: str) -> str:
        return urllib.parse.quote(str(s), safe="")

    def _oauth_signature(self, method: str, url: str, params: dict) -> str:
        sorted_params = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(v)}"
            for k, v in sorted(params.items())
        )
        base_string = (
            f"{method}&{self._percent_encode(url)}"
            f"&{self._percent_encode(sorted_params)}"
        )
        signing_key = (
            f"{self._percent_encode(self.api_secret)}"
            f"&{self._percent_encode(self.access_token_secret)}"
        )
        sig = hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(sig).decode()

    def _oauth_header(self, method: str, url: str) -> str:
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        sig = self._oauth_signature(method, url, oauth_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )
        return header

    def _oauth_post(self, url: str, payload: dict, _retries: int = 0) -> dict:
        body = json.dumps(payload).encode()
        auth = self._oauth_header("POST", url)
        req = Request(url, data=body, method="POST")
        req.add_header("Authorization", auth)
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 3:
                retry_after = int(e.headers.get('Retry-After', 60))
                time.sleep(min(retry_after + 5, 900))  # Cap at 15 min
                return self._oauth_post(url, payload, _retries=_retries + 1)
            raise Exception(f"X API error {e.code}: {error_body}")

    def _oauth_get(self, url: str, _retries: int = 0) -> dict:
        """GET request with OAuth 1.0a signing.

        Query params are included in the signature base string per OAuth spec.
        """
        # Parse URL and query params
        if '?' in url:
            base_url, qs = url.split('?', 1)
            query_params = dict(urllib.parse.parse_qsl(qs))
        else:
            base_url = url
            query_params = {}

        # Build OAuth params + query params for signature
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        all_params = {**oauth_params, **query_params}
        sig = self._oauth_signature("GET", base_url, all_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )

        req = Request(url, method="GET")
        req.add_header("Authorization", header)
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 2:
                time.sleep(5)
                return self._oauth_get(url, _retries=_retries + 1)
            raise Exception(f"X API GET error {e.code}: {error_body}")

    def _oauth_delete(self, url: str, _retries: int = 0) -> dict:
        auth = self._oauth_header("DELETE", url)
        req = Request(url, method="DELETE")
        req.add_header("Authorization", auth)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 3:
                retry_after = int(e.headers.get('Retry-After', 60))
                time.sleep(min(retry_after + 5, 900))
                return self._oauth_delete(url, _retries=_retries + 1)
            raise Exception(f"X API error {e.code}: {error_body}")


def _fill_from_broker(prefix: str) -> None:
    """Missing credential values come from `creds get`, never from a file search.

    Values stay in this process: set in os.environ for XPoster, never printed.
    """
    for suffix in ("API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_TOKEN_SECRET"):
        var = f"{prefix}{suffix}"
        if os.environ.get(var):
            continue
        r = subprocess.run(["creds", "get", var, "--consumer", "x-poster"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise KeyError(f"{var}: creds get failed (exit {r.returncode})")
        os.environ[var] = r.stdout.strip()


def _norm(text: str) -> str:
    """Post text with links (X rewrites them, bare domains too) made comparable."""
    text = re.sub(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)?", "<link>", text)
    return re.sub(r"( <link>)+$", "", " ".join(text.split()))


def _already_posted(poster, me: dict, text: str):
    """The id of a recent post of this account with the same text, or None.

    A retried task once posted the same post three times (2026-10-29): its
    check failed after each post and each retry posted again. Posting cannot
    be undone, so the poster itself refuses a repeat.
    """
    r = poster._oauth_get(f"https://api.x.com/2/users/{me.get('id')}/tweets?max_results=20")
    recent = r.get("data") if isinstance(r.get("data"), list) else []
    want = _norm(text)
    return next((t.get("id") for t in recent if _norm(t.get("text", "")) == want), None)


def main(argv=None) -> int:
    """Run the poster as a script, so the agent guard sees a post as one.

        x_poster.py --account datafund --whoami
        x_poster.py --account datafund --recent 10
        x_poster.py --account datafund --post-file post.txt
        x_poster.py --account datafund --delete POST_ID

    Exit 3: the post's text is already among the account's recent posts;
    nothing posted, the existing URL printed.
    """
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Post to X as one configured account.")
    ap.add_argument("--account", required=True, help=f"one of {', '.join(ACCOUNT_PREFIXES)}")
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--whoami", action="store_true", help="print the account's handle (read-only)")
    act.add_argument("--recent", type=int, metavar="N", help="print the account's last N posts (read-only)")
    act.add_argument("--post-file", type=Path, metavar="FILE", help="post FILE's text as one post")
    act.add_argument("--delete", metavar="POST_ID", help="delete one of this account's own posts")
    ap.add_argument("--media", action="append", type=Path, metavar="IMAGE",
                    help="attach IMAGE (or one .mp4 video) to the --post-file post (repeatable)")
    ap.add_argument("--state-dir", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    media = a.media or []
    if media and a.post_file is None:
        print("--media needs --post-file", file=sys.stderr)
        return 2
    missing = [str(m) for m in media if not m.is_file()]
    if missing:
        print(f"media not found: {', '.join(missing)}; nothing posted", file=sys.stderr)
        return 2

    prefix = ACCOUNT_PREFIXES.get(a.account)
    if not prefix:
        print(f"unknown account {a.account!r}; known: {', '.join(ACCOUNT_PREFIXES)}", file=sys.stderr)
        return 2
    text = None
    if a.post_file is not None:
        text = a.post_file.read_text().strip()
        if not text:
            print(f"{a.post_file}: empty, nothing posted", file=sys.stderr)
            return 2
    try:
        _fill_from_broker(prefix)
        poster = XPoster(account=a.account, state_dir=a.state_dir)
        me = poster._oauth_get("https://api.x.com/2/users/me").get("data") or {}
        handle = me.get("username", "?")
        if a.whoami:
            print(f"@{handle}")
        elif a.recent:
            n = max(5, min(a.recent, 100))
            r = poster._oauth_get(f"https://api.x.com/2/users/{me.get('id')}/tweets"
                                  f"?max_results={n}&tweet.fields=created_at")
            for t in (r.get("data") or [])[:a.recent]:
                print(f"{t.get('created_at', '')}  https://x.com/{handle}/status/{t.get('id')}")
                print("  " + t.get("text", "").replace("\n", "\n  "))
        elif a.delete:
            author = (poster.get_tweet(a.delete, fields="author_id").get("data") or {}).get("author_id")
            if author != me.get("id"):
                print(f"post {a.delete} is not by @{handle}; nothing deleted", file=sys.stderr)
                return 1
            poster._oauth_delete(f"https://api.x.com/2/tweets/{a.delete}")
            print(f"deleted: {a.delete}")
        elif (dup := _already_posted(poster, me, text)):
            print(f"already posted: https://x.com/{handle}/status/{dup}")
            return 3
        elif media:
            r = poster.post_with_media(text, [poster.upload_video(str(m)) if m.suffix.lower() == ".mp4"
                                              else poster.upload_media(str(m)) for m in media])
            print(f"posted: https://x.com/{handle}/status/{(r.get('data') or {}).get('id')}")
        else:
            r = poster.post(text)
            print(f"posted: https://x.com/{handle}/status/{(r.get('data') or {}).get('id')}")
    except Exception as exc:  # noqa: BLE001 -- reported, exit non-zero
        print(f"x_poster: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
