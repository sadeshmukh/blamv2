import asyncio
import os
import sqlite3
import threading
from functools import lru_cache
from typing import List


@lru_cache(maxsize=1)
def get_client() -> sqlite3.Connection:
    db_path = os.getenv("DB_PATH", "channelblam.db")
    if directory := os.path.dirname(db_path):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_db_lock = threading.Lock()


async def ensure_schema() -> None:
    db = get_client()

    def _create():
        with _db_lock:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_members (
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, user_id)
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_blammed (
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, user_id)
                );
                """
            )
            db.execute(  # idv_required_level: 0, 1, 2 (none, all IDV, IDV <18)
                """
                CREATE TABLE IF NOT EXISTS channel_settings (
                    channel_id TEXT NOT NULL,
                    idv_required_level INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (channel_id)
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_whitelist (
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    PRIMARY KEY (channel_id, user_id)
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_sync (
                    channel_id TEXT NOT NULL,
                    last_sync_at DATETIME,
                    last_update_at DATETIME,
                    PRIMARY KEY (channel_id)
                );
                """
            )
            db.commit()

    await asyncio.to_thread(_create)


async def mark_channel_updated(channel_id: str) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                """
                INSERT INTO channel_sync (channel_id, last_update_at)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id) DO UPDATE SET last_update_at=CURRENT_TIMESTAMP;
                """,
                (channel_id,),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def marksync(channel_id: str) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                """
                INSERT INTO channel_sync (channel_id, last_sync_at)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id) DO UPDATE SET last_sync_at=CURRENT_TIMESTAMP;
                """,
                (channel_id,),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def needs_sync(channel_id: str) -> bool:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT last_sync_at, last_update_at FROM channel_sync WHERE channel_id = ?;",
                (channel_id,),
            )
            row = cur.fetchone()
            if not row:
                return True
            last_sync = row["last_sync_at"]
            last_update = row["last_update_at"]
            if last_update is None:
                return False
            if last_sync is None:
                return True
            return last_update > last_sync

    return await asyncio.to_thread(_query)


async def list_tracked_channels() -> List[str]:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                """
                SELECT DISTINCT channel_id FROM (
                    SELECT channel_id FROM channel_settings
                    UNION
                    SELECT channel_id FROM channel_members
                    UNION
                    SELECT channel_id FROM channel_blammed
                    UNION
                    SELECT channel_id FROM channel_whitelist
                );
                """
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)


async def set_members(channel_id: str, user_ids: List[str]) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_members WHERE channel_id = ?;",
                (channel_id,),
            )
            for user_id in user_ids:
                db.execute(
                    "INSERT OR IGNORE INTO channel_members (channel_id, user_id) VALUES (?, ?);",
                    (channel_id, user_id),
                )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def get_idv_required_level(channel_id: str) -> int:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT idv_required_level FROM channel_settings WHERE channel_id = ?;",
                (channel_id,),
            )
            row = cur.fetchone()
            if row:
                return row["idv_required_level"]
            return 0

    return await asyncio.to_thread(_query)


async def set_idv_required_level(
    channel_id: str,
    level: int,
) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                """
                INSERT INTO channel_settings (channel_id, idv_required_level)
                VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET idv_required_level=excluded.idv_required_level;
                """,
                (channel_id, level),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def add_member(channel_id: str, user_id: str) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_members (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def remove_member(channel_id: str, user_id: str) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?;",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def list_members(channel_id: str) -> List[str]:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT user_id FROM channel_members WHERE channel_id = ?;",
                (channel_id,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)


async def add_blam(
    channel_id: str,
    user_id: str,
) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_blammed (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def list_blammed(channel_id: str) -> List[str]:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT user_id FROM channel_blammed WHERE channel_id = ? ORDER BY created_at DESC;",
                (channel_id,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)


async def remove_blam(
    channel_id: str,
    user_id: str,
) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_blammed WHERE channel_id = ? AND user_id = ?;",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def add_whitelist(
    channel_id: str,
    user_id: str,
) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_whitelist (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def remove_whitelist(
    channel_id: str,
    user_id: str,
) -> None:
    db = get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_whitelist WHERE channel_id = ? AND user_id = ?;",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)
    await mark_channel_updated(channel_id)


async def list_whitelisted(channel_id: str) -> List[str]:
    db = get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT user_id FROM channel_whitelist WHERE channel_id = ?;",
                (channel_id,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)
