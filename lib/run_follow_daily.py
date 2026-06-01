#!/usr/bin/env python3
"""Runner: daily follow execution. Called by systemd timer.

DISABLED 2026-06-01 — automated follow violates X Authenticity policy
(indiscriminate following → permanent suspension). Triggered the FDS
account suspension on 2026-05-20.

To re-enable: remove the sys.exit() block below. Don't. Use draft-only
patterns instead. See 5-plur/1-tracks/comms/comms-redesign-research-2026-05-30.md.
"""
import sys
sys.exit("DISABLED 2026-06-01 — automated follow violates X policy. See comms-redesign-research-2026-05-30.md")

import os
from pathlib import Path

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

# Load env
from env_utils import load_env_files
load_env_files()

from follow_db import FollowDB
from follow_manager import FollowManager
from x_poster import XPoster

db = FollowDB(DATA_DIR / ".datacore" / "state" / "follow-list.db")
poster = XPoster(account='fds', user_id=os.environ.get('FDS_X_USER_ID'))
fm = FollowManager(db=db, poster=poster, daily_budget=50,
                   bearer_token=os.environ.get('X_BEARER_TOKEN'))
result = fm.execute_daily_follows()
print(f"Followed: {result['followed']} | Errors: {result['errors']}")
print(f"Remaining pending: {result['remaining_pending']}")
db.close()
