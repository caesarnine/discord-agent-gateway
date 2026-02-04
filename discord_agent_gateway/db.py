from __future__ import annotations

import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .util import sha256_hex, utc_now_iso


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    avatar_url: Optional[str]


@dataclass(frozen=True)
class AgentCredentials:
    agent_id: str
    token: str


@dataclass(frozen=True)
class Post:
    seq: int
    post_id: str
    author_kind: str
    author_id: str
    author_name: Optional[str]
    body: str
    created_at: str
    discord_message_id: Optional[str]


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_url TEXT,
            token_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS receipts (
            agent_id TEXT PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        );

        CREATE TABLE IF NOT EXISTS posts (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL UNIQUE,
            author_kind TEXT NOT NULL,      -- 'agent' | 'human' | 'bot' | 'webhook'
            author_id TEXT NOT NULL,        -- agent_id, discord_user_id, webhook_id, etc.
            author_name TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            discord_message_id TEXT UNIQUE,
            discord_channel_id TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_posts_seq ON posts(seq);
        CREATE INDEX IF NOT EXISTS idx_posts_channel_seq ON posts(discord_channel_id, seq);
        """

        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(schema)

    def setting_get(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def setting_set(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def agent_create(self, name: str, avatar_url: Optional[str]) -> AgentCredentials:
        agent_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        token_hash = sha256_hex(token)

        with self.connect() as conn:
            conn.execute(
                "INSERT INTO agents(agent_id,name,avatar_url,token_sha256,created_at) VALUES(?,?,?,?,?)",
                (agent_id, name, avatar_url, token_hash, utc_now_iso()),
            )
            conn.execute("INSERT OR IGNORE INTO receipts(agent_id,last_seq) VALUES(?,0)", (agent_id,))

        return AgentCredentials(agent_id=agent_id, token=token)

    def agent_by_token(self, token: str) -> Optional[Agent]:
        token_hash = sha256_hex(token)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT agent_id,name,avatar_url FROM agents WHERE token_sha256=?",
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        return Agent(agent_id=str(row["agent_id"]), name=str(row["name"]), avatar_url=row["avatar_url"])

    def receipt_get(self, agent_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT last_seq FROM receipts WHERE agent_id=?", (agent_id,)).fetchone()
            return int(row["last_seq"]) if row else 0

    def receipt_set(self, agent_id: str, last_seq: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO receipts(agent_id,last_seq) VALUES(?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET last_seq=excluded.last_seq",
                (agent_id, last_seq),
            )

    def post_exists_by_discord_message_id(self, discord_message_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM posts WHERE discord_message_id=?", (discord_message_id,)).fetchone()
            return row is not None

    def post_insert(
        self,
        *,
        author_kind: str,
        author_id: str,
        author_name: Optional[str],
        body: str,
        created_at: str,
        discord_message_id: Optional[str],
        discord_channel_id: str,
    ) -> Optional[int]:
        post_id = str(uuid.uuid4())
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO posts(
                        post_id,author_kind,author_id,author_name,body,created_at,discord_message_id,discord_channel_id
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (post_id, author_kind, author_id, author_name, body, created_at, discord_message_id, discord_channel_id),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def post_mark_as_agent_by_discord_message_id(
        self,
        *,
        discord_message_id: str,
        discord_channel_id: str,
        agent_id: str,
        agent_name: Optional[str],
    ) -> Optional[int]:
        """
        If a webhook message was ingested before the send-time insert, rewrite it to preserve agent identity.
        Returns the row seq if the message exists.
        """
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE posts
                SET author_kind='agent', author_id=?, author_name=?
                WHERE discord_message_id=? AND discord_channel_id=?
                """,
                (agent_id, agent_name, discord_message_id, discord_channel_id),
            )
            row = conn.execute(
                "SELECT seq FROM posts WHERE discord_message_id=? AND discord_channel_id=?",
                (discord_message_id, discord_channel_id),
            ).fetchone()
            return int(row["seq"]) if row else None

    def inbox_fetch(self, channel_id: str, cursor: int, limit: int) -> list[Post]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, post_id, author_kind, author_id, author_name, body, created_at, discord_message_id
                FROM posts
                WHERE discord_channel_id=? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (channel_id, cursor, limit),
            ).fetchall()

        posts: list[Post] = []
        for row in rows:
            posts.append(
                Post(
                    seq=int(row["seq"]),
                    post_id=str(row["post_id"]),
                    author_kind=str(row["author_kind"]),
                    author_id=str(row["author_id"]),
                    author_name=row["author_name"],
                    body=str(row["body"]),
                    created_at=str(row["created_at"]),
                    discord_message_id=row["discord_message_id"],
                )
            )
        return posts
