#!/usr/bin/env python3
"""Content-First Engagement Engine.

Replaces the old discovery-heavy engagement pipeline with a content-first approach:
1. Morning prep: scan Reddit + watchlist → generate content queue
2. Hourly distribution: match content to opportunities → post

Architecture:
  Old: 8 API reads/cycle → find tweets → generate reply → 4-lens eval → mostly reject
  New: morning scan → craft content → find places to post it → post

Usage:
    python3 content_engine.py prep --account fds       # Morning content prep
    python3 content_engine.py distribute --account fds  # Hourly distribution
    python3 content_engine.py prep --account plur
    python3 content_engine.py distribute --account plur
    python3 content_engine.py status --account fds      # Show today's queue
    python3 content_engine.py --dry-run prep --account fds
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Add module lib to path
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))

try:
    from env_utils import load_env_files
    load_env_files()
except ImportError:
    pass

import yaml

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
MODULE_DIR = LIB_DIR.parent
STATE_DIR = DATA_DIR / ".datacore" / "state"
COMMS_DATA = MODULE_DIR / "data"

# ── Configuration ────────────────────────────────────────────────────────────

MAX_POSTS_PER_DAY = 15          # Total posts across all types
MAX_POSTS_PER_CYCLE = 3         # Posts per distribution cycle
CONTENT_QUEUE_SIZE = 20         # Max items in daily content queue
WATCHLIST_CHECK_LIMIT = 10      # Recent tweets to check per watchlist account


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    """Load a YAML file, return empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_watchlist(account: str) -> List[Dict]:
    """Load watchlist for an account."""
    data = load_yaml(COMMS_DATA / f"watchlist-{account}.yaml")
    return data.get("accounts", [])


def load_angles(account: str) -> List[Dict]:
    """Load brand angles for an account."""
    data = load_yaml(COMMS_DATA / f"angles-{account}.yaml")
    return data.get("angles", [])


def load_content_queue(account: str) -> Dict:
    """Load today's content queue."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queue_file = STATE_DIR / f"content-queue-{account}-{today}.json"
    if queue_file.exists():
        with open(queue_file) as f:
            return json.load(f)
    return {"date": today, "account": account, "items": [], "posted": [], "skipped": []}


def save_content_queue(queue: Dict, account: str):
    """Save today's content queue."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queue_file = STATE_DIR / f"content-queue-{account}-{today}.json"
    with open(queue_file, "w") as f:
        json.dump(queue, f, indent=2, default=str)


# ── Phase 1: Morning Content Prep ────────────────────────────────────────────

def scan_reddit(account: str) -> List[Dict]:
    """Scan Reddit for trending content. Returns content ideas."""
    from reddit_scanner import scan_all, scan_via_exa
    results = scan_all(account=account, max_age_hours=24)
    total = sum(len(v) for v in results.values())

    # Fallback to Exa if direct Reddit access returned nothing
    if total == 0:
        print("  Direct Reddit blocked — falling back to Exa Reddit search")
        exa_posts = scan_via_exa(account=account)
        if exa_posts:
            results = {"domain": exa_posts}

    ideas = []
    for tier, posts in results.items():
        for post in posts[:5]:  # Top 5 per tier
            ideas.append({
                "source": f"reddit/{post.get('source', 'direct')}",
                "tier": tier,
                "subreddit": post["subreddit"],
                "title": post["title"],
                "selftext": post.get("selftext", "")[:300],
                "score": post["score"],
                "url": post.get("permalink", post.get("url", "")),
                "suggested_type": "original" if tier in ("domain", "engagement") else "original",
            })
    return ideas


def scan_watchlist(account: str) -> List[Dict]:
    """Scan watchlist accounts for recent posts to amplify.

    Uses X API v2 user tweet lookup — 1 read per batch of user IDs.
    Falls back gracefully if X API is unavailable.
    """
    watchlist = load_watchlist(account)
    if not watchlist:
        print(f"  No watchlist found for {account}")
        return []

    # For now, return watchlist metadata for LLM to use during content generation
    # Actual X API reads happen in distribution phase to save credits
    high_priority = [w for w in watchlist if w.get("priority") == "high"]
    ideas = []
    for w in high_priority[:10]:
        ideas.append({
            "source": "watchlist",
            "handle": w["handle"],
            "name": w.get("name", ""),
            "category": w.get("category", ""),
            "topics": w.get("topics", []),
            "suggested_type": "amplify",
        })

    return ideas


def generate_content_queue(reddit_ideas: List[Dict], watchlist_ideas: List[Dict],
                           angles: List[Dict], account: str,
                           dry_run: bool = False) -> List[Dict]:
    """Use LLM to generate today's content queue from ideas + angles.

    Returns a list of content items ready to post or match.
    """
    # Build the prompt
    reddit_summary = "\n".join(
        f"- [r/{i['subreddit']}, {i['score']} pts, {i['tier']}] {i['title']}"
        for i in reddit_ideas[:15]
    )

    watchlist_summary = "\n".join(
        f"- {i['handle']} ({i['name']}) — {', '.join(i.get('topics', []))}"
        for i in watchlist_ideas[:10]
    )

    angles_summary = "\n".join(
        f"- {a['id']}: \"{a['angle']}\" (trigger: {a.get('trigger', 'any')})"
        for a in angles
    )

    account_brief = {
        "fds": "Fair Data Society — data sovereignty, decentralized storage, fair data economy. "
               "TONE: Empowering, optimistic, builder-focused. Show what's POSSIBLE — people owning their data, "
               "building their own infrastructure, participating in the value their data creates. "
               "NEVER doom/surveillance/fear. Think 'you can build this' not 'they're watching you'.",
        "plur": "PLUR — persistent memory for AI coding agents. Remembers corrections, preferences, patterns. "
                "TONE: Smart, curious, science-grounded. Connect AI memory to cognitive science — "
                "how brains consolidate memory, spaced repetition, neuroplasticity, forgetting curves. "
                "Mix fun neuroscience facts with practical AI memory insights. Builder culture.",
    }.get(account, "")

    prompt = f"""You are a social media strategist for the @{_account_handle(account)} X/Twitter account.
{account_brief}

Generate today's content queue — a mix of posts ready to publish.

## Today's trending Reddit content:
{reddit_summary or "No Reddit data available."}

## Watchlist accounts to potentially amplify:
{watchlist_summary or "No watchlist data available."}

## Brand angles (reusable perspectives):
{angles_summary or "No angles defined."}

## Output format:
Generate 10-15 content items as a JSON array. Each item:
{{
  "type": "original" | "amplify" | "reply",
  "text": "the actual tweet text (max 280 chars)",
  "source_context": "what inspired this (reddit post title, watchlist account, etc.)",
  "topic": "primary topic tag",
  "angle_id": "which brand angle this uses (if any)",
  "priority": 1-5 (1=highest),
  "timing": "morning" | "midday" | "evening" | "anytime"
}}

## Rules:
- Mix: ~40% amplify/rephrase, ~25% reply-ready angles, ~20% original takes, ~15% visual (statement cards)
- Engagement tier Reddit posts → fun/personality posts (not everything needs brand angle)
- Domain tier Reddit posts → apply brand angle, make it an original take
- Each tweet must stand alone — no threads unless specified
- Be punchy, specific, quotable. No generic marketing speak.
- For amplify type: rephrase the idea in your brand's voice (don't copy)
- For visual type: write a short punchy statement (max 10 words) that works as a statement card image
  plus the tweet text that accompanies it. Add "image_text" field with the card text.
- Include 0-2 relevant hashtags max
- Output ONLY the JSON array, no other text.
"""

    content = _call_llm(prompt)
    if not content:
        return []

    # Parse the JSON array from LLM response
    try:
        # Find JSON array in response
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            items = json.loads(content[start:end])
            # Add metadata
            for i, item in enumerate(items):
                item["id"] = f"{account}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{i:03d}"
                item["status"] = "queued"
                item["generated_at"] = datetime.now(timezone.utc).isoformat()
            return items[:CONTENT_QUEUE_SIZE]
    except json.JSONDecodeError as e:
        print(f"  Failed to parse LLM content: {e}")

    return []


def prep(account: str, dry_run: bool = False):
    """Morning content prep — scan sources, generate content queue."""
    print(f"[content] Morning prep for {account}")

    # Load angles
    angles = load_angles(account)
    print(f"  Loaded {len(angles)} brand angles")

    # Scan Reddit
    print("  Scanning Reddit...")
    reddit_ideas = scan_reddit(account)
    print(f"  Found {len(reddit_ideas)} Reddit ideas")

    # Scan watchlist
    print("  Checking watchlist...")
    watchlist_ideas = scan_watchlist(account)
    print(f"  Found {len(watchlist_ideas)} watchlist targets")

    # Generate content queue
    print("  Generating content queue via LLM...")
    items = generate_content_queue(reddit_ideas, watchlist_ideas, angles, account, dry_run)
    print(f"  Generated {len(items)} content items")

    if dry_run:
        for item in items:
            print(f"  [{item.get('type', '?')}] {item.get('text', '')[:80]}...")
        return items

    # Save queue
    queue = load_content_queue(account)
    queue["items"] = items
    queue["prep_time"] = datetime.now(timezone.utc).isoformat()
    queue["reddit_count"] = len(reddit_ideas)
    queue["watchlist_count"] = len(watchlist_ideas)
    save_content_queue(queue, account)
    print(f"  Saved content queue ({len(items)} items)")

    return items


# ── Phase 2: Hourly Distribution ─────────────────────────────────────────────

def distribute(account: str, dry_run: bool = False):
    """Hourly distribution — post content from today's queue."""
    print(f"[content] Distribution cycle for {account}")

    queue = load_content_queue(account)
    items = queue.get("items", [])
    posted_today = len(queue.get("posted", []))

    if posted_today >= MAX_POSTS_PER_DAY:
        print(f"  Daily limit reached ({posted_today}/{MAX_POSTS_PER_DAY})")
        return

    # Get queued items, sorted by priority
    queued = [i for i in items if i.get("status") == "queued"]
    if not queued:
        print("  No queued content. Run 'prep' first or wait for tomorrow.")
        return

    # Filter by timing
    hour = datetime.now(timezone.utc).hour
    if hour < 10:
        timing_filter = ("morning", "anytime")
    elif hour < 15:
        timing_filter = ("midday", "anytime")
    else:
        timing_filter = ("evening", "anytime")

    eligible = [i for i in queued if i.get("timing", "anytime") in timing_filter]
    if not eligible:
        eligible = [i for i in queued if i.get("timing") == "anytime"]

    eligible.sort(key=lambda x: x.get("priority", 5))

    # Post up to MAX_POSTS_PER_CYCLE
    posted_this_cycle = 0
    for item in eligible[:MAX_POSTS_PER_CYCLE]:
        text = item.get("text", "")
        if not text:
            continue

        content_type = item.get("type", "original")
        print(f"  [{content_type}] {text[:60]}...")

        if dry_run:
            item["status"] = "dry-run"
            posted_this_cycle += 1
            continue

        # Post based on type
        try:
            from x_poster import XPoster
            poster = XPoster(account=account)

            # Generate statement card if this is a visual post
            media_id = None
            if content_type == "visual" and item.get("image_text"):
                card_path = generate_statement_card(item["image_text"], account)
                if card_path:
                    media_id = poster.upload_media(str(card_path))
                    print(f"    Generated card: {card_path}")

            if content_type == "reply" and item.get("reply_to"):
                if media_id:
                    result = poster.reply_with_media(text, item["reply_to"], [media_id])
                else:
                    result = poster.reply(text, item["reply_to"])
            elif media_id:
                result = poster.post_with_media(text, [media_id])
            else:
                result = poster.post(text)

            # Extract tweet ID from response dict
            tweet_id = result.get("data", {}).get("id", "unknown") if isinstance(result, dict) else str(result)

            item["status"] = "posted"
            item["posted_at"] = datetime.now(timezone.utc).isoformat()
            item["tweet_id"] = tweet_id
            queue.setdefault("posted", []).append({
                "id": item["id"],
                "type": content_type,
                "text": text[:100],
                "tweet_id": tweet_id,
                "posted_at": item["posted_at"],
            })
            posted_this_cycle += 1
            print(f"    Posted! tweet_id={tweet_id}")

            # Rate limit: pause between posts
            time.sleep(3)

        except Exception as e:
            print(f"    Post failed: {e}")
            item["status"] = "failed"
            item["error"] = str(e)

            # Circuit breaker: if first post fails with 402/403, stop the cycle
            if "402" in str(e) or "403" in str(e) or "CreditsDepleted" in str(e):
                print("  API credits depleted — stopping cycle")
                break

    # Save updated queue
    save_content_queue(queue, account)
    print(f"  Cycle done: {posted_this_cycle} posted, {posted_today + posted_this_cycle}/{MAX_POSTS_PER_DAY} today")


# ── Watchlist-driven amplification (distribution phase) ──────────────────────

def check_watchlist_and_amplify(account: str, dry_run: bool = False):
    """Check watchlist for new posts worth amplifying.

    This runs during distribution. Does 1 X API read (list timeline)
    to check for recent posts from high-priority watchlist accounts.
    """
    watchlist = load_watchlist(account)
    high_priority = [w for w in watchlist if w.get("priority") == "high"]

    if not high_priority:
        return

    # Check for recent tweets from watchlist via X API search
    # Use user mentions or from: queries (1 API read for batch)
    handles = [w["handle"].lstrip("@") for w in high_priority[:5]]
    query = " OR ".join(f"from:{h}" for h in handles)

    # X API v2 recent search
    api_key = os.environ.get("FDS_X_API_KEY" if account == "fds" else "PLUR_X_API_KEY")
    api_secret = os.environ.get("FDS_X_API_SECRET" if account == "fds" else "PLUR_X_API_SECRET")

    if not api_key:
        print("  No X API key for watchlist check")
        return

    # TODO: Implement X API v2 search for watchlist tweets
    # For now, watchlist amplification happens through the content queue
    # (LLM generates amplification posts based on watchlist context during prep)
    print(f"  Watchlist check: {len(high_priority)} high-priority accounts tracked")


# ── Statement Card Generator ─────────────────────────────────────────────────

# Brand colors per account
BRAND_STYLES = {
    "fds": {
        "bg": (255, 247, 237),      # Cream
        "text": (30, 30, 30),        # Near black
        "accent": (249, 115, 22),    # Orange #F97316
        "url": "fairdatasociety.org",
    },
    "plur": {
        "bg": (15, 15, 25),          # Dark navy
        "text": (255, 255, 255),     # White
        "accent": (99, 102, 241),    # Indigo
        "url": "plur.ai",
    },
}


def generate_statement_card(text: str, account: str, output_dir: Path = None) -> Optional[Path]:
    """Generate a statement card image with brand styling.

    Uses Pillow for fast local generation (no API needed).
    Returns path to generated PNG.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  Pillow not installed — skipping card generation")
        return None

    style = BRAND_STYLES.get(account, BRAND_STYLES["fds"])
    width, height = 1200, 675  # 16:9 Twitter card ratio

    img = Image.new("RGB", (width, height), style["bg"])
    draw = ImageDraw.Draw(img)

    # Try to load a good font, fall back to default
    font_size = 72
    font = None
    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/System/Library/Fonts/HelveticaNeue.ttc",                # macOS
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (OSError, IOError):
            continue

    if font is None:
        font = ImageFont.load_default()

    # Draw text centered
    lines = text.split("\n")
    total_height = len(lines) * (font_size + 20)
    y_start = (height - total_height) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y = y_start + i * (font_size + 20)
        draw.text((x, y), line, fill=style["text"], font=font)

    # Draw accent bar at bottom
    draw.rectangle([(0, height - 8), (width, height)], fill=style["accent"])

    # Draw URL bottom-right
    try:
        url_font = ImageFont.truetype(font_path, 24) if font_path else font
    except Exception:
        url_font = font
    draw.text((width - 300, height - 40), style["url"], fill=style["accent"], font=url_font)

    # Save
    if output_dir is None:
        output_dir = STATE_DIR / "content-cards"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"card-{account}-{timestamp}.png"
    path = output_dir / filename
    img.save(str(path), "PNG")
    return path


# ── Status ───────────────────────────────────────────────────────────────────

def show_status(account: str):
    """Show today's content queue status."""
    queue = load_content_queue(account)
    items = queue.get("items", [])
    posted = queue.get("posted", [])

    print(f"Content queue for {account} ({queue.get('date', '?')})")
    print(f"  Prep time: {queue.get('prep_time', 'not run')}")
    print(f"  Reddit ideas: {queue.get('reddit_count', 0)}")
    print(f"  Watchlist targets: {queue.get('watchlist_count', 0)}")
    print(f"  Queue: {len(items)} items")
    print(f"  Posted: {len(posted)}")
    print()

    by_status = {}
    for item in items:
        s = item.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    print(f"  Status breakdown: {by_status}")
    print()

    by_type = {}
    for item in items:
        t = item.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    print(f"  Type breakdown: {by_type}")
    print()

    if items:
        print("  Next up:")
        queued = [i for i in items if i.get("status") == "queued"]
        for item in queued[:5]:
            print(f"    [{item.get('type', '?')}, p{item.get('priority', '?')}] "
                  f"{item.get('text', '')[:70]}...")


# ── LLM Helper ───────────────────────────────────────────────────────────────

def _call_llm(prompt: str) -> Optional[str]:
    """Call LLM for content generation. OpenRouter primary, Anthropic fallback."""
    # Primary: OpenRouter (pay-per-use, topped up)
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        for env_name in ["mrdata.env", "local.env", ".env"]:
            ef = DATA_DIR / ".datacore" / "env" / env_name
            if ef.exists():
                for line in ef.read_text().splitlines():
                    if line.startswith("OPENROUTER_API_KEY="):
                        or_key = line.split("=", 1)[1].strip()
                        break
            if or_key:
                break

    if or_key:
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps({
                    "model": "anthropic/claude-sonnet-4-6",
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  OpenRouter failed: {e}")

    # Fallback: Anthropic API (if credits available)
    api_key = _get_anthropic_key()
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-sonnet-4-6-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            print(f"  Anthropic API failed: {e}")

    # Last fallback: Claude CLI
    import subprocess
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        print(f"  Claude CLI failed: {e}")

    return None


def _get_anthropic_key() -> Optional[str]:
    """Get Anthropic API key from env files."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    env_dir = DATA_DIR / ".datacore" / "env"
    search_files = [
        env_dir / "mrdata.env",
        env_dir / "local.env",
        env_dir / ".env",
        Path.home() / "config" / "nightshift.env",
    ]
    for env_file in search_files:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


def _account_handle(account: str) -> str:
    """Map account name to X handle."""
    return {
        "fds": "FairDataSociety",
        "plur": "plur_ai",
    }.get(account, account)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Content-First Engagement Engine")
    parser.add_argument("command", choices=["prep", "distribute", "status"],
                        help="Command to run")
    parser.add_argument("--account", default="fds", choices=["fds", "plur"],
                        help="Account to run for")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate but don't post")
    args = parser.parse_args()

    if args.command == "prep":
        prep(args.account, dry_run=args.dry_run)
    elif args.command == "distribute":
        distribute(args.account, dry_run=args.dry_run)
    elif args.command == "status":
        show_status(args.account)


if __name__ == "__main__":
    main()
