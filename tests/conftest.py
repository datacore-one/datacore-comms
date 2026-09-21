"""Point the posting kill switch somewhere disposable for the duration of a test.

`x_poster.KILL_SWITCH_PATH` is resolved at import from DATACORE_ROOT, so it names
the REAL ~/Data/.datacore/state/campaign-kill-switch no matter what a test does
to its own tmp_path. That file has existed since the 2026-05-21 X suspension, and
its presence is correct -- so sixteen tests across test_x_poster.py,
test_link_verification.py and test_integration.py raised KillSwitchActive instead
of exercising posting, rate limiting and link checks.

Those sixteen had been red for months without anyone seeing it, because no CI job
and no scheduled run collects this directory. Red-for-a-real-reason is still red:
a genuine regression in the poster now had nowhere to show up.

Redirecting the module attribute is the seam test_engagement_autonomous.py
already uses to prove the switch DOES halt posting; that test patches it after
this fixture and still gets its own path. The production default is untouched --
nothing here can make a live kill switch stop working.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


@pytest.fixture(autouse=True)
def _kill_switch_is_not_the_hosts(tmp_path, monkeypatch):
    try:
        import x_poster
    except Exception:  # noqa: BLE001 -- tests that never import the poster are unaffected
        return
    monkeypatch.setattr(x_poster, "KILL_SWITCH_PATH", tmp_path / "no-kill-switch")
