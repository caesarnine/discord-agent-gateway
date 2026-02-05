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


def _build_client(tmp_dir: str, *, registration_mode: str = "open", admin_api_token: str = "") -> TestClient:
    db = Database(Path(tmp_dir) / "test.db")
    db.init_schema()

    settings = Settings.model_validate(
        {
            "DISCORD_BOT_TOKEN": "x",
            "DISCORD_CHANNEL_ID": "123",
            "DB_PATH": str(Path(tmp_dir) / "test.db"),
            "GATEWAY_HOST": "127.0.0.1",
            "GATEWAY_PORT": "8000",
            "REGISTRATION_MODE": registration_mode,
            "ADMIN_API_TOKEN": admin_api_token,
        }
    )

    app = create_app(settings=settings, db=db, webhooks=_StubWebhooks(), attachments=_StubAttachments())
    return TestClient(app)


class TestAPI(unittest.TestCase):
    def test_register_and_poll_open_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _build_client(tmp, registration_mode="open")

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

            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json(), {"ok": True})

    def test_registration_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _build_client(tmp, registration_mode="closed")
            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(reg.status_code, 403)

    def test_invite_registration_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            admin_token = "admin-secret"
            client = _build_client(tmp, registration_mode="invite", admin_api_token=admin_token)
            headers = {"X-Admin-Token": admin_token}

            created = client.post(
                "/v1/admin/invites",
                headers=headers,
                json={"label": "test", "max_uses": 1, "expires_at": None},
            )
            self.assertEqual(created.status_code, 200)
            invite_code = created.json()["code"]

            fail_no_code = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(fail_no_code.status_code, 403)

            ok = client.post(
                "/v1/agents/register",
                json={"name": "A", "avatar_url": None, "invite_code": invite_code},
            )
            self.assertEqual(ok.status_code, 200)

            fail_reuse = client.post(
                "/v1/agents/register",
                json={"name": "B", "avatar_url": None, "invite_code": invite_code},
            )
            self.assertEqual(fail_reuse.status_code, 403)

    def test_agent_revoke_invalidates_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            admin_token = "admin-secret"
            client = _build_client(tmp, registration_mode="open", admin_api_token=admin_token)

            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(reg.status_code, 200)
            payload = reg.json()

            token = payload["token"]
            agent_id = payload["agent_id"]

            me_before = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me_before.status_code, 200)

            revoked = client.post(
                f"/v1/admin/agents/{agent_id}/revoke",
                headers={"X-Admin-Token": admin_token},
            )
            self.assertEqual(revoked.status_code, 200)

            me_after = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me_after.status_code, 401)
