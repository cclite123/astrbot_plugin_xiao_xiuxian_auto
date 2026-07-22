from __future__ import annotations

import time
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.bounty import BountyController, BountyState
from astrbot_plugin_xiao_xiuxian_auto.sect import SectController, SectState


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class RetryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounty_pauses_after_three_unanswered_queries(self):
        store = MemoryStore()
        controller = BountyController(store, "3889001741", response_timeout_sec=1, max_response_retries=3)
        key = "10001:20002"
        sent = []

        async def send(message):
            sent.append(message)

        await controller._set(
            key,
            BountyState(enabled=True, phase="WAITING_QUERY", pending_action="query", last_action_ts=time.time() - 20),
        )
        for _ in range(4):
            await controller.tick(key, send)
            state = await controller._get(key)
            state.last_action_ts = time.time() - 20
            await controller._set(key, state)

        state = await controller._get(key)
        self.assertEqual("PAUSED", state.phase)
        self.assertEqual(3, sent.count("@3889001741 悬赏令查看"))

    async def test_sect_retries_complete_without_restarting_task(self):
        store = MemoryStore()
        controller = SectController(store, "3889001741", {"response_timeout_sec": 1, "max_response_retries": 3})
        key = "10001:20002"
        sent = []

        async def send(message):
            sent.append(message)

        await controller._set(
            key,
            SectState(enabled=True, phase="WAITING_COMPLETE", pending_action="complete", next_action_ts=time.time() - 1),
        )
        for _ in range(4):
            await controller.tick(key, send)
            state = await controller._get(key)
            state.next_action_ts = time.time() - 1
            await controller._set(key, state)

        state = await controller._get(key)
        self.assertEqual("PAUSED", state.phase)
        self.assertEqual(["@3889001741 宗门任务完成"] * 3, sent)


if __name__ == "__main__":
    unittest.main()
