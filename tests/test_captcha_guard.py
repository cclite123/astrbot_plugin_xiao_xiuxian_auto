from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.captcha_guard import CaptchaGuard


class FakeEvent:
    is_at_or_wake_command = True


class CaptchaGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_button_payload_reads_nested_current_event_shape(self):
        class Message:
            def __init__(self):
                self.group_id = "20002"
                self.raw_message = {
                    "msg_seq": 88,
                    "bot_appid": "app-1",
                    "keyboard": {"rows": [{"buttons": [{"label": "小猫", "button_id": "b-1", "data": "d-1"}]}]},
                }

        class Event:
            def __init__(self):
                self.message_obj = Message()

        payload = CaptchaGuard({"enabled": True})._button_payload(Event(), "小猫")
        self.assertEqual(
            {"group_id": "20002", "bot_appid": "app-1", "msg_seq": "88", "button_id": "b-1", "callback_data": "d-1"},
            payload,
        )

    async def test_targeted_captcha_pauses_when_vision_is_unconfigured(self):
        guard = CaptchaGuard({"enabled": True})
        notices = []

        async def notify(message):
            notices.append(message)

        async def click(_payload):
            self.fail("click must not run without a configured vision client")

        handled = await guard.handle(
            "10001:20002",
            FakeEvent(),
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

        class UntargetedEvent:
            is_at_or_wake_command = False

        async def noop(_value):
            return None

        handled = await guard.handle("10001:20002", UntargetedEvent(), "请点击图中第1个表情", "10001", noop, noop)
        self.assertFalse(handled)
        self.assertFalse(guard.is_paused("10001:20002"))


if __name__ == "__main__":
    unittest.main()
