#!/usr/bin/env python3
"""Upload videos to the PLUR and Datafund YouTube channels (YouTube Data API v3).

One Google login, several brand-account channels. Each channel has its own
refresh token in the credential broker; the OAuth client (a Desktop-app client
JSON) is shared.

    youtube_upload.py auth     --channel plur --expect-title "plur"
    youtube_upload.py whoami   --channel plur
    youtube_upload.py upload   --channel plur --file v.mp4 --meta v.yaml \
                               [--thumbnail t.png] [--playlist "Name"] [--short]
    youtube_upload.py playlist-add --channel plur --video ID --playlist "Name"
    youtube_upload.py list     --channel plur
    youtube_upload.py delete   --channel plur --video ID --confirm ID

Guards (tests/test_youtube_upload.py):
- uploads and new playlists are PRIVATE unless --approved "<the owner's yes>"
  is given; the note is written to the upload log;
- metadata YouTube would reject is refused before any bytes are sent;
- the token must belong to the channel recorded for that name, so a wrong pick
  in Google's brand-account chooser cannot upload to the other brand;
- tokens never appear on a command line, in output or in the log.

Credentials (broker ids; values only via `creds get`):
  youtube-oauth-client      YOUTUBE_OAUTH_CLIENT_JSON       (owner adds once)
  youtube-refresh-<name>    YOUTUBE_REFRESH_TOKEN_<NAME>    (written by `auth`)

Channel ids are recorded in the comms module data folder (comms_paths), not
in this module: youtube_channels.yaml.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
CORE_LIB = Path(os.environ.get("DATACORE_LIB") or LIB.parent.parent.parent / "lib")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # playlists, thumbnails, delete
]
CLIENT_CRED = "youtube-oauth-client"
CONSUMER = "youtube_upload"
CHANNEL_NAMES = ("plur", "datafund")
DEFAULT_CATEGORY = "28"  # Science & Technology
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500


class Refused(Exception):
    """A guard said no. The message says why and what to do."""


# ---------------------------------------------------------------- credentials

def _refresh_id(channel: str) -> str:
    return f"youtube-refresh-{channel}"


def _refresh_var(channel: str) -> str:
    return f"YOUTUBE_REFRESH_TOKEN_{channel.upper()}"


def read_credential(cred_id: str) -> str:
    """Value from the broker. Captured, never printed; nothing secret on argv."""
    creds_bin = shutil.which("creds") or str(CORE_LIB.parent / "bin" / "creds")
    argv = ["creds", "get", cred_id, "--consumer", CONSUMER]
    if creds_bin and not shutil.which("creds"):
        argv[0] = creds_bin
    r = subprocess.run(argv, capture_output=True, text=True)
    value = (r.stdout or "").strip()
    if r.returncode != 0 or not value:
        raise Refused(
            f"the broker did not serve {cred_id}. If it was never added: "
            f"`creds add {cred_id} ...` then `creds sync`. Do not look for it on disk.")
    return value


def _credential_manager():
    if str(CORE_LIB) not in sys.path:
        sys.path.insert(0, str(CORE_LIB))
    from creds import CredentialManager  # noqa: PLC0415
    return CredentialManager()


def credential_exists(cred_id: str) -> bool:
    mgr = _credential_manager()
    index = yaml.safe_load(mgr.index_path.read_text()) or {}
    return any(isinstance(r, dict) and r.get("id") == cred_id
               for r in index.get("credentials") or [])


def save_refresh_token(channel: str, token: str) -> None:
    """Write the token in-process (never argv), commit and sync via the broker."""
    cred_id = _refresh_id(channel)
    mgr = _credential_manager()
    exists = credential_exists(cred_id)
    rc = mgr.cmd_add(
        cred_id,
        var_name=_refresh_var(channel),
        value=token,
        scope="global",
        tier="high",
        category="social",
        provider="google",
        description=f"YouTube refresh token for the {channel} brand channel "
                    f"(youtube_upload.py auth)",
        overwrite=exists,
    )
    if rc != 0:
        # cmd_add also fails when the value was stored and committed but the
        # sync/push to other hosts failed (diverged secrets repo, 2026-11-02).
        # If this host now serves exactly the new token, it is saved here.
        try:
            served = read_credential(cred_id)
        except Refused:
            served = None
        if served != token:
            raise Refused(f"the broker refused to store {cred_id} (exit {rc}); "
                          f"nothing else was changed")
        print(f"WARNING: {cred_id} is saved and served on this host, but it did not "
              f"reach the other hosts (the credential sync/push failed). Fix the sync "
              f"and run `creds sync`; until then other hosts (e.g. Data) lack it.",
              file=sys.stderr)


# ---------------------------------------------------------------- config / log

def _data_dir() -> Path:
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from comms_paths import data_dir  # noqa: PLC0415
    return data_dir()


def _channels_path() -> Path:
    return _data_dir() / "youtube_channels.yaml"


def load_channels() -> dict:
    p = _channels_path()
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def record_channel(name: str, cid: str, title: str) -> None:
    data = load_channels()
    for other, row in data.items():
        if other != name and isinstance(row, dict) and row.get("channel_id") == cid:
            raise Refused(f"channel {title} ({cid}) is already recorded as '{other}'. "
                          f"Sign in again and pick the {name} channel in Google's chooser.")
    data[name] = {"channel_id": cid, "title": title,
                  "recorded": _dt.date.today().isoformat()}
    _channels_path().write_text(yaml.safe_dump(data, sort_keys=True))


def _log_path() -> Path:
    return _data_dir() / "youtube-uploads.jsonl"


def log_event(event: dict) -> None:
    event = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **event}
    with _log_path().open("a") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- metadata

def load_meta(path) -> dict:
    meta = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(meta, dict):
        raise Refused(f"{path}: expected a mapping with title/description/tags")
    return meta


def _privacy(privacy: str | None, approved: str | None) -> str:
    privacy = (privacy or "private").lower()
    if privacy not in ("private", "unlisted", "public"):
        raise Refused(f"unknown privacy {privacy!r}")
    if privacy != "private" and not (approved or "").strip():
        raise Refused(f"{privacy} needs the owner's yes for this item: pass "
                      f"--approved \"<who said yes, when>\". Default is private.")
    return privacy


def build_body(meta: dict, privacy: str | None, approved: str | None) -> dict:
    title = str(meta.get("title") or "").strip()
    description = str(meta.get("description") or "")
    tags = [str(t).strip() for t in meta.get("tags") or [] if str(t).strip()]
    if not title:
        raise Refused("title is empty")
    if len(title) > TITLE_MAX:
        raise Refused(f"title is {len(title)} characters; YouTube allows {TITLE_MAX}")
    if len(description.encode("utf-8")) > DESCRIPTION_MAX:
        raise Refused(f"description is over {DESCRIPTION_MAX} bytes")
    for field, text in (("title", title), ("description", description)):
        if "<" in text or ">" in text:
            raise Refused(f"{field} contains angle brackets (< or >), which YouTube "
                          f"rejects; write e.g. REFERENCE instead of <reference>")
    # YouTube counts a tag with a space as if quoted (+2) plus a comma between tags.
    tag_len = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)
    if tag_len > TAGS_MAX:
        raise Refused(f"tags total {tag_len} characters; YouTube allows {TAGS_MAX}")
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(meta.get("category") or DEFAULT_CATEGORY),
            "defaultLanguage": meta.get("language") or "en",
            "defaultAudioLanguage": meta.get("language") or "en",
        },
        "status": {
            "privacyStatus": _privacy(privacy, approved),
            "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
            "embeddable": True,
        },
    }


def check_short(path) -> None:
    """A Short must be vertical or square and at most 3 minutes."""
    if not shutil.which("ffprobe"):
        raise Refused("ffprobe is not installed, so a Short cannot be checked")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    w, h = info["streams"][0]["width"], info["streams"][0]["height"]
    dur = float(info["format"]["duration"])
    if w > h:
        raise Refused(f"{Path(path).name} is {w}x{h} (landscape); YouTube only treats "
                      f"vertical or square videos as Shorts. Render a vertical version.")
    if dur > 180:
        raise Refused(f"{Path(path).name} is {dur:.0f}s; Shorts are at most 180s")


def check_delete_confirmation(video_id: str, confirm: str | None) -> None:
    if not confirm or confirm != video_id:
        raise Refused("delete needs --confirm with the same video id, typed again")


# ---------------------------------------------------------------- Google

def _client_config() -> dict:
    raw = read_credential(CLIENT_CRED)
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        raise Refused(f"{CLIENT_CRED} is not valid JSON; re-add the downloaded "
                      f"client file's single line with `creds add --overwrite`") from None
    if "installed" not in cfg:
        raise Refused(f"{CLIENT_CRED} is not a Desktop-app client (no 'installed' key)")
    return cfg


def service_for(channel: str):
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415
    inst = _client_config()["installed"]
    creds = Credentials(
        None, refresh_token=read_credential(_refresh_id(channel)),
        token_uri=inst.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=inst["client_id"], client_secret=inst["client_secret"], scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def check_channel(svc, name: str, channels: dict) -> tuple[str, str]:
    expected = (channels.get(name) or {}).get("channel_id")
    if not expected:
        raise Refused(f"no channel_id recorded for '{name}'; run "
                      f"`youtube_upload.py auth --channel {name} --expect-title ...` first")
    items = svc.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        raise Refused("this token has no YouTube channel")
    cid, title = items[0]["id"], items[0]["snippet"]["title"]
    if cid != expected:
        raise Refused(f"token for '{name}' belongs to {title} ({cid}), expected "
                      f"{expected}. Nothing was uploaded.")
    return cid, title


def _ready(name: str):
    if name not in CHANNEL_NAMES:
        raise Refused(f"unknown channel {name!r}; known: {', '.join(CHANNEL_NAMES)}")
    svc = service_for(name)
    check_channel(svc, name, load_channels())
    return svc


def find_or_create_playlist(svc, title: str, privacy: str | None, approved: str | None) -> str:
    req = svc.playlists().list(part="snippet", mine=True, maxResults=50)
    while req is not None:
        resp = req.execute()
        for it in resp.get("items", []):
            if it["snippet"]["title"] == title:
                return it["id"]
        req = svc.playlists().list_next(req, resp)
    body = {"snippet": {"title": title},
            "status": {"privacyStatus": _privacy(privacy, approved)}}
    pid = svc.playlists().insert(part="snippet,status", body=body).execute()["id"]
    print(f"created playlist {title!r} ({body['status']['privacyStatus']}): {pid}")
    return pid


def add_to_playlist(svc, video_id: str, playlist_id: str) -> None:
    svc.playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": playlist_id,
        "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()


# ---------------------------------------------------------------- commands

def cmd_auth(a) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415
    if a.channel not in CHANNEL_NAMES:
        raise Refused(f"unknown channel {a.channel!r}")
    known = (load_channels().get(a.channel) or {}).get("channel_id")
    if not known and not a.expect_title:
        raise Refused("first sign-in for this channel: pass --expect-title with part of "
                      "the channel's name, so a wrong pick in Google's chooser is caught")
    print(f"Opening the browser. Sign in as the owner, then pick the "
          f"{a.channel.upper()} channel in the account chooser.", file=sys.stderr)
    flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  open_browser=True)
    if not creds.refresh_token:
        raise Refused("Google returned no refresh token; revoke the app's access at "
                      "myaccount.google.com/permissions and sign in again")
    svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = svc.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        raise Refused("the account you picked has no YouTube channel")
    cid, title = items[0]["id"], items[0]["snippet"]["title"]
    if known and cid != known:
        raise Refused(f"you picked {title} ({cid}); '{a.channel}' is {known}. Not saved.")
    if not known and a.expect_title.lower() not in title.lower():
        raise Refused(f"you picked {title} ({cid}), which does not match "
                      f"{a.expect_title!r}. Not saved.")
    if not known:
        record_channel(a.channel, cid, title)
    save_refresh_token(a.channel, creds.refresh_token)
    log_event({"action": "auth", "channel": a.channel, "channel_id": cid})
    print(f"{a.channel}: signed in as {title} ({cid}); refresh token stored in the broker")
    return 0


def cmd_whoami(a) -> int:
    svc = service_for(a.channel)
    cid, title = check_channel(svc, a.channel, load_channels())
    print(f"{a.channel}: {title} ({cid})")
    return 0


def cmd_upload(a) -> int:
    meta = load_meta(a.meta) if a.meta else {}
    for key in ("title", "description"):
        if getattr(a, key):
            meta[key] = getattr(a, key)
    if a.tags:
        meta["tags"] = [t.strip() for t in a.tags.split(",")]
    if meta.get("channel") and meta["channel"] != a.channel:
        raise Refused(f"{a.meta} is written for channel {meta['channel']!r}, not {a.channel!r}")
    a.playlist = a.playlist or meta.get("playlist")
    a.thumbnail = a.thumbnail or meta.get("thumbnail")
    body = build_body(meta, a.privacy, a.approved)
    path = Path(a.file)
    if not path.is_file():
        raise Refused(f"no such file: {path}")
    if a.short:
        check_short(path)
    if a.thumbnail and not Path(a.thumbnail).is_file():
        raise Refused(f"no such thumbnail: {a.thumbnail}")
    svc = _ready(a.channel)
    if a.dry_run:
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print(f"dry run: would upload {path.name} to {a.channel}", file=sys.stderr)
        return 0
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415
    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"  {int(status.progress() * 100)}%", file=sys.stderr)
    vid = resp["id"]
    print(f"uploaded {path.name} -> https://youtu.be/{vid} "
          f"({resp['status']['privacyStatus']})")
    event = {"action": "upload", "channel": a.channel, "video_id": vid,
             "file": str(path), "title": body["snippet"]["title"],
             "privacy": resp["status"]["privacyStatus"], "approved": a.approved}
    if a.thumbnail:
        try:
            svc.thumbnails().set(videoId=vid, media_body=MediaFileUpload(a.thumbnail)).execute()
            print("  thumbnail set")
        except Exception as exc:  # noqa: BLE001 -- the video is up; report, don't fail it
            print(f"  thumbnail NOT set ({type(exc).__name__}: {str(exc)[:160]}). "
                  f"Custom thumbnails need a phone-verified channel.", file=sys.stderr)
            event["thumbnail_error"] = type(exc).__name__
    if a.playlist:
        pid = find_or_create_playlist(svc, a.playlist, a.playlist_privacy, a.approved)
        add_to_playlist(svc, vid, pid)
        print(f"  added to playlist {a.playlist!r}")
        event["playlist_id"] = pid
    log_event(event)
    return 0


def cmd_playlist_add(a) -> int:
    svc = _ready(a.channel)
    pid = find_or_create_playlist(svc, a.playlist, a.playlist_privacy, a.approved)
    add_to_playlist(svc, a.video, pid)
    log_event({"action": "playlist-add", "channel": a.channel, "video_id": a.video,
               "playlist_id": pid})
    print(f"added {a.video} to {a.playlist!r}")
    return 0


def cmd_list(a) -> int:
    svc = _ready(a.channel)
    ch = svc.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    resp = svc.playlistItems().list(part="snippet,status", playlistId=uploads,
                                    maxResults=a.limit).execute()
    for it in resp.get("items", []):
        s = it["snippet"]
        print(f"{s['resourceId']['videoId']}  {s['publishedAt'][:10]}  "
              f"{it.get('status', {}).get('privacyStatus', '?'):8}  {s['title']}")
    return 0


def cmd_delete(a) -> int:
    check_delete_confirmation(a.video, a.confirm)
    svc = _ready(a.channel)
    svc.videos().delete(id=a.video).execute()
    log_event({"action": "delete", "channel": a.channel, "video_id": a.video})
    print(f"deleted {a.video} from {a.channel}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def chan(sp):
        sp.add_argument("--channel", required=True, choices=CHANNEL_NAMES)

    s = sub.add_parser("auth", help="one-time browser sign-in for a channel")
    chan(s)
    s.add_argument("--expect-title", help="part of the channel name (first sign-in)")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("whoami", help="which channel the stored token belongs to")
    chan(s)
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("upload", help="upload one video (private by default)")
    chan(s)
    s.add_argument("--file", required=True)
    s.add_argument("--meta", help="YAML with title, description, tags[, category]")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--tags", help="comma-separated")
    s.add_argument("--thumbnail")
    s.add_argument("--playlist", help="playlist title; created private if missing")
    s.add_argument("--playlist-privacy", default="private")
    s.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    s.add_argument("--approved", help="the owner's yes for a non-private item")
    s.add_argument("--short", action="store_true", help="check it qualifies as a Short")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser("playlist-add", help="add an uploaded video to a playlist")
    chan(s)
    s.add_argument("--video", required=True)
    s.add_argument("--playlist", required=True)
    s.add_argument("--playlist-privacy", default="private")
    s.add_argument("--approved")
    s.set_defaults(fn=cmd_playlist_add)

    s = sub.add_parser("list", help="recent uploads")
    chan(s)
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("delete", help="delete one video (needs --confirm ID)")
    chan(s)
    s.add_argument("--video", required=True)
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
