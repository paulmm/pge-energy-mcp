"""SQLite-backed storage for per-user system configurations."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DATA_DIR = os.environ.get("DATA_DIR", "./data")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS system_configs (
    config_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class ConfigStore:
    """Lazy-initialized SQLite store for system configs."""

    def __init__(self, db_dir: str = None):
        self._db_dir = db_dir or DATA_DIR
        self._db_path = Path(self._db_dir) / "configs.db"
        self._initialized = False

    def _get_conn(self) -> sqlite3.Connection:
        if not self._initialized:
            Path(self._db_dir).mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            self._initialized = True
            return conn
        return sqlite3.connect(str(self._db_path))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save(self, config_id: str, config_dict: dict) -> dict:
        """Save a new system config. Raises ValueError if config_id already exists."""
        if not config_id or not isinstance(config_id, str):
            raise ValueError("config_id must be a non-empty string")
        if not isinstance(config_dict, dict):
            raise ValueError("config_dict must be a dict")

        now = self._now()
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO system_configs (config_id, config_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (config_id, json.dumps(config_dict), now, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Config '{config_id}' already exists. Use update() to modify.")
        finally:
            conn.close()

        return {"config_id": config_id, "created_at": now, "status": "saved"}

    def get(self, config_id: str) -> Optional[dict]:
        """Retrieve a config by ID. Returns None if not found."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT config_json, created_at, updated_at FROM system_configs WHERE config_id = ?",
                (config_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return {
            "config_id": config_id,
            "config": json.loads(row[0]),
            "created_at": row[1],
            "updated_at": row[2],
        }

    def update(self, config_id: str, partial_dict: dict) -> dict:
        """Merge partial_dict into an existing config. Raises ValueError if not found."""
        existing = self.get(config_id)
        if existing is None:
            raise ValueError(f"Config '{config_id}' not found")

        config = existing["config"]
        _deep_merge(config, partial_dict)

        now = self._now()
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE system_configs SET config_json = ?, updated_at = ? WHERE config_id = ?",
                (json.dumps(config), now, config_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {"config_id": config_id, "updated_at": now, "config": config, "status": "updated"}

    def delete(self, config_id: str) -> dict:
        """Delete a config by ID. Raises ValueError if not found."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM system_configs WHERE config_id = ?", (config_id,)
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"Config '{config_id}' not found")
        finally:
            conn.close()

        return {"config_id": config_id, "status": "deleted"}

    def list_all(self) -> list[dict]:
        """List all stored config summaries (without full config body)."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT config_id, created_at, updated_at FROM system_configs ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()

        return [
            {"config_id": r[0], "created_at": r[1], "updated_at": r[2]}
            for r in rows
        ]


def _deep_merge(base: dict, updates: dict) -> None:
    """Recursively merge updates into base dict. Lists are replaced, not appended."""
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# Module-level singleton for use across tools.
_store: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    """Get or create the module-level ConfigStore singleton."""
    global _store
    if _store is None:
        _store = ConfigStore()
    return _store
