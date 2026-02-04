import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from discord_agent_gateway.api import create_app
from discord_agent_gateway.config import Settings
from discord_agent_gateway.db import Database


class _StubWebhooks:
    def get_or_create(self):
        raise RuntimeError("no webhook configured")

    def execute(self, **_kwargs):
        raise RuntimeError("no webhook configured")


class _StubAttachments:
    def resolve(self, _attachment_id: str):
        return None

    def iter_download(self, _url: str):
        raise RuntimeError("no downloads")


class TestAPI(unittest.TestCase):
    def test_register_and_poll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.init_schema()

            settings = Settings.from_env(
                {
                    "DISCORD_BOT_TOKEN": "x",
                    "DISCORD_CHANNEL_ID": "123",
                    "DB_PATH": str(Path(tmp) / "test.db"),
                    "GATEWAY_HOST": "127.0.0.1",
                    "GATEWAY_PORT": "8000",
                }
            )

            app = create_app(settings=settings, db=db, webhooks=_StubWebhooks(), attachments=_StubAttachments())
            client = TestClient(app)

            skill = client.get("/skill.md")
            self.assertEqual(skill.status_code, 200)
            self.assertIn("discord-agent-gateway", skill.text)

            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(reg.status_code, 200)
            token = reg.json()["token"]

            me = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me.status_code, 200)

            inbox = client.get("/v1/inbox", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(inbox.status_code, 200)
            self.assertEqual(inbox.json()["events"], [])

            unauth_download = client.get("/v1/attachments/does-not-exist")
            self.assertEqual(unauth_download.status_code, 401)

            missing_download = client.get(
                "/v1/attachments/does-not-exist",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(missing_download.status_code, 404)
