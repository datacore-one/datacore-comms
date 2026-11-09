"""A pending draft syncs to Miles where the fleet roster says he runs.

The nightshift droplet went dark around 2026-10-25; Miles now runs as a user on
dayshift. `ssh nightshift python3` reached the dark machine and every sync failed.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import draft_pipeline as dp


def test_draft_sync_runs_where_the_roster_puts_miles(monkeypatch):
    seen = []
    monkeypatch.setattr(dp, "_miles_argv", lambda cmd: ["ssh", "dayshift", f"sudo -n -u miles -H sh -lc '{cmd}'"])
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "ok", ""))
    dp._sync_draft_to_server({"pending": [{"id": "d1", "text": "x"}]}, "d1")
    assert seen and seen[0][:2] == ["ssh", "dayshift"] and "python3" in seen[0][2]
