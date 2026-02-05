import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from discord_agent_gateway.config import Settings


class TestSettings(unittest.TestCase):
    def test_missing_required_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None)

    def test_valid_minimal_env(self) -> None:
        settings = Settings.model_validate(
            {
                "DISCORD_BOT_TOKEN": "x",
                "DISCORD_CHANNEL_ID": "123",
            }
        )
        self.assertEqual(settings.discord_channel_id, 123)
