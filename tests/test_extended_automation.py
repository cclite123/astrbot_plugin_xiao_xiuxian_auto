from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.automation_safety import SideEffectGuard
from astrbot_plugin_xiao_xiuxian_auto.daily_dual import DailyDualController
from astrbot_plugin_xiao_xiuxian_auto.linjie_mining import LinjieMiningController
from astrbot_plugin_xiao_xiuxian_auto.inventory_ops import InventoryOpsController


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        value = self.data.get(key, default)
        return dict(value) if isinstance(value, dict) else value

    async def set(self, key, value):
        self.data[key] = value


class Recorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)


class ExtendedAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def test_guard_blocks_unknown_action_until_reset(self):
        store = MemoryStore()
        guard = SideEffectGuard(store)
        first = await guard.begin("1:2", "buy", "purchase", "购买 x")
        self.assertTrue(first.allowed)
        await guard.pause_unknown("1:2", "buy", first.request_id, "timeout")
        blocked = await guard.begin("1:2", "buy", "purchase", "购买 x")
        self.assertFalse(blocked.allowed)
        await guard.reset_module("1:2", "buy")
        self.assertTrue((await guard.begin("1:2", "buy", "purchase", "购买 x")).allowed)

    async def test_dual_uses_normal_then_bonded_counts(self):
        store = MemoryStore()
        guard = SideEffectGuard(store)
        dual = DailyDualController(store, guard, {"action_delay_sec": 0})
        send = Recorder()
        await dual.cmd_set_dao_name("1:2", "甲")
        await dual.cmd_enable("1:2")
        state = await dual._get("1:2")
        state.next_action_ts = 0
        await dual._set("1:2", state)
        await dual.tick("1:2", send)
        self.assertEqual(send.messages, ["我的双修次数"])
        handled = await dual.on_official_text(
            "1:2", "甲道友剩余双修次数：1次\n与乙双修剩余次数：1次", send
        )
        self.assertTrue(handled)
        await dual.tick("1:2", send)
        self.assertEqual(send.messages[-1], "双修 甲")
        await dual.on_official_text("1:2", "你们一起修炼了一晚", send)
        await dual.tick("1:2", send)
        self.assertEqual(send.messages[-1], "双修 乙")

    async def test_dual_timeout_does_not_resend_risky_command(self):
        store = MemoryStore()
        guard = SideEffectGuard(store)
        dual = DailyDualController(store, guard, {"response_timeout_sec": 3, "action_delay_sec": 0})
        send = Recorder()
        state = await dual._get("1:2")
        state.enabled = True
        state.dao_name = "甲"
        state.phase = "ACTION_PENDING"
        state.pending_action = "normal"
        state.cycle_date = dual._cycle_date()
        await dual._set("1:2", state)
        await dual.tick("1:2", send)
        state = await dual._get("1:2")
        state.sent_at = time.time() - 4
        await dual._set("1:2", state)
        await dual.tick("1:2", send)
        await dual.tick("1:2", send)
        self.assertEqual(send.messages.count("双修 甲"), 1)
        self.assertEqual((await dual._get("1:2")).phase, "PAUSED")

    async def test_linjie_mining_continues_only_after_success(self):
        store = MemoryStore()
        guard = SideEffectGuard(store)
        mining = LinjieMiningController(store, guard, {"action_delay_sec": 0})
        send = Recorder()
        await mining.cmd_enable("1:2")
        state = await mining._get("1:2")
        state.next_action_ts = 0
        await mining._set("1:2", state)
        await mining.tick("1:2", send)
        self.assertEqual(send.messages, ["灵界挖灵石"])
        await mining.on_official_text("1:2", "你提起矿镐，向着灵山走去\n本次挖矿时长：5秒")
        self.assertEqual((await mining._get("1:2")).phase, "WAITING")
        await mining.on_official_text("1:2", "成功采集到1,200灵矿石，灵矿石储备：2万")
        await mining.tick("1:2", send)
        self.assertEqual(send.messages.count("灵界挖灵石"), 2)

    async def test_inventory_preview_requires_second_snapshot_before_execution(self):
        controller = InventoryOpsController(official_qq="1")
        send = Recorder()
        await controller.cmd_preview_alchemy("1:2", "装备", send)
        page = "装备\n第 1 页 / 共 1 页\n名称：玄铁剑\n数量：2"
        await controller.on_official_text("1:2", page, send)
        self.assertIn("确认一键炼金", send.messages[-1])
        await controller.cmd_confirm("1:2", send)
        await controller.on_official_text("1:2", page, send)
        self.assertTrue(any("炼金 玄铁剑 2" in item for item in send.messages))


if __name__ == "__main__":
    unittest.main()
