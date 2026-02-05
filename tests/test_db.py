import tempfile
import unittest
from pathlib import Path

from discord_agent_gateway.db import Database


class TestDatabase(unittest.TestCase):
    def test_agent_roundtrip_and_posts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db = Database(db_path)
            db.init_schema()

            creds = db.agent_create("A", None)
            agent = db.agent_by_token(creds.token)
            self.assertIsNotNone(agent)
            assert agent is not None
            self.assertEqual(agent.name, "A")

            seq1 = db.post_insert(
                author_kind="webhook",
                author_id="wh1",
                author_name="Webhook",
                body="hello",
                created_at="t",
                discord_message_id="m1",
                discord_channel_id="c1",
                source_channel_id="c1",
            )
            self.assertIsInstance(seq1, int)

            # Unique discord_message_id -> second insert should fail (returns None).
            seq2 = db.post_insert(
                author_kind="webhook",
                author_id="wh1",
                author_name="Webhook",
                body="hello",
                created_at="t",
                discord_message_id="m1",
                discord_channel_id="c1",
                source_channel_id="c1",
            )
            self.assertIsNone(seq2)

            marked_seq = db.post_mark_as_agent_by_discord_message_id(
                discord_message_id="m1",
                discord_channel_id="c1",
                agent_id=creds.agent_id,
                agent_name="A",
            )
            self.assertEqual(marked_seq, seq1)

            inbox = db.inbox_fetch("c1", cursor=0, limit=10)
            self.assertEqual(len(inbox), 1)
            self.assertEqual(inbox[0].author_kind, "agent")
            self.assertEqual(inbox[0].author_id, creds.agent_id)

    def test_invite_consumption_and_agent_revoke_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db = Database(db_path)
            db.init_schema()

            invite = db.invite_create(label="x", max_uses=1, expires_at=None)
            created = db.agent_create_with_invite(name="A", avatar_url=None, invite_code=invite.code)
            self.assertIsNotNone(created)
            assert created is not None

            reused = db.agent_create_with_invite(name="B", avatar_url=None, invite_code=invite.code)
            self.assertIsNone(reused)

            valid = db.agent_by_token(created.token)
            self.assertIsNotNone(valid)

            revoked = db.agent_revoke(created.agent_id)
            self.assertTrue(revoked)

            invalid_after_revoke = db.agent_by_token(created.token)
            self.assertIsNone(invalid_after_revoke)

            rotated = db.agent_rotate_token(created.agent_id)
            self.assertIsNone(rotated)
