from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.auto_alchemy_optimizer import AutoAlchemyJob, AutoAlchemyOptimizer
from astrbot_plugin_xiao_xiuxian_auto.bounty import BountyController, BountyState
from astrbot_plugin_xiao_xiuxian_auto.cultivate import (
    CultivateController,
    CultivateState,
    MODE_CULTIVATE,
    MODE_SECLUSION,
    MODE_SECT_SECLUSION,
)
from astrbot_plugin_xiao_xiuxian_auto.endless import EndlessState, EndlessTowerController
from astrbot_plugin_xiao_xiuxian_auto.inventory_ops import InventoryJob, InventoryOpsController
from astrbot_plugin_xiao_xiuxian_auto.linjie import LinjieState, LinjieUpgradeController
from astrbot_plugin_xiao_xiuxian_auto.routine import RoutineController, RoutineState
from astrbot_plugin_xiao_xiuxian_auto.secret import SecretController, SecretState
from astrbot_plugin_xiao_xiuxian_auto.sect import SectController, SectState


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class SendRecorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(str(message))


class StateMachineRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def _run_ticks(self, controller, key: str, send: SendRecorder, count: int = 3) -> None:
        for _ in range(count):
            await asyncio.wait_for(controller.tick(key, send), timeout=0.5)

    async def test_due_state_machine_ticks_return_and_do_not_reenter_without_delay(self):
        key = "10001:20002"
        now = time.time()
        cases = []

        bounty = BountyController(MemoryStore(), "3889001741")
        await bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        cases.append(("bounty", bounty, 1))

        secret = SecretController(MemoryStore(), "3889001741")
        await secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        cases.append(("secret", secret, 1))

        routine = RoutineController(MemoryStore(), "3889001741")
        await routine._set(
            key,
            RoutineState(
                signin_enabled=True,
                sign_phase="WORKING",
                sign_action_ts=now - 1,
                pill_enabled=True,
                pill_phase="WORKING",
                pill_action_ts=now - 1,
                mine_enabled=True,
                mine_phase="WORKING",
                mine_action_ts=now - 1,
                farm_enabled=True,
                farm_phase="WORKING",
                farm_action_ts=now - 1,
            ),
        )
        cases.append(("routine", routine, 4))

        sect = SectController(MemoryStore(), "3889001741")
        await sect._set(key, SectState(enabled=True, phase="PROBING", next_action_ts=now - 1))
        cases.append(("sect", sect, 1))

        cultivate = CultivateController(MemoryStore(), "3889001741")
        await cultivate._set(
            key,
            CultivateState(mode=MODE_CULTIVATE, is_resting=False, last_action_ts=now - 120),
        )
        cases.append(("cultivate", cultivate, 1))

        linjie = LinjieUpgradeController(MemoryStore(), "3889001741")
        await linjie._set(key, LinjieState(enabled=True, phase="RUNNING", next_action_ts=now - 1))
        cases.append(("linjie", linjie, 1))

        endless = EndlessTowerController(MemoryStore(), "3889001741")
        await endless._set(key, EndlessState(enabled=True, phase="READY", next_action_ts=now - 1))
        cases.append(("endless", endless, 1))

        inventory = InventoryOpsController(official_qq="3889001741")
        inventory.jobs[key] = InventoryJob(
            op="market",
            category="药材",
            phase="COLLECTING",
            last_command_ts=now - 60,
        )
        cases.append(("inventory", inventory, 1))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            alchemy = AutoAlchemyOptimizer(
                official_qq="3889001741",
                recipe_path=str(tmp / "recipes.txt"),
                snapshot_path=str(tmp / "snapshot.json"),
                config={"page_timeout_sec": 8, "send_interval_sec": 0, "max_page_retries": 1},
            )
            alchemy.jobs[key] = AutoAlchemyJob(
                phase="COLLECTING",
                current_page=1,
                max_page=1,
                scan_pages=[1],
                last_command_ts=now - 60,
            )
            cases.append(("alchemy", alchemy, 2))

            for name, controller, max_messages in cases:
                send = SendRecorder()
                await self._run_ticks(controller, key, send)
                self.assertLessEqual(
                    len(send.messages),
                    max_messages,
                    f"{name} tick re-entered without advancing state: {send.messages!r}",
                )

    async def test_activity_commands_wait_for_confirmed_exit_when_secluded(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECLUSION,
                is_resting=True,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)

        activity_messages = [
            msg
            for msg in plugin._official_messages
            if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
        ]
        pending = plugin.cultivate._pending_after_exit.get(key, [])

        self.assertEqual([], activity_messages)
        self.assertEqual(1, sum("出关" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("悬赏令查看" in msg for msg in pending), pending)
        self.assertTrue(any("探索秘境" in msg for msg in pending), pending)
        self.assertTrue(any("宗门任务接取" in msg for msg in pending), pending)
        cultivate_state = await plugin.cultivate._get(key)
        self.assertTrue(cultivate_state.is_resting)
        self.assertTrue(cultivate_state.suspended_for_activity)

    async def test_low_hp_recovery_blocks_all_activity_commands_until_hp_recovers(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECLUSION,
                is_resting=False,
                hp_percent=20.0,
                hp_check_ts=now,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)

        activity_messages = [
            msg
            for msg in plugin._official_messages
            if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
        ]
        pending = plugin.cultivate._pending_after_exit.get(key, [])

        self.assertEqual([], activity_messages)
        self.assertEqual(1, sum(msg.endswith("闭关") for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("悬赏令查看" in msg for msg in pending), pending)
        self.assertTrue(any("探索秘境" in msg for msg in pending), pending)
        self.assertTrue(any("宗门任务接取" in msg for msg in pending), pending)
        cultivate_state = await plugin.cultivate._get(key)
        self.assertTrue(cultivate_state.is_resting)
        self.assertFalse(cultivate_state.suspended_for_activity)
        rest_started_at = cultivate_state.last_action_ts

        bounty_state = await plugin.bounty._get(key)
        bounty_state.last_action_ts = time.time() - 301
        await plugin.bounty._set(key, bounty_state)
        await plugin.bounty.tick(key, send_cb)

        activity_messages = [
            msg
            for msg in plugin._official_messages
            if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
        ]
        cultivate_state = await plugin.cultivate._get(key)
        self.assertEqual([], activity_messages)
        self.assertEqual(rest_started_at, cultivate_state.last_action_ts)

    async def test_stale_hp_check_blocks_all_activity_commands_until_status_reply(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECLUSION,
                is_resting=False,
                hp_percent=100.0,
                hp_check_ts=now - 120,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)

        activity_messages = [
            msg
            for msg in plugin._official_messages
            if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
        ]
        status_messages = [msg for msg in plugin._official_messages if msg.endswith("我的状态")]
        pending = plugin.cultivate._pending_after_exit.get(key, [])

        self.assertEqual([], activity_messages)
        self.assertEqual(1, len(status_messages), plugin._official_messages)
        self.assertTrue(any("悬赏令查看" in msg for msg in pending), pending)
        self.assertTrue(any("探索秘境" in msg for msg in pending), pending)
        self.assertTrue(any("宗门任务接取" in msg for msg in pending), pending)

    async def test_stale_hp_high_reply_replays_all_pending_activity_commands(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECLUSION,
                is_resting=False,
                hp_percent=100.0,
                hp_check_ts=now - 120,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)

        async def no_sleep(_delay):
            return None

        with patch("astrbot_plugin_xiao_xiuxian_auto.cultivate.asyncio.sleep", no_sleep):
            await plugin.cultivate.on_official_text(key, "气血: 100/100", send_cb)

        self.assertTrue(any("悬赏令查看" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("探索秘境" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("宗门任务接取" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertEqual([], plugin.cultivate._pending_after_exit.get(key, []))

    async def test_real_send_queue_keeps_fresh_hp_between_activity_commands(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)
        plugin.command_delay_sec = 0
        plugin.seclusion_guard_enabled = False
        plugin._activity_priority = dict(main.ACTIVITY_PRIORITY)
        plugin._last_official_command = {}
        plugin._enqueue_official_command = types.MethodType(
            main.XiaoXiuxianAuto._enqueue_official_command,
            plugin,
        )

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECT_SECLUSION,
                is_resting=False,
                hp_percent=99.6,
                hp_check_ts=now,
                hp_check_pending=False,
                suspended_for_activity=True,
                last_action_ts=now,
            ),
        )

        async def send_and_drain(text: str) -> None:
            await plugin._make_send_cb(key)(text)
            task = plugin._send_tasks.get(key)
            if task is not None:
                await task

        await send_and_drain("@3889001741 悬赏令查看")
        await send_and_drain("@3889001741 探索秘境")

        self.assertEqual(
            ["@3889001741 悬赏令查看", "@3889001741 探索秘境"],
            plugin._raw_messages,
        )
        self.assertFalse(
            any(msg.endswith("我的状态") for msg in plugin._raw_messages),
            plugin._raw_messages,
        )

    async def test_native_self_hook_ignores_generated_bounty_accept_command(self):
        main = _import_main_with_astrbot_stubs()
        plugin = _make_plugin_shell(main)
        plugin._recent_self_commands = {}
        strategy_args = []

        async def is_bound(_self_id, _group_id):
            return True

        async def set_strategy(_key, strategy):
            strategy_args.append(strategy)
            return "unexpected"

        plugin._is_bound_match = is_bound
        plugin.bounty.cmd_set_strategy = set_strategy
        event = {
            "self_id": "10001",
            "user_id": "10001",
            "group_id": "20002",
            "post_type": "message_sent",
            "raw_message": "悬赏令接取1",
        }

        await plugin._handle_native_self(event, force_self=True)

        self.assertEqual([], strategy_args)
        self.assertEqual([], plugin._raw_messages)

    async def test_account_business_controllers_use_isolated_config_and_files(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin.store = MemoryStore()
        plugin.market_price = None
        plugin.default_official_qq = "3889001741"
        plugin.multi_cfg = {
            "accounts": {
                "111": {"enabled": True},
                "222": {"enabled": True},
            }
        }
        plugin.cfg = {
            "bounty": {"default_strategy": "价值"},
            "endless_tower": {"mp_threshold": 600},
        }
        plugin._account_config_overrides = {
            "111": {
                "bounty": {"default_strategy": "修为"},
                "endless_tower": {"mp_threshold": 500},
            },
            "222": {
                "bounty": {"default_strategy": "耗时"},
                "endless_tower": {"mp_threshold": 900},
            },
        }
        plugin._account_controllers = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin.data_dir = temp_dir
            first = plugin._controller("bounty", "111:group")
            second = plugin._controller("bounty", "222:group")
            first_endless = plugin._controller("endless", "111:group")
            second_endless = plugin._controller("endless", "222:group")
            first_inventory = plugin._controller("inventory_ops", "111:group")
            second_inventory = plugin._controller("inventory_ops", "222:group")
            first_alchemy = plugin._controller("auto_alchemy", "111:group")
            second_alchemy = plugin._controller("auto_alchemy", "222:group")

            self.assertIsNot(first, second)
            self.assertEqual("修为", first.default_strategy)
            self.assertEqual("耗时", second.default_strategy)
            self.assertEqual(500, first_endless.default_mp_threshold)
            self.assertEqual(900, second_endless.default_mp_threshold)
            self.assertNotEqual(first_inventory.runtime_path, second_inventory.runtime_path)
            self.assertIn("111", first_inventory.runtime_path)
            self.assertIn("222", second_inventory.runtime_path)
            self.assertNotEqual(first_alchemy.herb_max_prices_path, second_alchemy.herb_max_prices_path)

    async def test_account_config_save_rejects_infrastructure_fields_and_reloads_one_account(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin.cfg = {
            "bounty": {"default_strategy": "价值"},
            "inventory_ops": {
                "enabled": True,
                "market_command_price_format": "lingshi",
            },
            "market_price": {"remote_url": "global"},
        }
        plugin.multi_cfg = {"accounts": {"111": {"enabled": True}}}
        saved = {}
        reloaded = []

        async def bound_dict():
            return {"111": ["group"]}

        async def reload_account(self_id):
            reloaded.append(self_id)

        class FakeRequest:
            async def json(self, default=None):
                return {
                    "self_id": "111",
                    "config": {
                        "bounty": {"default_strategy": "修为"},
                        "inventory_ops": {
                            "enabled": False,
                            "market_command_price_format": "raw",
                        },
                        "market_price": {"remote_url": "malicious"},
                        "send_fail_policy": {"auto_unbind_on_permanent_send_error": False},
                    },
                }

        plugin._get_bound_dict = bound_dict
        plugin._reload_account_controllers = reload_account

        def save_overrides(data):
            saved.update(data)

        with patch.object(main, "request", FakeRequest()), \
             patch.object(main, "_load_account_overrides", return_value={}), \
             patch.object(main, "_save_account_overrides", side_effect=save_overrides):
            result = await plugin._page_save_config()

        self.assertTrue(result["ok"])
        self.assertEqual(["111"], reloaded)
        self.assertEqual("修为", saved["111"]["bounty"]["default_strategy"])
        self.assertFalse(saved["111"]["inventory_ops"]["enabled"])
        self.assertNotIn("market_command_price_format", saved["111"]["inventory_ops"])
        self.assertNotIn("market_price", saved["111"])
        self.assertNotIn("send_fail_policy", saved["111"])

    async def test_account_reload_clears_only_target_account_queue_and_controllers(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        first_task = asyncio.create_task(asyncio.Event().wait())
        second_task = asyncio.create_task(asyncio.Event().wait())
        plugin._send_tasks = {"111:group": first_task, "222:group": second_task}
        plugin._send_queues = {"111:group": ["old-a"], "222:group": ["keep-b"]}
        plugin._activity_owner = {"111:group": "bounty", "222:group": "secret"}
        second_controllers = {"marker": "keep"}
        plugin._account_controllers = {"111": {"marker": "old"}, "222": second_controllers}
        plugin._account_config_overrides = {}
        plugin._build_account_controllers = lambda self_id: {"marker": f"new-{self_id}"}

        with patch.object(main, "_load_account_overrides", return_value={"111": {}}):
            await plugin._reload_account_controllers("111")

        self.assertTrue(first_task.cancelled())
        self.assertNotIn("111:group", plugin._send_tasks)
        self.assertNotIn("111:group", plugin._send_queues)
        self.assertNotIn("111:group", plugin._activity_owner)
        self.assertEqual({"marker": "new-111"}, plugin._account_controllers["111"])
        self.assertIs(second_controllers, plugin._account_controllers["222"])
        self.assertEqual(["keep-b"], plugin._send_queues["222:group"])
        self.assertFalse(second_task.done())

        second_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second_task

    async def test_stale_hp_low_reply_enters_recovery_without_waiting_for_activity_timeout(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode=MODE_SECLUSION,
                is_resting=False,
                hp_percent=100.0,
                hp_check_ts=now - 120,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)
        await plugin.cultivate.on_official_text(key, "气血: 20/100", send_cb)

        activity_messages = [
            msg
            for msg in plugin._official_messages
            if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
        ]
        pending = plugin.cultivate._pending_after_exit.get(key, [])
        cultivate_state = await plugin.cultivate._get(key)

        self.assertEqual([], activity_messages)
        self.assertEqual(1, sum(msg.endswith("闭关") for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(cultivate_state.is_resting)
        self.assertFalse(cultivate_state.suspended_for_activity)
        self.assertTrue(any("悬赏令查看" in msg for msg in pending), pending)
        self.assertTrue(any("探索秘境" in msg for msg in pending), pending)
        self.assertTrue(any("宗门任务接取" in msg for msg in pending), pending)

    async def test_low_hp_without_recovery_mode_replays_pending_instead_of_deadlocking(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        now = time.time()
        plugin = _make_plugin_shell(main)

        await plugin.cultivate._set(
            key,
            CultivateState(
                mode="",
                is_resting=False,
                hp_percent=100.0,
                hp_check_ts=now - 120,
                last_action_ts=now - 60,
            ),
        )
        await plugin.bounty._set(key, BountyState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.secret._set(key, SecretState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))
        await plugin.sect._set(key, SectState(enabled=True, phase="SLEEPING", wake_at_ts=now - 1))

        send_cb = plugin._make_send_cb(key)
        await plugin.bounty.tick(key, send_cb)
        await plugin.secret.tick(key, send_cb)
        await plugin.sect.tick(key, send_cb)

        async def no_sleep(_delay):
            return None

        with patch("astrbot_plugin_xiao_xiuxian_auto.cultivate.asyncio.sleep", no_sleep):
            await plugin.cultivate.on_official_text(key, "气血: 20/100", send_cb)

        self.assertTrue(any("悬赏令查看" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("探索秘境" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertTrue(any("宗门任务接取" in msg for msg in plugin._official_messages), plugin._official_messages)
        self.assertEqual([], plugin.cultivate._pending_after_exit.get(key, []))

    async def test_low_hp_recovery_modes_send_one_recovery_command_and_dedupe_pending(self):
        main = _import_main_with_astrbot_stubs()
        recovery_modes = (
            (MODE_CULTIVATE, "修炼"),
            (MODE_SECLUSION, "闭关"),
            (MODE_SECT_SECLUSION, "宗门闭关"),
        )

        for mode, command in recovery_modes:
            with self.subTest(mode=mode):
                key = f"10001:{20000 + len(command)}"
                now = time.time()
                plugin = _make_plugin_shell(main)
                await plugin.cultivate._set(
                    key,
                    CultivateState(
                        mode=mode,
                        is_resting=False,
                        hp_percent=20.0,
                        hp_check_ts=now,
                        last_action_ts=now - 60,
                    ),
                )
                send_cb = plugin._make_send_cb(key)
                activity_commands = (
                    f"@3889001741 悬赏令查看",
                    f"@3889001741 探索秘境",
                    f"@3889001741 宗门任务接取",
                )

                for _ in range(2):
                    for activity_command in activity_commands:
                        await send_cb(activity_command)

                activity_messages = [
                    msg
                    for msg in plugin._official_messages
                    if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
                ]
                recovery_messages = [msg for msg in plugin._official_messages if msg.endswith(command)]
                pending = plugin.cultivate._pending_after_exit.get(key, [])
                cultivate_state = await plugin.cultivate._get(key)

                self.assertEqual([], activity_messages)
                self.assertEqual(1, len(recovery_messages), plugin._official_messages)
                self.assertEqual(3, len(pending), pending)
                self.assertTrue(cultivate_state.is_resting)

    async def test_recovery_completion_replays_pending_activity_commands_once(self):
        main = _import_main_with_astrbot_stubs()
        recovery_modes = (
            (MODE_CULTIVATE, "本次修炼增加修为"),
            (MODE_SECLUSION, "闭关结算"),
            (MODE_SECT_SECLUSION, "闭关结算"),
        )

        async def no_sleep(_delay):
            return None

        for mode, completion_text in recovery_modes:
            with self.subTest(mode=mode):
                key = f"10001:{21000 + len(mode)}"
                now = time.time()
                plugin = _make_plugin_shell(main)
                await plugin.cultivate._set(
                    key,
                    CultivateState(
                        mode=mode,
                        is_resting=False,
                        hp_percent=20.0,
                        hp_check_ts=now,
                        last_action_ts=now - 60,
                    ),
                )
                send_cb = plugin._make_send_cb(key)
                for activity_command in (
                    f"@3889001741 悬赏令查看",
                    f"@3889001741 探索秘境",
                    f"@3889001741 宗门任务接取",
                ):
                    await send_cb(activity_command)

                if mode in (MODE_SECLUSION, MODE_SECT_SECLUSION):
                    st = await plugin.cultivate._get(key)
                    st.last_action_ts = time.time() - plugin.cultivate.REST_FULL_WAIT_SEC - 1
                    await plugin.cultivate._set(key, st)
                    await send_cb("@3889001741 悬赏令查看")
                    self.assertTrue(
                        any(msg.endswith("出关") for msg in plugin._official_messages),
                        plugin._official_messages,
                    )

                with patch("astrbot_plugin_xiao_xiuxian_auto.cultivate.asyncio.sleep", no_sleep):
                    await plugin.cultivate.on_official_text(key, completion_text, send_cb)

                activity_messages = [
                    msg
                    for msg in plugin._official_messages
                    if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))
                ]
                self.assertEqual(3, len(activity_messages), plugin._official_messages)
                self.assertEqual([], plugin.cultivate._pending_after_exit.get(key, []))


def _import_main_with_astrbot_stubs():
    if "astrbot_plugin_xiao_xiuxian_auto.main" in sys.modules:
        return sys.modules["astrbot_plugin_xiao_xiuxian_auto.main"]

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    star = types.ModuleType("astrbot.api.star")
    event = types.ModuleType("astrbot.api.event")
    web = types.ModuleType("astrbot.api.web")

    class Star:
        pass

    class AstrMessageEvent:
        pass

    def register(*args, **kwargs):
        def deco(cls):
            return cls

        return deco

    class Filter:
        class EventMessageType:
            ALL = "all"

        @staticmethod
        def regex(*args, **kwargs):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def event_message_type(*args, **kwargs):
            def deco(fn):
                return fn

            return deco

    class Logger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def exception(self, *args, **kwargs):
            pass

    def json_response(data=None, *args, **kwargs):
        return data

    def error_response(*args, **kwargs):
        return {"error": args[0] if args else ""}

    star.Star = Star
    star.register = register
    event.AstrMessageEvent = AstrMessageEvent
    event.filter = Filter
    api.star = star
    api.event = event
    api.logger = Logger()
    web.request = None
    web.json_response = json_response
    web.error_response = error_response
    astrbot.api = api

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.api.star", star)
    sys.modules.setdefault("astrbot.api.event", event)
    sys.modules.setdefault("astrbot.api.web", web)

    import astrbot_plugin_xiao_xiuxian_auto.main as main

    return main


def _make_plugin_shell(main):
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    plugin.default_official_qq = "3889001741"
    plugin._send_locks = {}
    plugin._send_queues = {}
    plugin._send_tasks = {}
    plugin._activity_owner = {}
    plugin._known_keys = set()
    plugin._raw_messages = []
    plugin._official_messages = []
    plugin._is_key_send_blocked = lambda _key: False
    plugin._official_qq_for_key = lambda _key: "3889001741"
    plugin._rewrite_official_target = lambda _key, text: text

    async def enqueue(_key, text):
        plugin._official_messages.append(str(text))

    async def raw(_key, text):
        plugin._raw_messages.append(str(text))

    plugin._enqueue_official_command = enqueue
    plugin._raw_send_by_key = raw

    store = MemoryStore()
    plugin.cultivate = CultivateController(store, "3889001741")
    plugin.bounty = BountyController(store, "3889001741")
    plugin.secret = SecretController(store, "3889001741")
    plugin.sect = SectController(store, "3889001741")
    return plugin


if __name__ == "__main__":
    unittest.main()
