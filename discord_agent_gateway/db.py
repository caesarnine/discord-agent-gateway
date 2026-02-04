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
    source_channel_id: str


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    post_seq: int
    discord_message_id: str
    source_channel_id: str
    filename: str
    url: Optional[str]
    proxy_url: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    height: Optional[int]
    width: Optional[int]


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
            discord_channel_id TEXT NOT NULL,
            source_channel_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_posts_seq ON posts(seq);
        CREATE INDEX IF NOT EXISTS idx_posts_channel_seq ON posts(discord_channel_id, seq);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_token_sha256 ON agents(token_sha256);

        CREATE TABLE IF NOT EXISTS attachments (
            attachment_id TEXT PRIMARY KEY,
            post_seq INTEGER NOT NULL,
            discord_message_id TEXT NOT NULL,
            source_channel_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            url TEXT,
            proxy_url TEXT,
            content_type TEXT,
            size_bytes INTEGER,
            height INTEGER,
            width INTEGER,
            FOREIGN KEY(post_seq) REFERENCES posts(seq) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_attachments_post_seq ON attachments(post_seq);

        CREATE TABLE IF NOT EXISTS ingestion_state (
            source_channel_id TEXT PRIMARY KEY,
            last_message_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """

        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(schema)

            # Lightweight migrations for older DBs.
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
            if "source_channel_id" not in cols:
                conn.execute("ALTER TABLE posts ADD COLUMN source_channel_id TEXT;")
                cols.append("source_channel_id")
            if "source_channel_id" in cols:
                conn.execute("UPDATE posts SET source_channel_id = discord_channel_id WHERE source_channel_id IS NULL;")

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
        source_channel_id: str,
    ) -> Optional[int]:
        post_id = str(uuid.uuid4())
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO posts(
                        post_id,author_kind,author_id,author_name,body,created_at,discord_message_id,discord_channel_id,source_channel_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        post_id,
                        author_kind,
                        author_id,
                        author_name,
                        body,
                        created_at,
                        discord_message_id,
                        discord_channel_id,
                        source_channel_id,
                    ),
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

    def post_seq_by_discord_message_id(self, *, discord_message_id: str, discord_channel_id: str) -> Optional[int]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT seq FROM posts WHERE discord_message_id=? AND discord_channel_id=?",
                (discord_message_id, discord_channel_id),
            ).fetchone()
            return int(row["seq"]) if row else None

    def attachments_insert(self, attachments: list[Attachment]) -> None:
        if not attachments:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO attachments(
                    attachment_id,post_seq,discord_message_id,source_channel_id,filename,url,proxy_url,content_type,size_bytes,height,width
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        a.attachment_id,
                        a.post_seq,
                        a.discord_message_id,
                        a.source_channel_id,
                        a.filename,
                        a.url,
                        a.proxy_url,
                        a.content_type,
                        a.size_bytes,
                        a.height,
                        a.width,
                    )
                    for a in attachments
                ],
            )

    def attachments_for_posts(self, post_seqs: list[int]) -> dict[int, list[Attachment]]:
        if not post_seqs:
            return {}
        placeholders = ",".join("?" for _ in post_seqs)
        query = f"""
            SELECT attachment_id,post_seq,discord_message_id,source_channel_id,filename,url,proxy_url,content_type,size_bytes,height,width
            FROM attachments
            WHERE post_seq IN ({placeholders})
            ORDER BY post_seq ASC
        """
        with self.connect() as conn:
            rows = conn.execute(query, post_seqs).fetchall()
        out: dict[int, list[Attachment]] = {}
        for row in rows:
            att = Attachment(
                attachment_id=str(row["attachment_id"]),
                post_seq=int(row["post_seq"]),
                discord_message_id=str(row["discord_message_id"]),
                source_channel_id=str(row["source_channel_id"]),
                filename=str(row["filename"]),
                url=row["url"],
                proxy_url=row["proxy_url"],
                content_type=row["content_type"],
                size_bytes=(int(row["size_bytes"]) if row["size_bytes"] is not None else None),
                height=(int(row["height"]) if row["height"] is not None else None),
                width=(int(row["width"]) if row["width"] is not None else None),
            )
            out.setdefault(att.post_seq, []).append(att)
        return out

    def attachment_get(self, attachment_id: str) -> Optional[Attachment]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT attachment_id,post_seq,discord_message_id,source_channel_id,filename,url,proxy_url,content_type,size_bytes,height,width
                FROM attachments
                WHERE attachment_id=?
                """,
                (attachment_id,),
            ).fetchone()
        if not row:
            return None
        return Attachment(
            attachment_id=str(row["attachment_id"]),
            post_seq=int(row["post_seq"]),
            discord_message_id=str(row["discord_message_id"]),
            source_channel_id=str(row["source_channel_id"]),
            filename=str(row["filename"]),
            url=row["url"],
            proxy_url=row["proxy_url"],
            content_type=row["content_type"],
            size_bytes=(int(row["size_bytes"]) if row["size_bytes"] is not None else None),
            height=(int(row["height"]) if row["height"] is not None else None),
            width=(int(row["width"]) if row["width"] is not None else None),
        )

    def ingestion_state_get(self, source_channel_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_message_id FROM ingestion_state WHERE source_channel_id=?",
                (source_channel_id,),
            ).fetchone()
            return str(row["last_message_id"]) if row else None

    def ingestion_state_set(self, *, source_channel_id: str, last_message_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_state(source_channel_id,last_message_id,updated_at) VALUES(?,?,?)
                ON CONFLICT(source_channel_id) DO UPDATE SET last_message_id=excluded.last_message_id, updated_at=excluded.updated_at
                """,
                (source_channel_id, last_message_id, utc_now_iso()),
            )

    def ingestion_state_source_channels(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT source_channel_id FROM ingestion_state").fetchall()
            return [str(r["source_channel_id"]) for r in rows]

    def inbox_fetch(self, channel_id: str, cursor: int, limit: int) -> list[Post]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, post_id, author_kind, author_id, author_name, body, created_at, discord_message_id, source_channel_id
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
                    source_channel_id=str(row["source_channel_id"] or channel_id),
                )
            )
        return posts
