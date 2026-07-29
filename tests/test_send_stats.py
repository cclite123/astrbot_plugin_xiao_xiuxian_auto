from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.send_stats import DailySendStats


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class DailySendStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.day = "2026-07-29"
        self.stats = DailySendStats(self.store, date_provider=lambda: self.day)

    def test_classify_only_three_command_prefixes(self):
        self.assertEqual(
            "market_view",
            DailySendStats.classify("@3889001741 坊市查看药材1"),
        )
        self.assertEqual(
            "purchase",
            DailySendStats.classify("@3889001741 坊市购买uuid 1"),
        )
        self.assertEqual(
            "alchemy",
            DailySendStats.classify(
                "@3889001741 配方主药离火梧桐芝1药引炼心芝1"
            ),
        )
        self.assertIsNone(DailySendStats.classify("@3889001741 我的状态"))
        self.assertIsNone(DailySendStats.classify("提示：即将坊市购买药材"))

    async def test_non_target_command_does_not_create_storage(self):
        recorded = await self.stats.record("111", "@3889001741 我的状态")

        self.assertFalse(recorded)
        self.assertEqual({}, self.store.data)

    async def test_snapshot_isolated_by_account_and_rolls_over_date(self):
        await self.stats.record("111", "@3889001741 坊市查看药材1")
        await self.stats.record("222", "@3889001741 坊市购买abc 1")

        first = await self.stats.snapshot("111")

        self.assertEqual(
            {"market_view": 1, "purchase": 0, "alchemy": 0},
            first["counts"],
        )
        self.assertEqual(1, first["total"])
        self.day = "2026-07-30"

        next_day = await self.stats.snapshot("111")

        self.assertEqual("2026-07-30", next_day["date"])
        self.assertEqual(0, next_day["total"])
        self.assertEqual({"date": "2026-07-30", "accounts": {}}, self.store.data[DailySendStats.STORAGE_KEY])

    async def test_persisted_counts_are_visible_to_new_instance(self):
        await self.stats.record("111", "@3889001741 配方主药灵草1药引灵花1")
        restarted = DailySendStats(self.store, date_provider=lambda: self.day)

        snapshot = await restarted.snapshot("111")

        self.assertEqual(1, snapshot["counts"]["alchemy"])
        self.assertEqual(1, snapshot["total"])

    async def test_invalid_persisted_counts_are_normalized_to_zero(self):
        self.store.data[DailySendStats.STORAGE_KEY] = {
            "date": self.day,
            "accounts": {
                "111": {
                    "market_view": -5,
                    "purchase": "bad",
                    "alchemy": 3.7,
                    "other": 100,
                }
            },
        }

        snapshot = await self.stats.snapshot("111")

        self.assertEqual(
            {"market_view": 0, "purchase": 0, "alchemy": 3},
            snapshot["counts"],
        )
        self.assertEqual(3, snapshot["total"])

    async def test_concurrent_records_do_not_lose_counts(self):
        await asyncio.gather(
            *[
                self.stats.record("111", "@3889001741 坊市查看药材1")
                for _ in range(50)
            ]
        )

        snapshot = await self.stats.snapshot("111")

        self.assertEqual(50, snapshot["counts"]["market_view"])
        self.assertEqual(50, snapshot["total"])


if __name__ == "__main__":
    unittest.main()
