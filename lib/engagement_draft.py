#!/usr/bin/env python3
"""Draft engagement replies using Claude Code in headless mode.

Runs `claude -p` from the Data directory so the drafting agent has full
CLAUDE.md context, comms module docs, brand voice, and example replies.
No API key needed — uses the local Claude Code auth (OAuth).
"""

import json
import os
import subprocess
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

PROMPT_TEMPLATE = """You are drafting a reply tweet for @FairDataSociety.

Read this file first — study the VOICE and RHYTHM of these posted replies:
- 0-personal/1-active/fds/fairdrop/comms/campaigns/fairdrop-launch/february-2026/engagement-replies.md

## Target tweet

Author: {author}
Tweet: {content}
URL: {url}

## Voice — THIS IS CRITICAL

Study the examples in engagement-replies.md. The voice is:
- **Punchy short openers** that hook: "Swiss jurisdiction > US jurisdiction. Still jurisdiction."
- **Rhythm**: short. short. then a longer sentence that lands the point.
- **Wit**: subtle, dry, never forced. "Build it so they can't, not so they won't."
- **Conversational**, not declarative. You're adding to a conversation, not writing a policy paper.
- Think: sharp engineer at a bar, not professor at a podium.

DO NOT write flat, lecture-y prose. If it sounds like it belongs in a whitepaper, throw it away and try again.

## Insight — EQUALLY CRITICAL

The reply MUST give the reader something they didn't know or hadn't thought about.
- A technical insight: "For async transfers you need decentralized storage, not just a direct pipe."
- A reframing: "Privacy must be architectural, not legislative."
- A non-obvious connection the author missed.

Being punchy alone isn't enough. Being insightful alone isn't enough. The best replies are BOTH — a sharp delivery of a genuinely new idea. The reader should think "huh, I hadn't considered that."

## Emotional tone — THE VIBE

FDS is builder energy. Not doom. Not cynicism. Not lecturing.

The reply should leave the reader feeling GOOD — like they just met someone who gets the problem AND is doing something about it. Think:
- Constructive, not just critical. Diagnose briefly, then point toward the solution.
- Warm confidence: "we've been building this" not "you're all doomed"
- Ally energy: you're on their side, fighting the same fight
- If the original tweet is frustrated/angry, validate briefly then redirect toward hope

BAD vibe: "They won't erase the database. You can't smash a backup." (bleak, no way out)
GOOD vibe: "The real fix isn't smashing cameras — it's architecture where there's nothing to subpoena." (same insight, but points forward)

## Rules

1. NO links — build authority through ideas
2. Under 280 characters. Shorter is better.
3. No emojis. No hashtags. No marketing speak.
4. Don't mention FDS/Fairdrop unless it fits naturally.
5. Match the conversation's register.
6. Make ONE sharp, insightful point — not a clever one-liner with no substance.
7. End on forward energy, not doom.

## Output

Return ONLY the reply text. No quotes, no explanation, no preamble."""


def draft_reply(conversation: dict) -> str:
    """Generate a reply draft using Claude Code headless mode.

    Runs from ~/Data so Claude has full context (CLAUDE.md, comms module,
    brand voice docs, example replies).

    Args:
        conversation: dict with author, content, url keys

    Returns:
        Draft reply text (under 280 chars)
    """
    prompt = PROMPT_TEMPLATE.format(
        author=conversation.get("author", "unknown"),
        content=conversation.get("content", ""),
        url=conversation.get("url", ""),
    )

    # Clean env: remove CLAUDECODE to allow nested invocation
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "sonnet",
            "--output-format", "text",
            "--no-session-persistence",
            "--max-turns", "3",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=str(DATA_DIR),
        env=env,
        timeout=120,
    )

    if result.returncode != 0:
        raise Exception(f"Claude CLI failed: {result.stderr[:300]}")

    text = result.stdout.strip().strip('"').strip("'")

    # Enforce length
    if len(text) > 280:
        for i in range(279, 0, -1):
            if text[i] in ".!?":
                text = text[: i + 1]
                break
        else:
            text = text[:277] + "..."

    return text
