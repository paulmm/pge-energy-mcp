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

_CREATE_OAUTH_TABLE = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    config_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    subscription_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (config_id, provider)
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
            conn.execute(_CREATE_OAUTH_TABLE)
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


    # ── OAuth Token Storage ────────────────────────────────────────────

    def save_oauth_token(self, config_id: str, provider: str, token_data: dict) -> dict:
        """
        Save or update OAuth tokens for a config+provider pair.

        Args:
            config_id: User's config identifier
            provider: Provider name (e.g., "pge")
            token_data: Dict with access_token, refresh_token, expires_in (seconds),
                        scope, subscription_id
        """
        if not config_id or not provider:
            raise ValueError("config_id and provider must be non-empty strings")

        now = self._now()
        expires_in = token_data.get("expires_in", 3600)
        # Compute absolute expiry from current time + expires_in
        from datetime import timedelta
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).isoformat()

        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_tokens "
                "(config_id, provider, access_token, refresh_token, expires_at, scope, subscription_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    config_id,
                    provider,
                    token_data.get("access_token", ""),
                    token_data.get("refresh_token", ""),
                    expires_at,
                    token_data.get("scope", ""),
                    token_data.get("subscription_id", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "config_id": config_id,
            "provider": provider,
            "expires_at": expires_at,
            "status": "saved",
        }

    def get_oauth_token(self, config_id: str, provider: str) -> Optional[dict]:
        """Retrieve stored OAuth tokens. Returns None if not found."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT access_token, refresh_token, expires_at, scope, subscription_id "
                "FROM oauth_tokens WHERE config_id = ? AND provider = ?",
                (config_id, provider),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return {
            "config_id": config_id,
            "provider": provider,
            "access_token": row[0],
            "refresh_token": row[1],
            "expires_at": row[2],
            "scope": row[3],
            "subscription_id": row[4],
        }

    def delete_oauth_token(self, config_id: str, provider: str) -> dict:
        """Delete stored OAuth tokens for a config+provider pair."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM oauth_tokens WHERE config_id = ? AND provider = ?",
                (config_id, provider),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(
                    f"No OAuth token found for config '{config_id}', provider '{provider}'"
                )
        finally:
            conn.close()

        return {"config_id": config_id, "provider": provider, "status": "deleted"}

    def is_token_expired(self, config_id: str, provider: str) -> bool:
        """Check if a stored token is expired (or missing)."""
        token = self.get_oauth_token(config_id, provider)
        if token is None:
            return True
        try:
            expires_at = datetime.fromisoformat(token["expires_at"])
            return datetime.now(timezone.utc) >= expires_at
        except (ValueError, KeyError):
            return True


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
