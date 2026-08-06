from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto import captcha_guard as captcha_module
from astrbot_plugin_xiao_xiuxian_auto.captcha_guard import (
    CaptchaButton,
    CaptchaGuard,
    is_click_action_accepted,
)


class FakeVisionCompletions:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = await self.responder(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=answer))]
        )


class FakeVisionClient:
    def __init__(self, responder):
        self.chat = SimpleNamespace(completions=FakeVisionCompletions(responder))


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, level, message, *args):
        rendered = message % args if args else message
        self.records.append(f"{level}:{rendered}")

    def info(self, message, *args):
        self._record("info", message, *args)

    def warning(self, message, *args):
        self._record("warning", message, *args)

    def error(self, message, *args):
        self._record("error", message, *args)

    def exception(self, message, *args):
        self._record("exception", message, *args)


def make_event(msg_seq: str = "88", labels=("🚗", "🐱")):
    buttons = [
        {"label": label, "button_id": f"b-{index}", "data": f"d-{index}"}
        for index, label in enumerate(labels)
    ]
    message_obj = SimpleNamespace(
        group_id="20002",
        self_id="10001",
        raw_message={
            "msg_seq": msg_seq,
            "bot_appid": "app-1",
            "keyboard": {"rows": [{"buttons": buttons}]},
        },
    )
    return SimpleNamespace(is_at_or_wake_command=True, message_obj=message_obj)


def _pb_varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _pb_uint(field: int, value: int) -> bytes:
    return _pb_varint(field << 3) + _pb_varint(value)


def _pb_bytes(field: int, value: bytes | str) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return _pb_varint((field << 3) | 2) + _pb_varint(len(data)) + data


def make_llbot_raw_pb_keyboard() -> str:
    render = _pb_bytes(1, "💻") + _pb_bytes(2, "💻") + _pb_uint(3, 1)
    action = _pb_uint(1, 1) + _pb_bytes(5, "fixture-callback") + _pb_uint(7, 1)
    button = _pb_bytes(1, "0_0") + _pb_bytes(2, render) + _pb_bytes(3, action)
    row = _pb_bytes(1, button)
    button_extra = _pb_bytes(1, _pb_bytes(1, row))
    keyboard_common = _pb_uint(1, 46) + _pb_bytes(2, button_extra) + _pb_uint(3, 1)
    keyboard_element = _pb_bytes(53, keyboard_common)

    markdown = "![captcha](https://qqbot.ugcimg.cn/102074059/fixture.png)"
    markdown_common = _pb_uint(1, 45) + _pb_bytes(2, _pb_bytes(1, markdown))
    markdown_element = _pb_bytes(53, markdown_common)
    rich_text = _pb_bytes(2, markdown_element) + _pb_bytes(2, keyboard_element)
    content_head = _pb_uint(1, 82) + _pb_uint(5, 5304)
    message = _pb_bytes(2, content_head) + _pb_bytes(3, _pb_bytes(1, rich_text))
    return message.hex()


async def noop_notify(_message):
    return None


class CaptchaGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_debug_logging_is_enabled_by_default_for_captcha_diagnostics(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertTrue(config["captcha"]["debug_print"])
        self.assertTrue(schema["captcha"]["items"]["debug_print"]["default"])
        self.assertIn("官方成功回执", schema["captcha"]["hint"])

    async def test_debug_log_covers_full_captcha_flow_and_redacts_callback_data(self):
        async def exact_answer(_kwargs):
            return "🐱"

        client = FakeVisionClient(exact_answer)
        logger = RecordingLogger()

        async def click(_payload):
            return {
                "result": 0,
                "status": 0,
                "promptText": "操作成功",
            }

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture-secret",
                    "vision_model": "fixture-model",
                    "debug_print": True,
                    "auto_resume": True,
                },
                logger=logger,
            )
            await guard.handle(
                "10001:20002",
                make_event("seq-log"),
                "请点击图中第2个表情 ![captcha](https://qqbot.ugcimg.cn/log.png)",
                "10001",
                noop_notify,
                click,
            )

        logs = "\n".join(logger.records)
        for expected in (
            "[captcha][CONFIG] 配置已加载",
            "api_key=已配置",
            "[captcha][CAPTCHA] 验证码处理开始",
            "目标编号=2",
            "图片URL=https://qqbot.ugcimg.cn/log.png",
            "msg_seq=seq-log",
            "按钮数=2",
            "[captcha][VISION] 提交图片给视觉模型",
            "[captcha][VISION] 模型回答='🐱'",
            "[captcha][CLICK] 准备点击按钮 label='🐱' button_id='b-1'",
            "'callback_data': '<redacted>'",
            "[captcha][CLICK] OneBot 返回={'result': 0, 'status': 0, 'promptText': '操作成功'}",
            "[captcha][CAPTCHA] 验证码处理结束",
            "耗时=",
        ):
            self.assertIn(expected, logs)
        self.assertNotIn("fixture-secret", logs)
        self.assertNotIn("d-1", logs)

    def test_button_matching_uses_exact_then_substring_then_first_fallback(self):
        buttons = (
            CaptchaButton("🚗", "b-0", "d-0"),
            CaptchaButton("小猫", "b-1", "d-1"),
        )

        exact, exact_fallback = CaptchaGuard._select_button("小猫", buttons)
        substring, substring_fallback = CaptchaGuard._select_button("答案：小猫", buttons)
        fallback, used_fallback = CaptchaGuard._select_button("拖鞋", buttons)

        self.assertEqual(exact.button_id, "b-1")
        self.assertFalse(exact_fallback)
        self.assertEqual(substring.button_id, "b-1")
        self.assertFalse(substring_fallback)
        self.assertEqual(fallback.button_id, "b-0")
        self.assertTrue(used_fallback)

    def test_click_action_requires_an_explicit_success_result(self):
        self.assertTrue(is_click_action_accepted(True))
        self.assertTrue(is_click_action_accepted({"result": 0, "status": 0}))
        self.assertTrue(is_click_action_accepted({"retcode": 0}))
        self.assertTrue(is_click_action_accepted({"status": 0}))
        self.assertTrue(is_click_action_accepted({"retcode": 0, "status": "ok"}))

        self.assertFalse(is_click_action_accepted(None))
        self.assertFalse(is_click_action_accepted({}))
        self.assertFalse(is_click_action_accepted({"result": 1, "status": 0}))
        self.assertFalse(is_click_action_accepted({"retcode": 1400}))
        self.assertFalse(is_click_action_accepted({"retcode": 0, "status": "failed"}))
        self.assertFalse(is_click_action_accepted({"retcode": 0.5}))
        self.assertFalse(is_click_action_accepted({"status": False}))
        self.assertFalse(is_click_action_accepted(False))

    def test_button_payload_reads_nested_current_event_shape(self):
        payload = CaptchaGuard({"enabled": True})._button_payload(
            make_event(labels=("小猫",)), "小猫"
        )
        self.assertEqual(
            {
                "group_id": "20002",
                "bot_appid": "app-1",
                "msg_seq": "88",
                "button_id": "b-0",
                "callback_data": "d-0",
            },
            payload,
        )

    def test_button_payload_does_not_mix_quoted_keyboard_buttons(self):
        event = make_event(labels=("当前",))
        event.message_obj.raw_message["reply"] = {
            "msg_seq": "quoted-seq",
            "bot_appid": "quoted-app",
            "keyboard": {
                "rows": [{"buttons": [{"label": "引用", "button_id": "quoted-button", "data": "quoted-secret"}]}]
            },
        }

        payload = CaptchaGuard({"enabled": True})._button_payload(event, "引用")

        self.assertEqual("88", payload["msg_seq"])
        self.assertEqual("app-1", payload["bot_appid"])
        self.assertEqual("b-0", payload["button_id"])
        self.assertEqual("d-0", payload["callback_data"])

    def test_button_payload_reads_current_data_event_shape(self):
        event = make_event(labels=("数据键盘",))
        event.data = event.message_obj.raw_message
        event.message_obj.raw_message = None

        payload = CaptchaGuard({"enabled": True})._button_payload(event, "数据键盘")

        self.assertEqual("88", payload["msg_seq"])
        self.assertEqual("app-1", payload["bot_appid"])
        self.assertEqual("b-0", payload["button_id"])

    def test_button_payload_reads_llbot_keyboard_segment_and_debug_raw_metadata(self):
        button = {
            "id": "ll-button-1",
            "render_data": {"label": "小猫", "visited_label": "小猫", "style": 1},
            "action": {"type": 1, "data": "ll-callback-secret"},
        }
        event = SimpleNamespace(
            is_at_or_wake_command=True,
            message_obj=SimpleNamespace(
                group_id="1040779831",
                self_id="1660315547",
                raw_message={
                    "group_id": 1040779831,
                    "message_id": 901,
                    "message_seq": 902,
                    "message": [
                        {"type": "keyboard", "data": {"rows": [{"buttons": [button]}]}},
                    ],
                    "raw": {
                        "msgSeq": 902,
                        "elements": [
                            {
                                "inlineKeyboardElement": {
                                    "botAppid": "ll-app-1",
                                    "rows": [
                                        {
                                            "buttons": [
                                                {
                                                    "id": "ll-button-1",
                                                    "label": "小猫",
                                                    "data": "ll-callback-secret",
                                                }
                                            ]
                                        }
                                    ],
                                }
                            }
                        ],
                    },
                },
            ),
        )

        payload = CaptchaGuard({"enabled": True})._button_payload(event, "小猫")

        self.assertEqual(
            {
                "group_id": "1040779831",
                "bot_appid": "ll-app-1",
                "msg_seq": "902",
                "button_id": "ll-button-1",
                "callback_data": "ll-callback-secret",
            },
            payload,
        )

    def test_llbot_keyboard_without_debug_raw_reports_missing_bot_appid(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                group_id="1040779831",
                raw_message={
                    "message_seq": 902,
                    "message": [
                        {
                            "type": "keyboard",
                            "data": {
                                "rows": [
                                    {
                                        "buttons": [
                                            {
                                                "id": "ll-button-1",
                                                "render_data": {"label": "小猫"},
                                                "action": {"type": 1, "data": "ll-secret"},
                                            }
                                        ]
                                    }
                                ]
                            },
                        }
                    ],
                },
            )
        )

        challenge, error = CaptchaGuard({"enabled": True})._parse_challenge(event)

        self.assertIsNone(challenge)
        self.assertEqual("缺少 bot_appid", error)

    def test_button_payload_decodes_llbot_service_46_keyboard_from_raw_pb(self):
        event = {
            "group_id": "1040779831",
            "message_detail": {
                "raw_pb": make_llbot_raw_pb_keyboard(),
            },
        }

        payload = CaptchaGuard({"enabled": True})._button_payload(event, "💻")

        self.assertEqual(
            {
                "group_id": "1040779831",
                "bot_appid": "102074059",
                "msg_seq": "5304",
                "button_id": "0_0",
                "callback_data": "fixture-callback",
            },
            payload,
        )

    async def test_llbot_raw_pb_captcha_reaches_vision_and_click(self):
        event = {
            "group_id": "1040779831",
            "message_detail": {"raw_pb": make_llbot_raw_pb_keyboard()},
        }
        clicks = []

        async def recognize(_kwargs):
            return "💻"

        async def click(payload):
            clicks.append(payload)
            return {"status": "ok", "retcode": 0}

        with patch.object(captcha_module, "AsyncOpenAI", return_value=FakeVisionClient(recognize)):
            guard = CaptchaGuard({
                "enabled": True,
                "vision_api_key": "fixture",
                "vision_model": "fixture-model",
            })
            handled = await guard.handle(
                "1660315547:1040779831",
                event,
                "[@bot](mqqapi://markdown/mention?at_type=1&at_tinyid=1660315547) "
                "请点击图中第3个表情 "
                "![captcha](https://qqbot.ugcimg.cn/102074059/fixture.png)",
                "1660315547",
                noop_notify,
                click,
            )

        self.assertTrue(handled)
        self.assertEqual(1, len(clicks))
        self.assertEqual("102074059", clicks[0]["bot_appid"])
        self.assertEqual("5304", clicks[0]["msg_seq"])
        self.assertEqual("0_0", clicks[0]["button_id"])
        self.assertEqual("fixture-callback", clicks[0]["callback_data"])

    def test_button_payload_decodes_realtime_pushmsg_raw_pb_wrapper(self):
        wrapped = _pb_bytes(1, bytes.fromhex(make_llbot_raw_pb_keyboard())).hex()
        event = {
            "group_id": "1040779831",
            "message_detail": {"raw": {"rawPb": wrapped}},
        }

        payload = CaptchaGuard({"enabled": True})._button_payload(event, "💻")

        self.assertIsNotNone(payload)
        self.assertEqual("0_0", payload["button_id"])
        self.assertEqual("5304", payload["msg_seq"])

    def test_malformed_raw_pb_is_ignored_without_breaking_normal_parse_error(self):
        event = {
            "group_id": "1040779831",
            "message_detail": {"raw_pb": "0a80"},
        }

        challenge, error = CaptchaGuard({"enabled": True})._parse_challenge(event)

        self.assertIsNone(challenge)
        self.assertEqual("缺少 keyboard", error)

    async def test_logged_llbot_captcha_shape_reaches_vision_and_click(self):
        labels = ("苹果", "雨伞", "小猫")
        buttons = [
            {
                "id": f"ll-button-{index}",
                "render_data": {"label": label},
                "action": {"type": 1, "data": f"ll-secret-{index}"},
            }
            for index, label in enumerate(labels, start=1)
        ]
        event = SimpleNamespace(
            is_at_or_wake_command=True,
            message_obj=SimpleNamespace(
                group_id="1040779831",
                self_id="1660315547",
                raw_message={
                    "message_seq": 902,
                    "message": [
                        {"type": "keyboard", "data": {"rows": [{"buttons": buttons}]}},
                    ],
                    "raw": {
                        "elements": [
                            {
                                "inlineKeyboardElement": {
                                    "botAppid": "ll-app-1",
                                    "rows": [{"buttons": buttons}],
                                }
                            }
                        ]
                    },
                },
            ),
        )
        raw_text = (
            "[@星之卡比](mqqapi://markdown/mention?at_type=1&at_tinyid=1660315547)"
            "![小小 #1100px #150px](https://qqbot.ugcimg.cn/captcha.png)"
            "请点击图中第3个表情对应的按钮"
        )
        logger = RecordingLogger()
        clicks = []

        async def recognize_third(_kwargs):
            return "小猫"

        async def click(payload):
            clicks.append(payload)
            return {"status": "ok", "retcode": 0}

        with patch.object(captcha_module, "AsyncOpenAI", return_value=FakeVisionClient(recognize_third)):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                    "debug_print": True,
                },
                logger=logger,
            )
            handled = await guard.handle(
                "1660315547:1040779831",
                event,
                raw_text,
                "1660315547",
                noop_notify,
                click,
            )

        self.assertTrue(handled)
        self.assertEqual("ll-button-3", clicks[0]["button_id"])
        self.assertEqual("ll-secret-3", clicks[0]["callback_data"])
        self.assertNotIn("缺少 keyboard", "\n".join(logger.records))

    async def test_missing_live_keyboard_uses_get_msg_detail_fallback(self):
        event = SimpleNamespace(
            is_at_or_wake_command=True,
            message_obj=SimpleNamespace(
                group_id="1040779831",
                self_id="1660315547",
                raw_message={
                    "message_id": 901,
                    "message_seq": 902,
                    "message": [{"type": "text", "data": {"text": "请点击图中第1个表情"}}],
                },
            ),
        )
        detail = {
            "group_id": 1040779831,
            "message_id": 901,
            "message_seq": 902,
            "bot_appid": "detail-app-1",
            "keyboard": {
                "rows": [
                    {
                        "buttons": [
                            {"label": "小猫", "button_id": "detail-button-1", "data": "detail-secret"}
                        ]
                    }
                ]
            },
        }
        clicks = []
        fetches = []

        async def recognize(_kwargs):
            return "小猫"

        async def fetch_message():
            fetches.append(True)
            return detail

        async def click(payload):
            clicks.append(payload)
            return {"status": "ok", "retcode": 0}

        with patch.object(captcha_module, "AsyncOpenAI", return_value=FakeVisionClient(recognize)):
            guard = CaptchaGuard({
                "enabled": True,
                "vision_api_key": "fixture",
                "vision_model": "fixture-model",
            })
            handled = await guard.handle(
                "1660315547:1040779831",
                event,
                "[At:1660315547] 请点击图中第1个表情 "
                "![captcha](https://qqbot.ugcimg.cn/fallback.png)",
                "1660315547",
                noop_notify,
                click,
                fetch_message=fetch_message,
            )

        self.assertTrue(handled)
        self.assertEqual([True], fetches)
        self.assertEqual("detail-button-1", clicks[0]["button_id"])

    async def test_missing_keyboard_captures_raw_pb_without_logging_its_contents(self):
        event = SimpleNamespace(
            is_at_or_wake_command=True,
            message_obj=SimpleNamespace(
                group_id="1040779831",
                raw_message={"message_seq": 902},
            ),
        )
        raw_pb = "0a087365637265742d63616c6c6261636b"
        logger = RecordingLogger()
        captures = []

        async def fetch_message():
            return {
                "message_id": 901,
                "raw_pb": raw_pb,
                "raw": {"rawPb": raw_pb},
                "message": [{"type": "text", "data": {"text": "captcha"}}],
            }

        async def capture_raw_pb(records):
            captures.append(records)
            return "data/captcha_diagnostics/latest_1660315547_1040779831.json"

        guard = CaptchaGuard(
            {"enabled": True, "debug_print": True},
            logger=logger,
        )
        handled = await guard.handle(
            "1660315547:1040779831",
            event,
            "[At:1660315547] 请点击图中第1个表情 "
            "![captcha](https://qqbot.ugcimg.cn/raw-pb.png)",
            "1660315547",
            noop_notify,
            lambda _payload: None,
            fetch_message=fetch_message,
            capture_raw_pb=capture_raw_pb,
        )

        self.assertTrue(handled)
        self.assertEqual(1, len(captures))
        self.assertEqual(
            [{"source": "event.message_detail.raw_pb", "encoding": "hex", "data": raw_pb}],
            captures[0],
        )
        logs = "\n".join(logger.records)
        self.assertIn("latest_1660315547_1040779831.json", logs)
        self.assertNotIn(raw_pb, logs)

    async def test_model_receives_button_labels_and_unmatched_name_falls_back_to_first(self):
        async def answer_with_name(_kwargs):
            return "拖鞋"

        client = FakeVisionClient(answer_with_name)
        clicks = []

        async def click(payload):
            clicks.append(payload)

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                }
            )
            handled = await guard.handle(
                "10001:20002",
                make_event(),
                "请点击图中第2个表情 ![captcha](https://qqbot.ugcimg.cn/example.png)",
                "10001",
                noop_notify,
                click,
            )

        self.assertTrue(handled)
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0]["button_id"], "b-0")
        prompt = client.chat.completions.calls[0]["messages"][0]["content"][0]["text"]
        self.assertIn("🚗", prompt)
        self.assertIn("🐱", prompt)
        status = guard.status("10001:20002")
        self.assertTrue(status.active)
        self.assertEqual(status.phase, "awaiting_confirmation")

    async def test_same_msg_seq_is_recognized_only_once(self):
        started = asyncio.Event()
        release = asyncio.Event()
        answer_count = 0

        async def delayed_answer(_kwargs):
            nonlocal answer_count
            answer_count += 1
            if answer_count == 1:
                started.set()
                await release.wait()
            return "🚗"

        client = FakeVisionClient(delayed_answer)
        clicks = []

        async def click(payload):
            clicks.append(payload)

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                }
            )
            first = asyncio.create_task(
                guard.handle(
                    "10001:20002",
                    make_event("same-seq"),
                    "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/same.png)",
                    "10001",
                    noop_notify,
                    click,
                )
            )
            await started.wait()
            duplicate = await guard.handle(
                "10001:20002",
                make_event("same-seq"),
                "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/same.png)",
                "10001",
                noop_notify,
                click,
            )
            release.set()
            await first

        self.assertTrue(duplicate)
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertEqual(len(clicks), 1)

    async def test_same_msg_seq_is_not_recognized_again_after_resume(self):
        async def exact_answer(_kwargs):
            return "🚗"

        client = FakeVisionClient(exact_answer)
        clicks = []

        async def click(payload):
            clicks.append(payload)

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {"enabled": True, "vision_api_key": "fixture", "vision_model": "fixture-model"}
            )
            key = "10001:20002"
            raw_captcha = "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/same-resumed.png)"
            await guard.handle(key, make_event("same-resumed"), raw_captcha, "10001", noop_notify, click)
            await guard.handle(key, make_event("receipt"), "验证成功", "10001", noop_notify, click)
            duplicate = await guard.handle(
                key, make_event("same-resumed"), raw_captcha, "10001", noop_notify, click
            )

        self.assertTrue(duplicate)
        self.assertFalse(guard.is_paused(key))
        self.assertEqual(1, len(client.chat.completions.calls))
        self.assertEqual(1, len(clicks))

    async def test_stale_visual_result_cannot_click_newer_keyboard(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def answer_by_image(kwargs):
            image_url = kwargs["messages"][0]["content"][1]["image_url"]["url"]
            if image_url.endswith("first.png"):
                first_started.set()
                await release_first.wait()
                return "🚗"
            return "🐱"

        client = FakeVisionClient(answer_by_image)
        clicks = []

        async def click(payload):
            clicks.append(payload)

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                }
            )
            first = asyncio.create_task(
                guard.handle(
                    "10001:20002",
                    make_event("seq-1"),
                    "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/first.png)",
                    "10001",
                    noop_notify,
                    click,
                )
            )
            await first_started.wait()
            await guard.handle(
                "10001:20002",
                make_event("seq-2"),
                "请点击图中第2个表情 ![captcha](https://qqbot.ugcimg.cn/second.png)",
                "10001",
                noop_notify,
                click,
            )
            release_first.set()
            await first

        self.assertEqual([payload["msg_seq"] for payload in clicks], ["seq-2"])
        self.assertEqual(guard.status("10001:20002").msg_seq, "seq-2")

    async def test_failure_reply_stays_paused_and_success_reply_resumes(self):
        async def exact_answer(_kwargs):
            return "🚗"

        client = FakeVisionClient(exact_answer)

        async def click(_payload):
            return None

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                    "auto_resume": True,
                }
            )
            key = "10001:20002"
            await guard.handle(
                key,
                make_event("seq-result"),
                "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/result.png)",
                "10001",
                noop_notify,
                click,
            )
            self.assertTrue(guard.is_paused(key))

            handled_failure = await guard.handle(
                key,
                make_event("failure"),
                "验证码不正确",
                "10001",
                noop_notify,
                click,
            )
            self.assertTrue(handled_failure)
            self.assertTrue(guard.is_paused(key))
            self.assertEqual(guard.status(key).phase, "failed")

            guard.pause(key, "等待官方确认", phase="awaiting_confirmation", msg_seq="seq-retry")
            handled_success = await guard.handle(
                key,
                make_event("success"),
                "不需要验证",
                "10001",
                noop_notify,
                click,
            )

        self.assertTrue(handled_success)
        self.assertFalse(guard.is_paused(key))

    async def test_success_receipt_during_recognition_does_not_resume_tasks(self):
        guard = CaptchaGuard({"enabled": True, "auto_resume": True})
        key = "10001:20002"
        guard.pause(key, "检测到验证码，等待识别与点击", phase="recognizing", msg_seq="seq-recognizing")

        handled = await guard.handle(
            key, make_event("unrelated"), "奖励123灵石", "10001", noop_notify, lambda _payload: None
        )

        self.assertFalse(handled)
        self.assertTrue(guard.is_paused(key))
        self.assertEqual("recognizing", guard.status(key).phase)

    async def test_old_configuration_error_cannot_overwrite_manual_resume_during_notify(self):
        notify_started = asyncio.Event()
        release_notify = asyncio.Event()

        async def delayed_notify(_message):
            notify_started.set()
            await release_notify.wait()

        guard = CaptchaGuard({"enabled": True})
        key = "10001:20002"
        challenge = asyncio.create_task(
            guard.handle(
                key,
                make_event("seq-notify-race"),
                "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/notify-race.png)",
                "10001",
                delayed_notify,
                lambda _payload: None,
            )
        )
        await notify_started.wait()
        guard.resume(key)
        release_notify.set()
        await challenge

        self.assertFalse(guard.is_paused(key))

    async def test_failure_receipt_during_click_cannot_be_overwritten_by_old_click(self):
        click_started = asyncio.Event()
        release_click = asyncio.Event()

        async def exact_answer(_kwargs):
            return "🚗"

        async def delayed_click(_payload):
            click_started.set()
            await release_click.wait()
            return {"retcode": 0, "status": "ok"}

        client = FakeVisionClient(exact_answer)
        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {"enabled": True, "vision_api_key": "fixture", "vision_model": "fixture-model"}
            )
            key = "10001:20002"
            challenge = asyncio.create_task(
                guard.handle(
                    key,
                    make_event("seq-race"),
                    "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/race.png)",
                    "10001",
                    noop_notify,
                    delayed_click,
                )
            )
            await click_started.wait()
            await guard.handle(key, make_event("receipt-race"), "验证码错误", "10001", noop_notify, delayed_click)
            release_click.set()
            await challenge

        self.assertTrue(guard.is_paused(key))
        self.assertEqual("failed", guard.status(key).phase)

    async def test_manual_success_receipt_during_click_cannot_be_overwritten_by_old_click(self):
        click_started = asyncio.Event()
        release_click = asyncio.Event()

        async def exact_answer(_kwargs):
            return "🚗"

        async def delayed_click(_payload):
            click_started.set()
            await release_click.wait()
            return {"retcode": 0, "status": "ok"}

        client = FakeVisionClient(exact_answer)
        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                    "auto_resume": False,
                }
            )
            key = "10001:20002"
            challenge = asyncio.create_task(
                guard.handle(
                    key,
                    make_event("seq-manual-race"),
                    "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/manual-race.png)",
                    "10001",
                    noop_notify,
                    delayed_click,
                )
            )
            await click_started.wait()
            await guard.handle(key, make_event("manual-receipt"), "验证成功", "10001", noop_notify, delayed_click)
            release_click.set()
            await challenge

        self.assertTrue(guard.is_paused(key))
        self.assertEqual("verified", guard.status(key).phase)

    async def test_two_to_five_digit_spirit_stone_reward_confirms_success(self):
        guard = CaptchaGuard({"enabled": True, "auto_resume": True})
        key = "10001:20002"

        async def click(_payload):
            return None

        for receipt in (
            "奖励12灵石",
            "恭喜完成验证，奖励123灵石",
            "奖励1234灵石",
            "奖励12345灵石",
        ):
            guard.pause(
                key,
                "验证码已提交，等待官方确认",
                phase="awaiting_confirmation",
                msg_seq="seq-reward",
            )
            handled = await guard.handle(
                key,
                make_event("reward"),
                receipt,
                "10001",
                noop_notify,
                click,
            )
            self.assertTrue(handled, receipt)
            self.assertFalse(guard.is_paused(key), receipt)

    async def test_spirit_stone_reward_outside_two_to_five_digits_is_not_confirmation(self):
        guard = CaptchaGuard({"enabled": True, "auto_resume": True})
        key = "10001:20002"

        async def click(_payload):
            return None

        for receipt in ("奖励1灵石", "奖励123456灵石"):
            guard.pause(
                key,
                "验证码已提交，等待官方确认",
                phase="awaiting_confirmation",
                msg_seq="seq-reward",
            )
            handled = await guard.handle(
                key,
                make_event("reward"),
                receipt,
                "10001",
                noop_notify,
                click,
            )
            self.assertFalse(handled, receipt)
            self.assertTrue(guard.is_paused(key), receipt)

    async def test_rejected_click_keeps_tasks_paused_with_a_specific_reason(self):
        async def exact_answer(_kwargs):
            return "🚗"

        client = FakeVisionClient(exact_answer)
        notices = []

        async def notify(message):
            notices.append(message)

        async def rejected_click(_payload):
            raise RuntimeError("OneBot 未明确接受点击请求")

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                }
            )
            key = "10001:20002"
            handled = await guard.handle(
                key,
                make_event("seq-rejected"),
                "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/rejected.png)",
                "10001",
                notify,
                rejected_click,
            )

        self.assertTrue(handled)
        self.assertTrue(guard.is_paused(key))
        self.assertEqual(guard.status(key).phase, "processing_error")
        self.assertIn("OneBot 未明确接受", guard.status(key).reason)
        self.assertTrue(any("处理失败" in message for message in notices))

    async def test_success_reply_waits_for_manual_resume_when_auto_resume_is_disabled(self):
        guard = CaptchaGuard({"enabled": True, "auto_resume": False})
        key = "10001:20002"
        guard.pause(key, "等待官方确认", phase="awaiting_confirmation", msg_seq="seq-manual")

        async def click(_payload):
            return None

        handled = await guard.handle(
            key,
            make_event("success"),
            "验证成功",
            "10001",
            noop_notify,
            click,
        )

        self.assertTrue(handled)
        self.assertTrue(guard.is_paused(key))
        self.assertEqual(guard.status(key).phase, "verified")

    async def test_click_error_redacts_callback_data_from_logs_and_notice(self):
        async def exact_answer(_kwargs):
            return "🚗"

        logger = RecordingLogger()
        notices = []

        async def notify(message):
            notices.append(message)

        async def rejected_click(_payload):
            raise RuntimeError("OneBot {'callback_data': 'callback-secret'} rejected")

        client = FakeVisionClient(exact_answer)
        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                    "debug_print": True,
                },
                logger=logger,
            )
            await guard.handle(
                "10001:20002",
                make_event("seq-secret"),
                "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/secret.png)",
                "10001",
                notify,
                rejected_click,
            )

        output = "\n".join([*logger.records, *notices])
        self.assertNotIn("callback-secret", output)
        self.assertIn("<redacted>", output)

    async def test_manual_resume_invalidates_inflight_visual_result(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_answer(_kwargs):
            started.set()
            await release.wait()
            return "🚗"

        client = FakeVisionClient(delayed_answer)
        clicks = []

        async def click(payload):
            clicks.append(payload)

        with patch.object(captcha_module, "AsyncOpenAI", return_value=client):
            guard = CaptchaGuard(
                {
                    "enabled": True,
                    "vision_api_key": "fixture",
                    "vision_model": "fixture-model",
                }
            )
            key = "10001:20002"
            task = asyncio.create_task(
                guard.handle(
                    key,
                    make_event("seq-manual"),
                    "请点击图中第1个表情 ![captcha](https://qqbot.ugcimg.cn/manual.png)",
                    "10001",
                    noop_notify,
                    click,
                )
            )
            await started.wait()
            guard.resume(key)
            release.set()
            await task

        self.assertEqual(clicks, [])
        self.assertFalse(guard.is_paused(key))

    async def test_targeted_captcha_pauses_when_vision_is_unconfigured(self):
        guard = CaptchaGuard({"enabled": True})
        notices = []

        async def notify(message):
            notices.append(message)

        async def click(_payload):
            self.fail("click must not run without a configured vision client")

        handled = await guard.handle(
            "10001:20002",
            make_event(),
            "请点击图中第2个表情 ![captcha](https://qqbot.ugcimg.cn/example.png)",
            "10001",
            notify,
            click,
        )

        self.assertTrue(handled)
        self.assertTrue(guard.is_paused("10001:20002"))
        self.assertIn("openai", guard.status("10001:20002").reason)
        self.assertGreaterEqual(len(notices), 2)

    async def test_non_targeted_message_does_not_pause(self):
        guard = CaptchaGuard({"enabled": True})
        event = make_event()
        event.is_at_or_wake_command = False

        async def noop(_value):
            return None

        handled = await guard.handle(
            "10001:20002", event, "请点击图中第1个表情", "10001", noop, noop
        )
        self.assertFalse(handled)
        self.assertFalse(guard.is_paused("10001:20002"))


if __name__ == "__main__":
    unittest.main()
