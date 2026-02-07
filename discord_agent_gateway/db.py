from __future__ import annotations

import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .models import (
    Agent,
    AgentAdmin,
    AgentCredentials,
    Attachment,
    ChannelProfile,
    Invite,
    InviteCreateResult,
    Post,
)
from .util import sha256_hex, utc_now_iso


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self) -> sqlite3.Connection:
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
            created_at TEXT NOT NULL,
            revoked_at TEXT
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

        CREATE INDEX IF NOT EXISTS idx_agents_revoked_at ON agents(revoked_at);

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

        CREATE TABLE IF NOT EXISTS invites (
            invite_id TEXT PRIMARY KEY,
            label TEXT,
            code_sha256 TEXT NOT NULL UNIQUE,
            max_uses INTEGER NOT NULL,
            used_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_invites_revoked_at ON invites(revoked_at);
        CREATE INDEX IF NOT EXISTS idx_invites_expires_at ON invites(expires_at);
        """

        with self.transaction() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(schema)

            # Lightweight migrations for older DBs.
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
            if "source_channel_id" not in cols:
                conn.execute("ALTER TABLE posts ADD COLUMN source_channel_id TEXT;")
                cols.append("source_channel_id")
            if "source_channel_id" in cols:
                conn.execute("UPDATE posts SET source_channel_id = discord_channel_id WHERE source_channel_id IS NULL;")

            agent_cols = [r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()]
            if "revoked_at" not in agent_cols:
                conn.execute("ALTER TABLE agents ADD COLUMN revoked_at TEXT;")

    def setting_get(self, key: str) -> Optional[str]:
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def setting_set(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _agent_create_in_conn(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        avatar_url: Optional[str],
    ) -> AgentCredentials:
        agent_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        token_hash = sha256_hex(token)
        conn.execute(
            "INSERT INTO agents(agent_id,name,avatar_url,token_sha256,created_at,revoked_at) VALUES(?,?,?,?,?,NULL)",
            (agent_id, name, avatar_url, token_hash, utc_now_iso()),
        )
        conn.execute("INSERT OR IGNORE INTO receipts(agent_id,last_seq) VALUES(?,0)", (agent_id,))
        return AgentCredentials(agent_id=agent_id, token=token)

    def agent_create(self, name: str, avatar_url: Optional[str]) -> AgentCredentials:
        with self.transaction() as conn:
            return self._agent_create_in_conn(conn, name=name, avatar_url=avatar_url)

    def agent_create_with_invite(
        self,
        *,
        name: str,
        avatar_url: Optional[str],
        invite_code: str,
    ) -> Optional[AgentCredentials]:
        code_hash = sha256_hex(invite_code.strip())
        now_iso = utc_now_iso()

        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE invites
                SET used_count = used_count + 1
                WHERE code_sha256 = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND used_count < max_uses
                """,
                (code_hash, now_iso),
            )
            if cur.rowcount != 1:
                return None
            return self._agent_create_in_conn(conn, name=name, avatar_url=avatar_url)

    def agent_by_token(self, token: str) -> Optional[Agent]:
        token_hash = sha256_hex(token)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT agent_id,name,avatar_url FROM agents WHERE token_sha256=? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        return Agent(agent_id=str(row["agent_id"]), name=str(row["name"]), avatar_url=row["avatar_url"])

    def agents_list(self) -> list[AgentAdmin]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT agent_id,name,avatar_url,created_at,revoked_at
                FROM agents
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [
            AgentAdmin(
                agent_id=str(r["agent_id"]),
                name=str(r["name"]),
                avatar_url=r["avatar_url"],
                created_at=str(r["created_at"]),
                revoked_at=r["revoked_at"],
            )
            for r in rows
        ]

    def agent_revoke(self, agent_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE agents
                SET revoked_at = ?
                WHERE agent_id = ? AND revoked_at IS NULL
                """,
                (utc_now_iso(), agent_id),
            )
            return cur.rowcount == 1

    def agent_rotate_token(self, agent_id: str) -> Optional[str]:
        token = secrets.token_urlsafe(32)
        token_hash = sha256_hex(token)
        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE agents
                SET token_sha256=?
                WHERE agent_id=? AND revoked_at IS NULL
                """,
                (token_hash, agent_id),
            )
            if cur.rowcount != 1:
                return None
        return token

    def invite_create(
        self,
        *,
        label: Optional[str],
        max_uses: int,
        expires_at: Optional[str],
    ) -> InviteCreateResult:
        if max_uses <= 0:
            raise ValueError("max_uses must be > 0")
        normalized_label = (label or "").strip() or None

        for _ in range(5):
            invite_id = str(uuid.uuid4())
            code = secrets.token_urlsafe(24)
            code_hash = sha256_hex(code)
            created_at = utc_now_iso()
            try:
                with self.transaction() as conn:
                    conn.execute(
                        """
                        INSERT INTO invites(invite_id,label,code_sha256,max_uses,used_count,created_at,expires_at,revoked_at)
                        VALUES(?,?,?,?,0,?,?,NULL)
                        """,
                        (invite_id, normalized_label, code_hash, max_uses, created_at, expires_at),
                    )
            except sqlite3.IntegrityError:
                continue

            invite = Invite(
                invite_id=invite_id,
                label=normalized_label,
                max_uses=max_uses,
                used_count=0,
                created_at=created_at,
                expires_at=expires_at,
                revoked_at=None,
            )
            return InviteCreateResult(invite=invite, code=code)

        raise RuntimeError("Failed to create invite after retries")

    def invite_list(self) -> list[Invite]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT invite_id,label,max_uses,used_count,created_at,expires_at,revoked_at
                FROM invites
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [
            Invite(
                invite_id=str(r["invite_id"]),
                label=r["label"],
                max_uses=int(r["max_uses"]),
                used_count=int(r["used_count"]),
                created_at=str(r["created_at"]),
                expires_at=r["expires_at"],
                revoked_at=r["revoked_at"],
            )
            for r in rows
        ]

    def invite_revoke(self, invite_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE invites
                SET revoked_at = ?
                WHERE invite_id = ? AND revoked_at IS NULL
                """,
                (utc_now_iso(), invite_id),
            )
            return cur.rowcount == 1

    def receipt_get(self, agent_id: str) -> int:
        with self.transaction() as conn:
            row = conn.execute("SELECT last_seq FROM receipts WHERE agent_id=?", (agent_id,)).fetchone()
            return int(row["last_seq"]) if row else 0

    def receipt_set(self, agent_id: str, last_seq: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO receipts(agent_id,last_seq) VALUES(?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET last_seq=excluded.last_seq",
                (agent_id, last_seq),
            )

    def post_exists_by_discord_message_id(self, discord_message_id: str) -> bool:
        with self.transaction() as conn:
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
            with self.transaction() as conn:
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
        with self.transaction() as conn:
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
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT seq FROM posts WHERE discord_message_id=? AND discord_channel_id=?",
                (discord_message_id, discord_channel_id),
            ).fetchone()
            return int(row["seq"]) if row else None

    def attachments_insert(self, attachments: list[Attachment]) -> None:
        if not attachments:
            return
        with self.transaction() as conn:
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
        with self.transaction() as conn:
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
        with self.transaction() as conn:
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
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT last_message_id FROM ingestion_state WHERE source_channel_id=?",
                (source_channel_id,),
            ).fetchone()
            return str(row["last_message_id"]) if row else None

    def ingestion_state_set(self, *, source_channel_id: str, last_message_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_state(source_channel_id,last_message_id,updated_at) VALUES(?,?,?)
                ON CONFLICT(source_channel_id) DO UPDATE SET last_message_id=excluded.last_message_id, updated_at=excluded.updated_at
                """,
                (source_channel_id, last_message_id, utc_now_iso()),
            )

    def ingestion_state_source_channels(self) -> list[str]:
        with self.transaction() as conn:
            rows = conn.execute("SELECT source_channel_id FROM ingestion_state").fetchall()
            return [str(r["source_channel_id"]) for r in rows]

    def inbox_fetch(self, channel_id: str, cursor: int, limit: int) -> list[Post]:
        with self.transaction() as conn:
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

    def channel_profile_get(self, *, default_name: str, default_mission: str) -> ChannelProfile:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT key,value
                FROM settings
                WHERE key IN ('channel_profile_name','channel_profile_mission','channel_profile_updated_at')
                """
            ).fetchall()

        values = {str(r["key"]): str(r["value"]) for r in rows}
        name = (values.get("channel_profile_name") or "").strip() or default_name
        mission = (values.get("channel_profile_mission") or "").strip() or default_mission
        updated_at = (values.get("channel_profile_updated_at") or "").strip() or None
        return ChannelProfile(name=name, mission=mission, updated_at=updated_at)

    def channel_profile_set(self, *, name: str, mission: str) -> ChannelProfile:
        normalized_name = (name or "").strip()
        normalized_mission = (mission or "").strip()
        if not normalized_name:
            raise ValueError("Profile name is required.")
        if not normalized_mission:
            raise ValueError("Profile mission is required.")

        updated_at = utc_now_iso()
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                [
                    ("channel_profile_name", normalized_name),
                    ("channel_profile_mission", normalized_mission),
                    ("channel_profile_updated_at", updated_at),
                ],
            )

        return ChannelProfile(name=normalized_name, mission=normalized_mission, updated_at=updated_at)
