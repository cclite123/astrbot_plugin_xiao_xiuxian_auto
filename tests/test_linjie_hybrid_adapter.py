from __future__ import annotations

import tempfile
import time
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.linjie import LinjieState, LinjieUpgradeController
from astrbot_plugin_xiao_xiuxian_auto.linjie_upgrade.model import (
    PAGE_KINDS,
    Building,
    BuildingUpgrade,
    DisplayOutput,
    LinjieSnapshot,
)


class MemoryStore:
    def __init__(self) -> None:
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class SendRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(str(message))


def combo_snapshot() -> LinjieSnapshot:
    return LinjieSnapshot(
        "account",
        "group",
        0,
        DisplayOutput(10),
        0,
        0,
        False,
        (Building("ComboBuilding", 9, DisplayOutput(10), 100, False),),
        (BuildingUpgrade("ComboBuilding", 0, 100, False),),
        0,
        0,
        0,
        0,
        (),
        "2026-08-21T00:00:00+08:00",
        {name: "test" for name in PAGE_KINDS},
    )


class LinjieHybridAdapterTests(unittest.TestCase):
    def test_controller_prefers_official_compound_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = LinjieUpgradeController(
                MemoryStore(),
                "official",
                {"planner_engine": "hybrid", "snapshot_root": directory, "cache_ttl_sec": 10**9},
            )
            snapshot = combo_snapshot()
            state = LinjieState(
                balance=snapshot.balance,
                total_speed=snapshot.total_output.total,
                last_query_ts=time.time(),
                module_snapshot=snapshot.to_dict(),
            )

            plan = controller._module_plan(state)
            self.assertEqual([item.kind for item in plan], ["building", "tech"])
            self.assertEqual(plan[0].route_target_count, 10)
            self.assertEqual(plan[0].route_target_level, 1)

            steps = controller._simulate_multi_step_plan(state)
            self.assertEqual([item["kind"] for item in steps[:2]], ["building", "upgrade"])
            self.assertEqual(steps[1]["cost"], 100)

    def test_action_timeout_pauses_instead_of_resending(self) -> None:
        async def run() -> tuple[LinjieState, SendRecorder]:
            store = MemoryStore()
            controller = LinjieUpgradeController(
                store,
                "official",
                {"snapshot_root": tempfile.gettempdir(), "action_timeout_sec": 5},
            )
            key = "account:group"
            await controller._set(
                key,
                LinjieState(
                    enabled=True,
                    phase="WAITING_RESULT",
                    next_action_ts=time.time() - 1,
                    pending_action={"command": "灵界建造ComboBuilding 1"},
                ),
            )
            send = SendRecorder()
            await controller.tick(key, send)
            return await controller._get(key), send

        import asyncio

        state, send = asyncio.run(run())
        self.assertEqual(state.phase, "PAUSED")
        self.assertTrue(any("不会自动重发" in message for message in send.messages))


if __name__ == "__main__":
    unittest.main()
