#!/usr/bin/env python3
"""Structured event logging for comms pipeline.

Logs JSONL to .datacore/state/comms-events.log
Events: discover, draft, post, approve, reject, escalate, error, rate_limit
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict


def _log_path() -> Path:
    root = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
    log_dir = root / ".datacore" / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "comms-events.log"


def log_event(event_type: str, details: Dict, account: str = None):
    """Append a structured event to the log.

    Args:
        event_type: discover | draft | post | approve | reject | escalate | error | rate_limit
        details: Arbitrary event payload
        account: Account name if applicable
    """
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "account": account,
        "details": details,
    }
    log_file = _log_path()
    with open(log_file, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def tail_events(n: int = 50, event_type: str = None) -> list:
    """Read last N events, optionally filtered by type."""
    log_file = _log_path()
    if not log_file.exists():
        return []
    lines = []
    with open(log_file) as f:
        for line in f:
            lines.append(line.strip())
    # Filter and return last N
    events = []
    for line in reversed(lines):
        if not line:
            continue
        try:
            evt = json.loads(line)
            if event_type is None or evt.get("type") == event_type:
                events.append(evt)
                if len(events) >= n:
                    break
        except json.JSONDecodeError:
            continue
    return list(reversed(events))
