import unittest

from discord_agent_gateway.config import Settings


class TestSettings(unittest.TestCase):
    def test_missing_required_env(self) -> None:
        with self.assertRaises(ValueError):
            Settings.from_env({})

    def test_valid_minimal_env(self) -> None:
        settings = Settings.from_env(
            {
                "DISCORD_BOT_TOKEN": "x",
                "DISCORD_CHANNEL_ID": "123",
            }
        )
        self.assertEqual(settings.discord_channel_id, 123)

