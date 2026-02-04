import unittest

from discord_agent_gateway.util import split_for_discord


class TestSplitForDiscord(unittest.TestCase):
    def test_splits_long_text(self) -> None:
        text = "a" * 50
        parts = split_for_discord(text, max_len=10)
        self.assertTrue(all(1 <= len(p) <= 10 for p in parts))
        self.assertEqual("".join(parts), text)

