#!/usr/bin/env python3
"""Strategic follow campaign manager.

Manages daily follow budgets, cluster prioritization, and follow/unfollow workflows.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from follow_db import FollowDB


class FollowManager:
    def __init__(self, db: FollowDB, poster, daily_budget: int = 10,
                 cluster_priority: list = None):
        """
        Args:
            db: FollowDB instance
            poster: XPoster instance with follow/unfollow methods
            daily_budget: Max follows per day
            cluster_priority: List of cluster names in priority order
        """
        self.db = db
        self.poster = poster
        self.daily_budget = daily_budget
        self.cluster_priority = cluster_priority or ["dev", "privacy", "ai"]

    def execute_daily_follows(self) -> Dict:
        """Execute today's follow budget. Returns result summary."""
        followed = 0
        failed = 0
        skipped = 0

        for cluster in self.cluster_priority:
            if followed >= self.daily_budget:
                break
            pending = self.db.get_pending(cluster=cluster,
                                          limit=self.daily_budget - followed)
            for account in pending:
                if followed >= self.daily_budget:
                    break
                try:
                    self.poster.follow(account["user_id"])
                    self.db.mark_followed(account["user_id"])
                    followed += 1
                except Exception as e:
                    failed += 1
                    # Skip accounts that block or are private
                    if "unauthorized" in str(e).lower() or "forbidden" in str(e).lower():
                        self.db._conn.execute(
                            "UPDATE accounts SET status = 'skipped' WHERE user_id = ?",
                            (account["user_id"],),
                        )
                        self.db._conn.commit()
                        skipped += 1

        return {
            "followed": followed,
            "failed": failed,
            "skipped": skipped,
            "budget": self.daily_budget,
        }
