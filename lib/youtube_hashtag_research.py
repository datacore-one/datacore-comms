#!/usr/bin/env python3
"""Hashtag research on YouTube before choosing a video's hashtags.

For each candidate hashtag: how many recent videos use it (YouTube's own
estimate) and how many views the top recent ones got. For each seed topic:
which hashtags the most-watched recent videos on that topic actually carry.

    youtube_hashtag_research.py --channel plur \
        --tags "#AIAgents,#AIMemory,#MCP" --seeds "AI agent memory,claude code memory" \
        [--days 365] [--out report.md]

Read-only. Each search costs 100 units of the project's daily YouTube quota
(10,000 by default), so ~20 tags and seeds use about a fifth of it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_upload import service_for  # noqa: E402

HASHTAG = re.compile(r"#([A-Za-z][A-Za-z0-9_]{1,40})")


def top_videos(svc, q: str, after: str, n: int = 25) -> tuple[int, list[dict]]:
    r = svc.search().list(part="id", q=q, type="video", order="viewCount",
                          publishedAfter=after, maxResults=n).execute()
    ids = [it["id"]["videoId"] for it in r.get("items", [])]
    total = r.get("pageInfo", {}).get("totalResults", 0)
    if not ids:
        return total, []
    v = svc.videos().list(part="snippet,statistics", id=",".join(ids)).execute()
    return total, v.get("items", [])


def views(items) -> list[int]:
    return [int(it.get("statistics", {}).get("viewCount", 0)) for it in items]


def tags_in(items) -> Counter:
    c: Counter = Counter()
    for it in items:
        sn = it.get("snippet", {})
        found = {t.lower() for t in HASHTAG.findall(sn.get("title", "") + " " + sn.get("description", ""))}
        c.update(found)
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="plur")
    ap.add_argument("--tags", default="")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    svc = service_for(a.channel)
    after = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"# YouTube hashtag research ({_dt.date.today()}, videos from the last {a.days} days)", "",
             "| Hashtag | Videos using it (YouTube estimate) | Median views, top 25 | Top video views |",
             "|---|---|---|---|"]
    for tag in [t.strip() for t in a.tags.split(",") if t.strip()]:
        total, items = top_videos(svc, tag, after)
        vs = views(items)
        lines.append(f"| {tag} | {total:,} | {int(statistics.median(vs)) if vs else 0:,} | {max(vs) if vs else 0:,} |")
    for seed in [s.strip() for s in a.seeds.split(",") if s.strip()]:
        total, items = top_videos(svc, seed, after)
        common = ", ".join(f"#{t} ({n})" for t, n in tags_in(items).most_common(15)) or "none"
        lines += ["", f"## Seed: {seed}", f"Hashtags on the 25 most-watched recent videos (how many carry it): {common}"]
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
