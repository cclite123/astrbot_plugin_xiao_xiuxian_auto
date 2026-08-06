from __future__ import annotations

import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.protocol_compat import (
    build_llbot_inline_keyboard_click,
    is_onebot_action_missing,
    validate_llbot_inline_keyboard_click_response,
)


class ProtocolCompatTests(unittest.TestCase):
    def test_build_llbot_inline_keyboard_click_matches_oidb_112e_wire_fields(self):
        command, request_hex = build_llbot_inline_keyboard_click({
            "group_id": "1",
            "bot_appid": "2",
            "msg_seq": "3",
            "button_id": "b",
            "callback_data": "c",
        })

        self.assertEqual("OidbSvcTrpcTcp.0x112e_1", command)
        self.assertEqual(
            "08ae221001220e180220032a016232016340014801",
            request_hex,
        )

    def test_validate_llbot_inline_keyboard_click_accepts_zero_result(self):
        result = validate_llbot_inline_keyboard_click_response({
            "cmd": "OidbSvcTrpcTcp.0x112e_1",
            "hex": "08ae22100122021800",
        })

        self.assertEqual("ok", result["status"])
        self.assertEqual(0, result["result"])
        self.assertEqual("llbot_send_pb", result["provider"])

    def test_validate_llbot_inline_keyboard_click_rejects_business_error(self):
        with self.assertRaisesRegex(RuntimeError, "result=7"):
            validate_llbot_inline_keyboard_click_response({
                "cmd": "OidbSvcTrpcTcp.0x112e_1",
                "hex": "220a18072a0664656e696564",
            })

    def test_validate_llbot_inline_keyboard_click_rejects_oidb_error(self):
        with self.assertRaisesRegex(RuntimeError, "error_code=5"):
            validate_llbot_inline_keyboard_click_response({
                "cmd": "OidbSvcTrpcTcp.0x112e_1",
                "hex": "18052a0664656e696564",
            })

    def test_validate_llbot_inline_keyboard_click_requires_response_body(self):
        with self.assertRaisesRegex(RuntimeError, "缺少 body"):
            validate_llbot_inline_keyboard_click_response({
                "cmd": "OidbSvcTrpcTcp.0x112e_1",
                "hex": "08ae221001",
            })

    def test_missing_action_detection_requires_llbot_1404_signature(self):
        class MissingAction(RuntimeError):
            retcode = 1404
            message = "click_inline_keyboard_button API 不存在"
            wording = message

        self.assertTrue(is_onebot_action_missing(
            MissingAction("click_inline_keyboard_button API 不存在"),
            "click_inline_keyboard_button",
        ))
        self.assertFalse(is_onebot_action_missing(
            {"retcode": 1400, "message": "click_inline_keyboard_button failed"},
            "click_inline_keyboard_button",
        ))


if __name__ == "__main__":
    unittest.main()
