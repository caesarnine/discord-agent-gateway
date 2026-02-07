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

    settings = Settings(
        _env_file=None,
        DISCORD_BOT_TOKEN="x",
        DISCORD_CHANNEL_ID=123,
        DB_PATH=str(Path(tmp_dir) / "test.db"),
        GATEWAY_HOST="127.0.0.1",
        GATEWAY_PORT=8000,
        REGISTRATION_MODE=registration_mode,
        ADMIN_API_TOKEN=admin_api_token,
    )

    app = create_app(settings=settings, db=db, webhooks=_StubWebhooks(), attachments=_StubAttachments())
    return TestClient(app)


class TestAPI(unittest.TestCase):
    def test_register_and_poll_open_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _build_client(tmp, registration_mode="open")

            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(reg.status_code, 200)
            data = reg.json()
            token = data["token"]
            agent_id = data["agent_id"]
            self.assertEqual(data["gateway_base_url"], "http://127.0.0.1:8000")
            self.assertEqual(
                data["credential_path"],
                f"~/.config/discord-agent-gateway/127.0.0.1_8000/{agent_id}.json",
            )

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

    def test_skill_docs_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _build_client(tmp, registration_mode="open")

            skill = client.get("/skill.md")
            self.assertEqual(skill.status_code, 200)
            self.assertIn("## Channel Focus", skill.text)
            self.assertIn("## Bootstrap", skill.text)
            self.assertIn("## Credentials", skill.text)
            self.assertIn("~/.config/discord-agent-gateway/127.0.0.1_8000/", skill.text)
            self.assertIn("credential_path", skill.text)
            self.assertIn("## API Reference", skill.text)
            self.assertIn("## Threads", skill.text)
            self.assertIn("### GET /v1/inbox", skill.text)
            self.assertIn('"is_self"', skill.text)
            self.assertIn("### POST /v1/post", skill.text)
            self.assertIn('"last_seq"', skill.text)
            self.assertIn("### GET /v1/capabilities", skill.text)

            heartbeat = client.get("/heartbeat.md")
            self.assertEqual(heartbeat.status_code, 200)
            self.assertIn("## Steps", heartbeat.text)
            self.assertIn("## Pagination", heartbeat.text)
            self.assertIn("## Ack discipline", heartbeat.text)
            self.assertIn("last_check_at", heartbeat.text)
            self.assertIn("<gateway_slug>/<agent_id>.json", heartbeat.text)

            messaging = client.get("/messaging.md")
            self.assertEqual(messaging.status_code, 200)
            self.assertIn("## Peer norms", messaging.text)
            self.assertIn("## Avoiding loops and dogpiles", messaging.text)
            self.assertIn("Don't post twice in a row", messaging.text)
            self.assertIn("## Formatting", messaging.text)

    def test_profile_uses_discord_metadata_as_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.init_schema()
            db.setting_set("discord_channel_name", "math-talk")
            db.setting_set("discord_channel_topic", "Discuss proofs and conjectures.")

            settings = Settings(
                _env_file=None,
                DISCORD_BOT_TOKEN="x",
                DISCORD_CHANNEL_ID=123,
                DB_PATH=str(Path(tmp) / "test.db"),
                GATEWAY_HOST="127.0.0.1",
                GATEWAY_PORT=8000,
                ADMIN_API_TOKEN="admin-secret",
            )
            app = create_app(settings=settings, db=db, webhooks=_StubWebhooks(), attachments=_StubAttachments())
            client = TestClient(app)

            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            token = reg.json()["token"]

            ctx = client.get("/v1/context", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(ctx.status_code, 200)
            self.assertEqual(ctx.json()["name"], "math-talk")
            self.assertEqual(ctx.json()["mission"], "Discuss proofs and conjectures.")

    def test_profile_context_and_admin_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            admin_token = "admin-secret"
            db = Database(Path(tmp) / "test.db")
            db.init_schema()
            db.setting_set("discord_channel_name", "general")
            db.setting_set("discord_channel_topic", "General discussion.")

            settings = Settings(
                _env_file=None,
                DISCORD_BOT_TOKEN="x",
                DISCORD_CHANNEL_ID=123,
                DB_PATH=str(Path(tmp) / "test.db"),
                GATEWAY_HOST="127.0.0.1",
                GATEWAY_PORT=8000,
                ADMIN_API_TOKEN=admin_token,
            )
            app = create_app(settings=settings, db=db, webhooks=_StubWebhooks(), attachments=_StubAttachments())
            client = TestClient(app)

            reg = client.post("/v1/agents/register", json={"name": "A", "avatar_url": None})
            self.assertEqual(reg.status_code, 200)
            token = reg.json()["token"]

            initial_context = client.get("/v1/context", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(initial_context.status_code, 200)
            self.assertEqual(initial_context.json()["name"], "general")
            self.assertEqual(initial_context.json()["mission"], "General discussion.")

            updated = client.put(
                "/v1/admin/profile",
                headers={"X-Admin-Token": admin_token},
                json={"name": "Incident Room", "mission": "Focus on triage and unblock production incidents quickly."},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["name"], "Incident Room")

            updated_context = client.get("/v1/context", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(updated_context.status_code, 200)
            self.assertEqual(updated_context.json()["name"], "Incident Room")
            self.assertIn("production incidents", updated_context.json()["mission"])

            skill = client.get("/skill.md")
            self.assertEqual(skill.status_code, 200)
            self.assertIn("Incident Room", skill.text)
