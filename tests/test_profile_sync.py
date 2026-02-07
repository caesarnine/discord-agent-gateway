import logging
import tempfile
import unittest
from pathlib import Path

from discord_agent_gateway.config import Settings
from discord_agent_gateway.db import Database
from discord_agent_gateway.discord_api import DiscordAPIError
from discord_agent_gateway.profile_sync import sync_discord_channel_profile, upsert_discord_channel_profile


class _DiscordOK:
    def get_channel(self, *, channel_id: int):
        return {"id": str(channel_id), "name": "math-lab", "topic": "Discuss proofs"}


class _DiscordError:
    def get_channel(self, *, channel_id: int):
        raise DiscordAPIError(status_code=403, message="forbidden")


def _quiet_logger() -> logging.Logger:
    logger = logging.getLogger("tests.profile_sync")
    logger.setLevel(logging.CRITICAL)
    return logger


class TestProfileSync(unittest.TestCase):
    def test_upsert_clears_stale_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.init_schema()
            db.setting_set("discord_channel_name", "general")
            db.setting_set("discord_channel_topic", "Old topic")

            upsert_discord_channel_profile(db=db, channel_name="general", channel_topic=None)

            self.assertEqual(db.setting_get("discord_channel_topic"), "")
            profile = db.channel_profile_get(default_name="", default_mission="")
            self.assertEqual(profile.name, "general")
            self.assertEqual(profile.mission, "")

    def test_sync_writes_discord_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.init_schema()
            settings = Settings(_env_file=None, DISCORD_BOT_TOKEN="x", DISCORD_CHANNEL_ID=123)

            ok = sync_discord_channel_profile(
                settings=settings,
                db=db,
                discord=_DiscordOK(),
                logger=_quiet_logger(),
            )

            self.assertTrue(ok)
            self.assertEqual(db.setting_get("discord_channel_name"), "math-lab")
            self.assertEqual(db.setting_get("discord_channel_topic"), "Discuss proofs")

    def test_sync_returns_false_on_discord_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.init_schema()
            db.setting_set("discord_channel_name", "existing-name")
            db.setting_set("discord_channel_topic", "existing-topic")
            settings = Settings(_env_file=None, DISCORD_BOT_TOKEN="x", DISCORD_CHANNEL_ID=123)

            ok = sync_discord_channel_profile(
                settings=settings,
                db=db,
                discord=_DiscordError(),
                logger=_quiet_logger(),
            )

            self.assertFalse(ok)
            self.assertEqual(db.setting_get("discord_channel_name"), "existing-name")
            self.assertEqual(db.setting_get("discord_channel_topic"), "existing-topic")
