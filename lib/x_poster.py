# .datacore/modules/comms/lib/x_poster.py
"""Multi-account X poster with OAuth 1.0a signing and persistent rate limiting.

Supports: post, reply, quote RT, like, follow, unfollow.
Reusable across any space/brand — accounts configured via comms-config.yaml.

**Rate limits persist to SQLite** — survives systemd oneshot restarts.
**Kill switch** — checks for .datacore/state/campaign-kill-switch file.

Account credentials use env var prefix convention defined in comms-config.yaml:
  {PREFIX}API_KEY, {PREFIX}API_SECRET,
  {PREFIX}ACCESS_TOKEN, {PREFIX}ACCESS_TOKEN_SECRET

Usage:
    poster = XPoster(account='plur')  # loads config from active space
    poster.post("Privacy by architecture, not by promise.")
    poster.reply("Exactly.", tweet_id="123456")
    poster.like("123456")
    poster.follow("67890")
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TWEET_URL = "https://api.x.com/2/tweets"

# Kill switch — if this file exists, all posting is halted
KILL_SWITCH_PATH = Path(os.environ.get(
    "DATACORE_ROOT", os.path.expanduser("~/Data")
)) / ".datacore" / "state" / "campaign-kill-switch"


class KillSwitchActive(RuntimeError):
    """Raised when kill switch file exists."""
    pass


class XPoster:
    def __init__(self, account: str, daily_limit: int = None,
                 user_id: str = None, state_dir: str = None, config: dict = None):
        """Initialize poster for a given account.

        Args:
            account: Account key (must exist in comms-config.yaml accounts)
            daily_limit: Override daily post limit (default from config)
            user_id: Needed for like/follow endpoints
            state_dir: Where to store rate-limits.db
            config: Optional pre-loaded comms config dict
        """
        if config is None:
            from comms_config import load_config, get_account_credentials
            self.config = load_config()
            self._creds = get_account_credentials(account, self.config)
        else:
            from comms_config import get_account_credentials
            self.config = config
            self._creds = get_account_credentials(account, config)

        self.account = account
        self.api_key = self._creds["consumer_key"]
        self.api_secret = self._creds["consumer_secret"]
        self.access_token = self._creds["access_token"]
        self.access_token_secret = self._creds["access_token_secret"]
        self.user_id = user_id

        account_cfg = self.config.get("accounts", {}).get(account, {})

        # Validate credentials
        for key, val in self._creds.items():
            if not val:
                raise ValueError(
                    f"Missing credential for account '{account}': {key} "
                    f"(check env var with prefix {account_cfg.get('env_prefix', 'X_')})"
                )

        self.daily_limit = daily_limit or account_cfg.get("daily_limit", 50)

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

    # --- Public API ---

    def post(self, text: str) -> dict:
        """Post a standalone tweet."""
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {'text': text})
        self._increment_rate_count()
        return result

    def reply(self, text: str, reply_to_id: str) -> dict:
        """Reply to a tweet."""
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {
            'text': text,
            'reply': {'in_reply_to_tweet_id': reply_to_id},
        })
        self._increment_rate_count()
        return result

    def quote_rt(self, text: str, tweet_id: str) -> dict:
        """Quote retweet."""
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

    # --- OAuth 1.0a signing ---

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
