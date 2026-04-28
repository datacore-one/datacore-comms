#!/usr/bin/env python3
"""Reddit scanner — mine trending posts for content ideas.

Uses Reddit's public .json endpoints (no auth needed).
Three tiers of subreddits: domain signal, adjacent interesting, pure engagement.

Usage:
    python3 reddit_scanner.py                    # Scan all tiers
    python3 reddit_scanner.py --tier domain      # Domain signal only
    python3 reddit_scanner.py --account fds      # FDS subreddits
    python3 reddit_scanner.py --account plur     # PLUR subreddits
    python3 reddit_scanner.py --json             # JSON output
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional

USER_AGENT = "datacore-content-engine:v1.0 (by /u/datacorebot)"
REQUEST_DELAY = 3.0  # seconds between requests — Reddit rate limits at ~30 req/min

# Reddit OAuth app credentials (script type — free, no user auth needed)
# Create at: https://www.reddit.com/prefs/apps → "script" type
# Set env vars: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET
_reddit_token = None
_reddit_token_expires = 0

# ── Subreddit Configuration ─────────────────────────────────────────────────

# Tier 1: Domain signal — drives original posts with brand angle
DOMAIN_SUBS = {
    "fds": [
        "selfhosted", "opensource", "decentralization",
        "DataHoarder", "homelab", "ethswarm",
        "degoogle", "floss", "linux",
    ],
    "plur": [
        "ClaudeAI", "LocalLLaMA", "MachineLearning",
        "cognitivescience", "neuroscience", "memory",
        "LanguageTechnology", "learnmachinelearning",
    ],
    "shared": [
        "technology", "Futurology",
    ],
}

# Tier 2: Adjacent interesting — shows you're paying attention to the bigger picture
ADJACENT_SUBS = {
    "fds": [
        "web3", "CryptoCurrency", "ethereum",
        "Entrepreneur", "startups", "cooperative",
        "solarpunk", "RightToRepair",
    ],
    "plur": [
        "ExperiencedDevs", "softwarearchitecture",
        "productivityapps", "Zettelkasten", "ObsidianMD",
        "spacedrepetition", "Anki",
        "startups", "SideProject",
    ],
    "shared": [
        "datascience", "programming", "webdev",
    ],
}

# Tier 3: Pure engagement — cool stuff, personality posts (2-3x/week)
ENGAGEMENT_SUBS = [
    "dataisbeautiful", "todayilearned",
    "explainlikeimfive", "AskScience", "space",
    "interestingasfuck", "coolguides",
    "DesignPorn", "mechanical_gifs",
    "GetMotivated", "DecidingToBeBetter",
]

# Minimum score thresholds per tier (filters noise)
SCORE_THRESHOLDS = {
    "domain": 20,
    "adjacent": 50,
    "engagement": 200,
}

# Max posts to return per subreddit
MAX_PER_SUB = 5


def _get_reddit_token() -> Optional[str]:
    """Get Reddit OAuth token for authenticated API access."""
    global _reddit_token, _reddit_token_expires

    if _reddit_token and time.time() < _reddit_token_expires:
        return _reddit_token

    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    try:
        import base64
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            _reddit_token = result.get("access_token")
            _reddit_token_expires = time.time() + result.get("expires_in", 3600) - 60
            return _reddit_token
    except Exception as e:
        print(f"  Reddit OAuth failed: {e}", file=sys.stderr)
        return None


def _fetch_reddit(sub: str, sort: str = "hot", limit: int = 10) -> List[Dict]:
    """Fetch posts from Reddit. Tries RSS (works everywhere), then OAuth API, then JSON."""

    # Primary: RSS feed — works from servers, no auth needed
    posts = _fetch_via_rss(sub, sort, limit)
    if posts:
        return posts

    # Fallback: OAuth API
    token = _get_reddit_token()
    if token:
        url = f"https://oauth.reddit.com/r/{sub}/{sort}?limit={limit}&raw_json=1"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    else:
        url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}&raw_json=1"
        headers = {"User-Agent": USER_AGENT}

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            posts = []
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                if p.get("stickied"):
                    continue
                posts.append({
                    "id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "selftext": (p.get("selftext") or "")[:500],
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "url": p.get("url", ""),
                    "permalink": f"https://reddit.com{p.get('permalink', '')}",
                    "subreddit": sub,
                    "author": p.get("author", ""),
                    "created_utc": p.get("created_utc", 0),
                    "upvote_ratio": p.get("upvote_ratio", 0),
                })
            return posts
    except Exception as e:
        print(f"  Failed to fetch r/{sub}: {e}", file=sys.stderr)
        return []


def _fetch_via_rss(sub: str, sort: str = "hot", limit: int = 10) -> List[Dict]:
    """Fetch posts via RSS/Atom feed. Works from any IP, no auth needed."""
    import xml.etree.ElementTree as ET

    url = f"https://www.reddit.com/r/{sub}/{sort}.rss"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        posts = []

        for entry in entries[:limit]:
            title = entry.findtext("atom:title", "", ns)
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            author_el = entry.find("atom:author/atom:name", ns)
            author = author_el.text if author_el is not None else ""
            updated = entry.findtext("atom:updated", "", ns)
            content = entry.findtext("atom:content", "", ns)

            # Parse timestamp
            created_utc = 0
            if updated:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    created_utc = dt.timestamp()
                except Exception:
                    pass

            # Extract text snippet from HTML content
            selftext = ""
            if content:
                import re
                # Strip HTML tags for a text preview
                text = re.sub(r'<[^>]+>', ' ', content)
                text = re.sub(r'\s+', ' ', text).strip()
                selftext = text[:500]

            # Skip stickied/megathread posts
            if any(kw in title.lower() for kw in ["megathread", "weekly", "daily thread", "monthly"]):
                continue

            posts.append({
                "id": link.split("/comments/")[1].split("/")[0] if "/comments/" in link else "",
                "title": title,
                "selftext": selftext,
                "score": 0,  # RSS doesn't include scores
                "num_comments": 0,
                "url": link,
                "permalink": link,
                "subreddit": sub,
                "author": author.replace("/u/", ""),
                "created_utc": created_utc,
                "upvote_ratio": 0,
                "source": "rss",
            })

        return posts
    except Exception as e:
        print(f"  RSS failed for r/{sub}: {e}", file=sys.stderr)
        return []


def scan_tier(tier: str, account: str = "shared",
              max_age_hours: int = 48) -> List[Dict]:
    """Scan a tier of subreddits and return trending posts."""
    if tier == "domain":
        subs = DOMAIN_SUBS.get(account, []) + DOMAIN_SUBS.get("shared", [])
    elif tier == "adjacent":
        subs = ADJACENT_SUBS.get(account, []) + ADJACENT_SUBS.get("shared", [])
    elif tier == "engagement":
        subs = ENGAGEMENT_SUBS
    else:
        subs = []

    threshold = SCORE_THRESHOLDS.get(tier, 50)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    results = []

    for sub in subs:
        posts = _fetch_reddit(sub, sort="hot", limit=MAX_PER_SUB + 5)
        time.sleep(REQUEST_DELAY)

        for post in posts:
            if post["created_utc"]:
                created = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc)
                if created < cutoff:
                    continue
            # RSS doesn't include scores — skip threshold for RSS-sourced posts
            if post.get("source") != "rss" and post["score"] < threshold:
                continue
            post["tier"] = tier
            post["age_hours"] = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            results.append(post)

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:MAX_PER_SUB * len(subs)]


def scan_all(account: str = "shared", max_age_hours: int = 48) -> Dict[str, List[Dict]]:
    """Scan all tiers and return organized results."""
    return {
        "domain": scan_tier("domain", account, max_age_hours),
        "adjacent": scan_tier("adjacent", account, max_age_hours),
        "engagement": scan_tier("engagement", account, max_age_hours),
    }


def format_summary(results: Dict[str, List[Dict]]) -> str:
    """Format scan results as readable markdown."""
    lines = []
    total = sum(len(v) for v in results.values())
    lines.append(f"Reddit scan: {total} trending posts\n")

    for tier, posts in results.items():
        if not posts:
            continue
        lines.append(f"### {tier.title()} ({len(posts)} posts)")
        for p in posts[:8]:
            age = f"{p['age_hours']:.0f}h"
            lines.append(
                f"- [{p['score']:,} pts, {age}] r/{p['subreddit']}: "
                f"{p['title'][:80]}"
            )
        lines.append("")

    return "\n".join(lines)


def save_results(results: Dict[str, List[Dict]], output_dir: Path):
    """Save scan results to JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_file = output_dir / f"reddit-scan-{today}.json"

    output = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "stats": {
            tier: len(posts) for tier, posts in results.items()
        },
    }

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    return output_file


def scan_via_exa(account: str, max_results: int = 10) -> List[Dict]:
    """Fallback: use Exa to search Reddit content when direct access is blocked.

    Uses 1 Exa query with includeDomains=reddit.com — budget-friendly fallback.
    """
    exa_key = os.environ.get("EXA_API_KEY", "")
    if not exa_key:
        return []

    subs = DOMAIN_SUBS.get(account, []) + DOMAIN_SUBS.get("shared", [])
    # Build topic-focused query from subreddit themes
    topics = " OR ".join(s for s in subs[:5])

    try:
        payload = json.dumps({
            "query": f"{topics} discussion trending",
            "numResults": max_results,
            "startPublishedDate": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "includeDomains": ["reddit.com"],
            "type": "neural",
            "contents": {"text": {"maxCharacters": 300}},
        }).encode()

        req = urllib.request.Request(
            "https://api.exa.ai/search",
            data=payload,
            headers={
                "x-api-key": exa_key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            results = data.get("results", [])
            posts = []
            for r in results:
                url = r.get("url", "")
                # Extract subreddit from URL
                sub = "unknown"
                if "/r/" in url:
                    sub = url.split("/r/")[1].split("/")[0]

                posts.append({
                    "id": r.get("id", ""),
                    "title": r.get("title", ""),
                    "selftext": (r.get("text") or "")[:300],
                    "score": 0,  # Exa doesn't return scores
                    "url": url,
                    "permalink": url,
                    "subreddit": sub,
                    "tier": "domain",
                    "source": "exa",
                    "age_hours": 0,
                })
            return posts
    except Exception as e:
        print(f"  Exa Reddit scan failed: {e}", file=sys.stderr)
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Reddit Scanner")
    parser.add_argument("--tier", choices=["domain", "adjacent", "engagement"],
                        help="Scan specific tier only")
    parser.add_argument("--account", default="shared",
                        choices=["fds", "plur", "shared"],
                        help="Account-specific subreddits")
    parser.add_argument("--hours", type=int, default=48,
                        help="Max age of posts in hours")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--save", type=str, help="Save results to directory")
    args = parser.parse_args()

    if args.tier:
        results = {args.tier: scan_tier(args.tier, args.account, args.hours)}
    else:
        results = scan_all(args.account, args.hours)

    if args.save:
        path = save_results(results, Path(args.save))
        print(f"Saved to {path}")

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_summary(results))


if __name__ == "__main__":
    main()
