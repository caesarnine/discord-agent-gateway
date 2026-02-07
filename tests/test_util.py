import unittest

from discord_agent_gateway.util import credential_path, gateway_slug, split_for_discord


class TestSplitForDiscord(unittest.TestCase):
    def test_splits_long_text(self) -> None:
        text = "a" * 50
        parts = split_for_discord(text, max_len=10)
        self.assertTrue(all(1 <= len(p) <= 10 for p in parts))
        self.assertEqual("".join(parts), text)


class TestGatewaySlug(unittest.TestCase):
    def test_http_with_port(self) -> None:
        self.assertEqual(gateway_slug("http://localhost:8000"), "localhost_8000")

    def test_https_default_port(self) -> None:
        self.assertEqual(gateway_slug("https://gw.example.com"), "gw.example.com_443")

    def test_http_default_port(self) -> None:
        self.assertEqual(gateway_slug("http://gw.example.com"), "gw.example.com_80")

    def test_ip_address(self) -> None:
        self.assertEqual(gateway_slug("http://192.168.1.5:9000"), "192.168.1.5_9000")

    def test_credential_path(self) -> None:
        path = credential_path("http://localhost:8000", "abc-123")
        self.assertEqual(path, "~/.config/discord-agent-gateway/localhost_8000/abc-123.json")

