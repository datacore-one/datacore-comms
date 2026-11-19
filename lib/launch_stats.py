#!/usr/bin/env python3
"""Read-only reach numbers for published posts: YouTube videos, X posts, LinkedIn posts.

    launch_stats.py --youtube plur:c6a7ALX2MwI --x plur_ai:2123032755613151497 \
                    --linkedin plur:urn:li:ugcPost:7528795369745600512

Each flag repeats. Nothing is written or posted. A platform that refuses the read
(missing scope, expired sign-in) is reported as "not readable" with its reason,
never as zero.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def yt_row(item: dict) -> dict:
    s = item.get("statistics") or {}
    return {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0))}


def x_row(data: dict) -> dict:
    pub = data.get("public_metrics") or {}
    priv = data.get("non_public_metrics") or {}
    return {"views": int(priv.get("impression_count", pub.get("impression_count", 0))),
            "likes": int(pub.get("like_count", 0)), "reposts": int(pub.get("retweet_count", 0)),
            "replies": int(pub.get("reply_count", 0)), "bookmarks": int(pub.get("bookmark_count", 0))}


def li_row(summary: dict) -> dict:
    likes = (summary.get("likesSummary") or {}).get("totalLikes", 0)
    comments = (summary.get("commentsSummary") or {}).get("aggregatedTotalComments", 0)
    return {"likes": int(likes), "comments": int(comments)}


def _split(spec: str) -> tuple[str, str]:
    who, _, ref = spec.partition(":")
    if not ref:
        raise SystemExit(f"expected account:id, got {spec!r}")
    return who, ref


def youtube(spec: str) -> dict:
    import youtube_upload as yu  # noqa: PLC0415
    ch, vid = _split(spec)
    items = yu.service_for(ch).videos().list(part="statistics", id=vid).execute().get("items", [])
    return yt_row(items[0]) if items else {"error": "video not found"}


def x(spec: str) -> dict:
    import os, shutil, subprocess  # noqa: E401, PLC0415
    from x_poster import XPoster  # noqa: PLC0415
    acct, pid = _split(spec)
    creds_bin = shutil.which("creds") or str(LIB.parents[2] / "bin" / "creds")
    for suffix in ("API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_TOKEN_SECRET"):
        var = f"{acct.upper()}_X_{suffix}"
        if not os.environ.get(var):  # served by the broker in-process, never printed
            r = subprocess.run([creds_bin, "get", var, "--consumer", "launch_stats"],
                               capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                os.environ[var] = r.stdout.strip()
    r = XPoster(acct).get_tweet(pid, fields="public_metrics,non_public_metrics")
    if "data" not in r:
        r = XPoster(acct).get_tweet(pid, fields="public_metrics")
    return x_row(r.get("data") or {}) if r.get("data") else {"error": json.dumps(r)[:200]}


def linkedin(spec: str) -> dict:
    import linkedin_post as lp  # noqa: PLC0415
    author, urn = _split(spec)
    tok = lp.load_token(author)["access_token"]
    _, _, body = lp._send("GET", f"{lp.API}/rest/socialActions/{urllib.parse.quote(urn, safe='')}", tok)
    return li_row(body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--youtube", action="append", default=[])
    ap.add_argument("--x", action="append", default=[])
    ap.add_argument("--linkedin", action="append", default=[])
    a = ap.parse_args(argv)
    out = []
    for platform, fn, specs in (("youtube", youtube, a.youtube), ("x", x, a.x),
                                ("linkedin", linkedin, a.linkedin)):
        for spec in specs:
            try:
                row = fn(spec)
            except Exception as exc:  # noqa: BLE001 -- report the refusal, never a zero
                row = {"error": f"not readable: {str(exc)[:200]}"}
            out.append({"platform": platform, "ref": spec, **row})
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
