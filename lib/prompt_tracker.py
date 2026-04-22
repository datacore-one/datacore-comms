#!/usr/bin/env python3
"""Prompt Performance Tracker — analyze which models and prompts perform best.

Reads event logs and produces a performance report per prompt_version and model.

Usage:
    python prompt_tracker.py --report
    python prompt_tracker.py --compare v1-minimal v2-memory-architect
    python prompt_tracker.py --best-model
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVENT_LOG = Path.home() / ".cache" / "datacore" / "engagement-events.jsonl"


def load_events(since_hours: int = 168):
    """Load events from the last N hours."""
    if not EVENT_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    events = []
    for line in EVENT_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            ts = e.get("timestamp")
            if ts:
                dt = datetime.fromisoformat(ts)
                if dt >= cutoff:
                    events.append(e)
        except json.JSONDecodeError:
            continue
    return events


def report_by_prompt(events):
    """Aggregate metrics per prompt version."""
    by_prompt = defaultdict(lambda: {
        "count": 0,
        "tokens_used": [],
        "avg_tokens": 0,
        "avg_chars": 0,
        "finish_ok": 0,
        "fallback_used": 0,
    })

    for e in events:
        if e.get("type") != "draft":
            continue
        payload = e.get("payload", {})
        pv = payload.get("prompt_version", "unknown")
        d = by_prompt[pv]
        d["count"] += 1
        d["tokens_used"].append(payload.get("tokens_used", 0))
        d["avg_chars"] += payload.get("chars", 0)
        if payload.get("finish_reason") == "stop":
            d["finish_ok"] += 1
        if payload.get("model") and "fallback" in str(payload.get("model", "")).lower():
            d["fallback_used"] += 1

    for pv, d in by_prompt.items():
        if d["count"]:
            d["avg_tokens"] = sum(d["tokens_used"]) / d["count"]
            d["avg_chars"] = d["avg_chars"] / d["count"]
            d["finish_rate"] = d["finish_ok"] / d["count"]

    return by_prompt


def report_by_model(events):
    """Aggregate metrics per model."""
    by_model = defaultdict(lambda: {
        "count": 0,
        "tokens_used": [],
        "avg_tokens": 0,
        "finish_ok": 0,
        "fallback": 0,
    })

    for e in events:
        if e.get("type") != "draft":
            continue
        payload = e.get("payload", {})
        model = payload.get("model", "unknown")
        d = by_model[model]
        d["count"] += 1
        d["tokens_used"].append(payload.get("tokens_used", 0))
        if payload.get("finish_reason") == "stop":
            d["finish_ok"] += 1

    for model, d in by_model.items():
        if d["count"]:
            d["avg_tokens"] = sum(d["tokens_used"]) / d["count"]
            d["finish_rate"] = d["finish_ok"] / d["count"]

    return by_model


def print_report(events):
    print(f"# Prompt Performance Report")
    print(f"Events analyzed: {len(events)} (last 7 days)")
    print()

    by_prompt = report_by_prompt(events)
    print("## By Prompt Version")
    print(f"{'Version':<30} {'Count':>6} {'Avg Tokens':>12} {'Avg Chars':>10} {'Finish %':>10}")
    print("-" * 72)
    for pv in sorted(by_prompt, key=lambda x: -by_prompt[x]["count"]):
        d = by_prompt[pv]
        print(f"{pv:<30} {d['count']:>6} {d['avg_tokens']:>12.1f} {d['avg_chars']:>10.1f} {d['finish_rate']*100:>9.1f}%")
    print()

    by_model = report_by_model(events)
    print("## By Model")
    print(f"{'Model':<45} {'Count':>6} {'Avg Tokens':>12} {'Finish %':>10}")
    print("-" * 75)
    for model in sorted(by_model, key=lambda x: -by_model[x]["count"]):
        d = by_model[model]
        print(f"{model:<45} {d['count']:>6} {d['avg_tokens']:>12.1f} {d['finish_rate']*100:>9.1f}%")
    print()


def compare_versions(v1, v2, events):
    by_prompt = report_by_prompt(events)
    d1 = by_prompt.get(v1)
    d2 = by_prompt.get(v2)
    if not d1 or not d2:
        print(f"Insufficient data for comparison: {v1} vs {v2}")
        return

    print(f"# Comparison: {v1} vs {v2}")
    print(f"{'Metric':<20} {v1:<20} {v2:<20}")
    print("-" * 60)
    print(f"{'Drafts':<20} {d1['count']:<20} {d2['count']:<20}")
    print(f"{'Avg Tokens':<20} {d1['avg_tokens']:<20.1f} {d2['avg_tokens']:<20.1f}")
    print(f"{'Avg Chars':<20} {d1['avg_chars']:<20.1f} {d2['avg_chars']:<20.1f}")
    print(f"{'Finish Rate':<20} {d1['finish_rate']*100:<19.1f}% {d2['finish_rate']*100:<19.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Track prompt performance")
    parser.add_argument("--report", action="store_true", help="Full performance report")
    parser.add_argument("--compare", nargs=2, metavar=("V1", "V2"), help="Compare two prompt versions")
    parser.add_argument("--since-hours", type=int, default=168, help="Hours back to analyze (default: 168 = 7 days)")
    args = parser.parse_args()

    events = load_events(since_hours=args.since_hours)

    if args.compare:
        compare_versions(args.compare[0], args.compare[1], events)
    else:
        print_report(events)


if __name__ == "__main__":
    main()
