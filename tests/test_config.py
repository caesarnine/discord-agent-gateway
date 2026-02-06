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
        settings = Settings(
            _env_file=None,
            DISCORD_BOT_TOKEN="x",
            DISCORD_CHANNEL_ID=123,
        )
        self.assertEqual(settings.discord_channel_id, 123)
        self.assertEqual(settings.registration_mode, "closed")

    def test_invalid_registration_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                DISCORD_BOT_TOKEN="x",
                DISCORD_CHANNEL_ID=123,
                REGISTRATION_MODE="nope",
            )

    def test_port_alias_falls_back_to_railway_port(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DISCORD_BOT_TOKEN": "x",
                "DISCORD_CHANNEL_ID": "123",
                "PORT": "4321",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.gateway_port, 4321)
