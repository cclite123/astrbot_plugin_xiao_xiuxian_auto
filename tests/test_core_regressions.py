from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.bounty import BountyOption, choose_option
from astrbot_plugin_xiao_xiuxian_auto.endless import EndlessState, EndlessTowerController
from astrbot_plugin_xiao_xiuxian_auto.sect import SectController


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class CoreRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_special_bounty_items_are_individual_priorities(self):
        options = [
            BountyOption(1, "普通任务", 100, 1, 100, "普通奖励"),
            BountyOption(2, "剑诀任务", 100, 60, 1, "无瑕七绝剑"),
        ]
        self.assertEqual(choose_option(options, "修为").index, 2)

    async def test_endless_ignores_unsolicited_challenge_success(self):
        store = MemoryStore()
        controller = EndlessTowerController(store, "1")
        key = "1:2"
        sent = []

        async def send(message):
            sent.append(message)

        await controller._set(key, EndlessState(enabled=True, phase="READY", done_count=2))
        await controller.on_official_text(key, "踏破星河，成就无上", send)
        self.assertEqual((await controller._get(key)).done_count, 2)

        await controller._set(
            key,
            EndlessState(
                enabled=True,
                phase="WAITING_CHALLENGE",
                pending_action="challenge",
                done_count=2,
            ),
        )
        await controller.on_official_text(key, "踏破星河，成就无上", send)
        self.assertEqual((await controller._get(key)).done_count, 3)

    async def test_sect_default_tasks_require_a_list(self):
        controller = SectController(
            MemoryStore(),
            "1",
            {"daily_start_time": "07:45", "default_enabled_tasks": ["密令", "除魔"]},
        )
        state = await controller._get("1:2")
        self.assertEqual((state.daily_hour, state.daily_minute), (7, 45))
        self.assertTrue(state.tasks["密令"])
        self.assertTrue(state.tasks["除魔"])
        self.assertFalse(state.tasks["仙丹"])


if __name__ == "__main__":
    unittest.main()
