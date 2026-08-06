from __future__ import annotations

import asyncio
import functools
import sys
import tempfile
import time
import types
import unittest
from types import SimpleNamespace
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

    async def test_only_secret_entry_waits_for_a_fresh_hp_reply(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        plugin = _make_plugin_shell(main)
        send_cb = plugin._make_send_cb(key)

        await plugin.bounty.cmd_enable(key, send_cb)
        await plugin.secret.cmd_enable(key, send_cb)
        await plugin.sect.cmd_enable(key, send_cb)

        self.assertEqual(
            [
                "@3889001741 悬赏令查看",
                "@3889001741 我的状态",
                "@3889001741 宗门任务接取",
            ],
            plugin._official_messages,
        )

    async def test_secret_entry_with_fresh_sufficient_hp_starts_immediately(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        plugin = _make_plugin_shell(main)
        await plugin.cultivate._set(
            key,
            CultivateState(hp_percent=80.0, hp_check_ts=time.time(), hp_check_pending=False),
        )

        await plugin.secret.cmd_enable(key, plugin._make_send_cb(key))

        self.assertEqual(["@3889001741 探索秘境"], plugin._official_messages)

    async def test_secret_low_hp_uses_temporary_cultivation_then_restores_idle(self):
        main = _import_main_with_astrbot_stubs()
        key = "10001:20002"
        plugin = _make_plugin_shell(main)
        send_cb = plugin._make_send_cb(key)

        await plugin.secret.cmd_enable(key, send_cb)
        await plugin.cultivate.on_official_text(key, "气血: 20/100", send_cb)
        recovering = await plugin.cultivate._get(key)
        self.assertEqual(MODE_CULTIVATE, recovering.mode)
        self.assertTrue(recovering.is_resting)
        self.assertIn("@3889001741 修炼", plugin._official_messages)

        async def no_sleep(_delay):
            return None

        with patch("astrbot_plugin_xiao_xiuxian_auto.cultivate.asyncio.sleep", no_sleep):
            await plugin.cultivate.on_official_text(key, "气血: 80/100", send_cb)

        restored = await plugin.cultivate._get(key)
        self.assertEqual("", restored.mode)
        self.assertFalse(restored.is_resting)
        self.assertIn("@3889001741 探索秘境", plugin._official_messages)

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

    async def test_low_hp_does_not_block_bounty_or_sect(self):
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
        await plugin.sect.tick(key, send_cb)

        self.assertIn("@3889001741 悬赏令查看", plugin._official_messages)
        self.assertIn("@3889001741 宗门任务接取", plugin._official_messages)
        self.assertNotIn("@3889001741 我的状态", plugin._official_messages)

    async def test_stale_hp_check_blocks_only_secret_entry(self):
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

        activity_messages = [msg for msg in plugin._official_messages if any(word in msg for word in ("悬赏令", "探索秘境", "宗门任务"))]
        status_messages = [msg for msg in plugin._official_messages if msg.endswith("我的状态")]
        pending = plugin.cultivate._pending_after_exit.get(key, [])

        self.assertEqual(
            ["@3889001741 悬赏令查看", "@3889001741 宗门任务接取"],
            activity_messages,
        )
        self.assertEqual(1, len(status_messages), plugin._official_messages)
        self.assertTrue(any("探索秘境" in msg for msg in pending), pending)

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

    def test_qq_id_normalization_rejects_dynamic_action_partial(self):
        main = _import_main_with_astrbot_stubs()
        dynamic_action = functools.partial(lambda action: action, "self_id")

        self.assertEqual("10001", main._normalize_qq_id(10001))
        self.assertEqual("10001", main._normalize_qq_id(" 10001 "))
        self.assertEqual("", main._normalize_qq_id(dynamic_action))
        self.assertEqual("", main._normalize_qq_id(True))
        self.assertEqual("", main._normalize_qq_id("default"))

    async def test_native_hook_supports_llbot_self_events_without_duplicate_registration(self):
        main = _import_main_with_astrbot_stubs()

        class FakeBot:
            def __init__(self):
                self.handlers = {}

            def call_action(self, *_args, **_kwargs):
                return None

            @property
            def self_id(self):
                return functools.partial(self.call_action, "self_id")

            def on_message(self, message_type):
                return self.on(f"message.{message_type}")

            def on(self, event_name):
                def register(handler):
                    self.handlers.setdefault(event_name, []).append(handler)
                    return handler

                return register

        bot = FakeBot()
        platform = type("AiocqhttpAdapter", (), {"bot": bot})()
        manager = SimpleNamespace(get_insts=lambda: [platform])
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin.context = SimpleNamespace(platform_manager=manager)
        plugin._cached_bots = {}
        plugin._any_bot = None
        plugin._native_hooked = False
        plugin._native_self_hooked = False
        plugin._native_official_hooked = False
        plugin._native_hooked_bot_ids = set()
        handled = []

        async def handle_self(event, force_self=False, sid_hint=None, bot=None):
            handled.append((event, force_self, sid_hint, bot))

        async def handle_official(*_args, **_kwargs):
            return None

        plugin._handle_native_self = handle_self
        plugin._handle_native_official = handle_official

        self.assertTrue(plugin._hook_native_self_message())
        self.assertTrue(plugin._hook_native_self_message())
        self.assertEqual(1, len(bot.handlers["message.group"]))
        self.assertEqual(1, len(bot.handlers["message.private"]))
        self.assertEqual(1, len(bot.handlers["message_sent"]))
        self.assertNotIn(str(bot.self_id), plugin._cached_bots)

        llbot_event = {
            "self_id": 10001,
            "user_id": 10001,
            "group_id": 20002,
            "post_type": "message_sent",
            "message_id": 30003,
            "raw_message": "关闭悬赏",
        }
        await bot.handlers["message_sent"][0](llbot_event)
        self.assertTrue(handled[-1][1])
        self.assertIs(bot, plugin._cached_bots["10001"])

        legacy_event = dict(llbot_event, post_type="message")
        await bot.handlers["message.group"][0](legacy_event)
        self.assertTrue(handled[-1][1])

    async def test_native_hook_intercepts_llbot_event_before_aiocqhttp_drops_it(self):
        main = _import_main_with_astrbot_stubs()

        class AiocqhttpLikeBot:
            def __init__(self):
                self.handlers = {}

            def on_message(self, message_type):
                return self.on(f"message.{message_type}")

            def on(self, event_name):
                def register(handler):
                    self.handlers.setdefault(event_name, []).append(handler)
                    return handler

                return register

            async def _handle_event(self, payload):
                post_type = payload["post_type"]
                try:
                    detail_type = payload[f"{post_type}_type"]
                except KeyError:
                    return None
                for handler in self.handlers.get(f"{post_type}.{detail_type}", []):
                    await handler(payload)
                return None

        bot = AiocqhttpLikeBot()
        original_handle_event = bot._handle_event
        platform = type("AiocqhttpAdapter", (), {"bot": bot})()
        manager = SimpleNamespace(get_insts=lambda: [platform])
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin.context = SimpleNamespace(platform_manager=manager)
        plugin._cached_bots = {}
        plugin._any_bot = None
        plugin._native_hooked = False
        plugin._native_self_hooked = False
        plugin._native_official_hooked = False
        plugin._native_hooked_bot_ids = set()
        handled = []

        async def handle_self(event, force_self=False, sid_hint=None, bot=None):
            handled.append((event, force_self, sid_hint, bot))

        async def handle_official(*_args, **_kwargs):
            return None

        plugin._handle_native_self = handle_self
        plugin._handle_native_official = handle_official

        self.assertTrue(plugin._hook_native_self_message())
        llbot_event = {
            "self_id": 1660315547,
            "user_id": 1660315547,
            "group_id": 1040779831,
            "post_type": "message_sent",
            "message_type": "group",
            "raw_message": "绑定此群",
        }

        await bot._handle_event(llbot_event)

        self.assertEqual(1, len(handled))
        self.assertTrue(handled[0][1])

        plugin._unhook_native_raw_events()

        self.assertIs(bot._handle_event.__func__, original_handle_event.__func__)

    def test_official_event_claim_deduplicates_raw_and_astrbot_wrappers(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._recent_official_events = {}
        raw_event = {
            "self_id": 10001,
            "user_id": 3889001741,
            "group_id": 20002,
            "message_id": 30003,
        }
        astr_event = SimpleNamespace(
            message_obj=SimpleNamespace(
                self_id="10001",
                group_id="20002",
                raw_message=raw_event,
            )
        )

        self.assertTrue(plugin._claim_official_event(raw_event, "10001", "20002", "回执"))
        self.assertFalse(plugin._claim_official_event(astr_event, "10001", "20002", "回执"))

    async def test_astrbot_official_fallback_stays_active_after_native_hook_registration(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._native_official_hooked = True
        processed = []

        async def process(event, sid_hint=None, bot=None):
            processed.append((event, sid_hint, bot))

        plugin._handle_official_event = process
        event = object()

        await plugin.on_official_reply(event)

        self.assertEqual([(event, None, None)], processed)

    async def test_send_routes_by_self_id_and_does_not_guess_between_clients(self):
        main = _import_main_with_astrbot_stubs()

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **payload):
                self.calls.append((action, payload))

        first = FakeClient()
        second = FakeClient()
        platforms = [
            type("AiocqhttpAdapter", (), {"bot": first})(),
            type("AiocqhttpAdapter", (), {"bot": second})(),
        ]
        manager = SimpleNamespace(get_insts=lambda: platforms)
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin.context = SimpleNamespace(platform_manager=manager)
        plugin._cached_bots = {}
        plugin._any_bot = first

        self.assertIsNone(plugin._find_client_by_self_id("10001"))

        result = await plugin._do_send(first, "20002", [], self_id="10001")

        self.assertTrue(result)
        self.assertEqual(10001, first.calls[0][1]["self_id"])

    async def test_captcha_click_routes_action_by_self_id(self):
        main = _import_main_with_astrbot_stubs()

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **payload):
                self.calls.append((action, payload))
                return {"status": "ok", "retcode": 0}

        class FakeGuard:
            async def handle(self, _key, _event, _raw_text, _self_id, _notify, click, **_kwargs):
                await click({
                    "group_id": "1040779831",
                    "bot_appid": "app-1",
                    "msg_seq": "902",
                    "button_id": "button-1",
                    "callback_data": "secret",
                })
                return True

        client = FakeClient()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._captcha_for_key = lambda _key: FakeGuard()
        plugin._find_client_by_self_id = lambda _self_id: client
        plugin._raw_send_by_key = lambda *_args: None

        handled = await plugin._handle_captcha(
            "1660315547:1040779831",
            object(),
            "请点击图中第3个表情对应的按钮",
        )

        self.assertTrue(handled)
        self.assertEqual("click_inline_keyboard_button", client.calls[0][0])
        self.assertEqual(1660315547, client.calls[0][1]["self_id"])

    async def test_captcha_get_msg_fallback_routes_by_message_and_self_id(self):
        main = _import_main_with_astrbot_stubs()

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **payload):
                self.calls.append((action, payload))
                return {"message_id": payload["message_id"], "keyboard": {}}

        class FakeGuard:
            async def handle(
                self,
                _key,
                _event,
                _raw_text,
                _self_id,
                _notify,
                _click,
                *,
                fetch_message,
            ):
                detail = await fetch_message()
                return bool(detail)

        client = FakeClient()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._captcha_for_key = lambda _key: FakeGuard()
        plugin._find_client_by_self_id = lambda _self_id: client
        plugin._raw_send_by_key = lambda *_args: None
        event = {
            "self_id": 1660315547,
            "group_id": 1040779831,
            "message_id": 901,
        }

        handled = await plugin._handle_captcha(
            "1660315547:1040779831",
            event,
            "请点击图中第3个表情对应的按钮",
        )

        self.assertTrue(handled)
        self.assertEqual("get_msg", client.calls[0][0])
        self.assertEqual(901, client.calls[0][1]["message_id"])
        self.assertEqual(1660315547, client.calls[0][1]["self_id"])

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

    async def test_herb_price_page_api_returns_groups_for_selected_account(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)

        async def validate_account(self_id):
            return self_id == "111"

        class FakeRequest:
            async def json(self, default=None):
                return {"self_id": "111"}

        class FakeOptimizer:
            def get_herb_price_config(self):
                return {
                    "groups": {"九品药材": {"尘磊岩麟果": 960.0}},
                    "unclassified": {},
                    "prices": {"尘磊岩麟果": 960.0},
                }

        plugin._page_validate_account = validate_account
        plugin._controller = lambda module, key: FakeOptimizer()

        with patch.object(main, "request", FakeRequest()):
            result = await plugin._page_load_herb_prices()

        self.assertEqual("111", result["self_id"])
        self.assertEqual(960.0, result["groups"]["九品药材"]["尘磊岩麟果"])
        self.assertEqual({}, result["unclassified"])
        self.assertEqual({"尘磊岩麟果": 960.0}, result["prices"])

    async def test_herb_price_page_api_rejects_invalid_groups(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)

        async def validate_account(self_id):
            return self_id == "111"

        class FakeRequest:
            async def json(self, default=None):
                return {
                    "self_id": "111",
                    "groups": {"九品药材": {"同名药": 10}},
                }

        class FakeOptimizer:
            def set_herb_price_groups(self, groups):
                raise ValueError("药材名重复")

        errors = []

        def capture_error(message, status_code=500):
            errors.append((message, status_code))
            return {"error": message, "status_code": status_code}

        plugin._page_validate_account = validate_account
        plugin._controller = lambda module, key: FakeOptimizer()

        with patch.object(main, "request", FakeRequest()), patch.object(
            main,
            "error_response",
            side_effect=capture_error,
        ):
            result = await plugin._page_save_herb_prices()

        self.assertEqual(400, result["status_code"])
        self.assertIn("药材名重复", result["error"])
        self.assertEqual([("药材名重复", 400)], errors)

    async def test_official_command_is_counted_when_queued_during_captcha_pause(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._send_locks = {}
        plugin._send_queues = {}
        plugin._send_blocked_keys = {}
        blocker = asyncio.create_task(asyncio.Event().wait())
        plugin._send_tasks = {"111:group": blocker}
        recorded = []

        class Stats:
            async def record(self, self_id, text):
                recorded.append((self_id, text))

        plugin.send_stats = Stats()

        await plugin._enqueue_official_command(
            "111:group",
            "@3889001741 坊市查看药材1",
        )

        self.assertEqual(
            ["@3889001741 坊市查看药材1"],
            plugin._send_queues["111:group"],
        )
        self.assertEqual(
            [("111", "@3889001741 坊市查看药材1")],
            recorded,
        )
        blocker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await blocker

    async def test_stats_failure_does_not_remove_queued_command(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
        plugin._send_locks = {}
        plugin._send_queues = {}
        plugin._send_blocked_keys = {}
        blocker = asyncio.create_task(asyncio.Event().wait())
        plugin._send_tasks = {"111:group": blocker}
        attempted = []

        class Stats:
            async def record(self, self_id, text):
                attempted.append((self_id, text))
                raise OSError("disk")

        plugin.send_stats = Stats()

        await plugin._enqueue_official_command(
            "111:group",
            "@3889001741 坊市购买abc 1",
        )

        self.assertEqual(
            ["@3889001741 坊市购买abc 1"],
            plugin._send_queues["111:group"],
        )
        self.assertEqual(
            [("111", "@3889001741 坊市购买abc 1")],
            attempted,
        )
        blocker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await blocker

    async def test_send_stats_page_api_returns_selected_account_snapshot(self):
        main = _import_main_with_astrbot_stubs()
        plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)

        async def validate(self_id):
            return self_id == "111"

        class Request:
            async def json(self, default=None):
                return {"self_id": "111"}

        class Stats:
            async def snapshot(self, self_id):
                self.requested_self_id = self_id
                return {
                    "date": "2026-07-29",
                    "counts": {
                        "market_view": 2,
                        "purchase": 3,
                        "alchemy": 4,
                    },
                    "total": 9,
                }

        stats = Stats()
        plugin._page_validate_account = validate
        plugin.send_stats = stats

        with patch.object(main, "request", Request()):
            result = await plugin._page_load_send_stats()

        self.assertEqual("111", stats.requested_self_id)
        self.assertEqual("111", result["self_id"])
        self.assertEqual(9, result["total"])
        self.assertEqual(3, result["counts"]["purchase"])

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

    async def test_secret_hp_recovery_restores_each_existing_cultivate_mode(self):
        main = _import_main_with_astrbot_stubs()
        recovery_modes = ("", MODE_CULTIVATE, MODE_SECLUSION, MODE_SECT_SECLUSION)

        async def no_sleep(_delay):
            return None

        for mode in recovery_modes:
            with self.subTest(mode=mode or "空闲"):
                key = f"10001:{22000 + len(mode)}"
                plugin = _make_plugin_shell(main)
                send_cb = plugin._make_send_cb(key)
                await plugin.cultivate._set(
                    key,
                    CultivateState(mode=mode, hp_percent=20.0, hp_check_ts=time.time()),
                )
                plugin.cultivate.queue_pending(key, "@3889001741 探索秘境")

                self.assertFalse(await plugin.cultivate.ensure_secret_entry_hp(key, send_cb))
                self.assertIn("@3889001741 修炼", plugin._official_messages)

                with patch("astrbot_plugin_xiao_xiuxian_auto.cultivate.asyncio.sleep", no_sleep):
                    await plugin.cultivate.on_official_text(key, "气血: 80/100", send_cb)

                restored = await plugin.cultivate._get(key)
                self.assertEqual(mode, restored.mode)
                self.assertFalse(restored.is_resting)
                self.assertEqual(bool(mode), restored.suspended_for_activity)
                self.assertIn("@3889001741 探索秘境", plugin._official_messages)


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
