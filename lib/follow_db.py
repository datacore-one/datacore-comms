#!/usr/bin/env python3
"""SQLite-backed follow tracking for strategic follow campaigns.

Tracks discovered accounts, follow status, source anchors, and overlap metrics.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict


class FollowDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT,
                cluster TEXT,
                source_anchor TEXT,
                overlap_count INTEGER DEFAULT 0,
                followers INTEGER,
                following INTEGER,
                status TEXT DEFAULT 'discovered',
                discovered_at TEXT,
                followed_at TEXT,
                unfollowed_at TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON accounts(status)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cluster ON accounts(cluster)
        """)
        self._conn.commit()

    def add_account(self, user_id: str, username: str, display_name: str = None,
                    cluster: str = None, source_anchor: str = None,
                    overlap_count: int = 0, followers: int = None,
                    following: int = None) -> bool:
        """Add a new discovered account. Returns False if already exists."""
        try:
            self._conn.execute(
                """INSERT INTO accounts
                   (user_id, username, display_name, cluster, source_anchor,
                    overlap_count, followers, following, status, discovered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)""",
                (user_id, username, display_name, cluster, source_anchor,
                 overlap_count, followers, following,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_followed(self, user_id: str):
        self._conn.execute(
            "UPDATE accounts SET status = 'followed', followed_at = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self._conn.commit()

    def mark_unfollowed(self, user_id: str):
        self._conn.execute(
            "UPDATE accounts SET status = 'unfollowed', unfollowed_at = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self._conn.commit()

    def get_pending(self, cluster: str = None, limit: int = 100) -> List[Dict]:
        """Get accounts ready to follow (discovered but not yet followed)."""
        if cluster:
            rows = self._conn.execute(
                "SELECT * FROM accounts WHERE status = 'discovered' AND cluster = ? "
                "ORDER BY overlap_count DESC, followers DESC LIMIT ?",
                (cluster, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM accounts WHERE status = 'discovered' "
                "ORDER BY overlap_count DESC, followers DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = [c[1] for c in self._conn.execute("PRAGMA table_info(accounts)").fetchall()]
        return [dict(zip(cols, row)) for row in rows]

    def stats(self) -> Dict:
        total = self._conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        by_status = {}
        for status, count in self._conn.execute(
            "SELECT status, COUNT(*) FROM accounts GROUP BY status"
        ).fetchall():
            by_status[status] = count
        by_cluster = {}
        for cluster, count in self._conn.execute(
            "SELECT cluster, COUNT(*) FROM accounts WHERE cluster IS NOT NULL GROUP BY cluster"
        ).fetchall():
            by_cluster[cluster] = count
        return {
            "total": total,
            "by_status": by_status,
            "by_cluster": by_cluster,
        }
