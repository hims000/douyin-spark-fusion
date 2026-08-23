from __future__ import annotations

import hashlib
import os
from typing import Any

import aiosqlite

from .config import DATA_DIR, settings

DB_PATH = os.path.join(DATA_DIR, "fusion.db")


async def get_db() -> aiosqlite.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    db = await get_db()
    await db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA cache_size=-10000;
        PRAGMA mmap_size=268435456;

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            group_id INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            cookies TEXT DEFAULT '[]',
            storage_state TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            last_login TEXT DEFAULT '',
            send_gap_min INTEGER DEFAULT 10,
            send_gap_max INTEGER DEFAULT 20,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            spark_days INTEGER DEFAULT 0,
            last_msg_at TEXT DEFAULT '',
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            friend_name TEXT NOT NULL,
            message TEXT DEFAULT '',
            message_type TEXT DEFAULT 'text',
            cron_expr TEXT DEFAULT '0 9 * * *',
            is_active INTEGER DEFAULT 1,
            last_run TEXT DEFAULT '',
            next_run TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            task_id INTEGER,
            user_id INTEGER NOT NULL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            friend_name TEXT NOT NULL,
            message_hash TEXT NOT NULL,
            status TEXT DEFAULT 'success',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts(user_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_account_id ON tasks(account_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_is_active ON tasks(is_active);
        CREATE INDEX IF NOT EXISTS idx_friends_account_id ON friends(account_id);
        CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);
    """)

    admin_hash = hashlib.sha256("spark2024".encode()).hexdigest()
    await db.execute(
        "INSERT OR IGNORE INTO users(username, password_hash, is_admin) VALUES(?,?,1)",
        ("admin", admin_hash),
    )
    await db.commit()
    await db.close()


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


async def get_setting(key: str, default: str = "") -> str:
    db = await get_db()
    row = await db.execute_fetchall("SELECT value FROM settings WHERE key=?", (key,))
    await db.close()
    return row[0]["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
        (key, value, value),
    )
    await db.commit()
    await db.close()


def parse_auth_json(value: str, label: str) -> Any:
    import json
    from pathlib import Path

    candidate = Path(value).expanduser()
    try:
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是有效 JSON 或可读文件路径") from exc