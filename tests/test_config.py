import unittest

from pydantic import ValidationError

from discord_agent_gateway.config import Settings


class TestSettings(unittest.TestCase):
    def test_missing_required_env(self) -> None:
        with self.assertRaises(ValidationError):
            Settings.model_validate({})

    def test_valid_minimal_env(self) -> None:
        settings = Settings.model_validate(
            {
                "DISCORD_BOT_TOKEN": "x",
                "DISCORD_CHANNEL_ID": "123",
            }
        )
        self.assertEqual(settings.discord_channel_id, 123)
