from __future__ import annotations

import sys
import unittest
import re
import json
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.bounty import BountyOption, choose_option
from astrbot_plugin_xiao_xiuxian_auto.endless import EndlessState, EndlessTowerController
from astrbot_plugin_xiao_xiuxian_auto.sect import SectController


ROOT = Path(__file__).resolve().parents[1]


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value):
        self.data[key] = value


class CoreRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_captcha_click_keeps_and_validates_the_raw_onebot_result(self):
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn(
            'return await target(action, **payload)',
            main_text,
        )
        self.assertIn(
            'if not is_click_action_accepted(result):',
            main_text,
        )
        self.assertNotIn(
            'await target(action, **payload)\n                return True',
            main_text,
        )

    def test_captcha_status_exposes_phase_and_message_sequence(self):
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(main_text.count("阶段：{captcha_pause.phase"), 1)
        self.assertGreaterEqual(main_text.count("消息序号：{captcha_pause.msg_seq"), 1)
        self.assertGreaterEqual(main_text.count("阶段：{pause.phase"), 2)
        self.assertGreaterEqual(main_text.count("消息序号：{pause.msg_seq"), 2)

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

    def test_self_commands_and_receipts_do_not_use_auto_wording(self):
        main_text = (ROOT / "main.py").read_text(encoding="utf-8")
        command_regex = re.search(r"@filter\.regex\(r\"(.+?)\"\)", main_text, re.S).group(1)

        for old_command in (
            "开启自动悬赏",
            "关闭自动悬赏",
            "自动悬赏(修为|价值|耗时)",
            "开启自动秘境",
            "关闭自动秘境",
            "开启自动签到",
            "关闭自动签到",
            "开启自动领丹",
            "关闭自动领丹",
            "开启自动挖矿",
            "关闭自动挖矿",
            "开启自动灵田",
            "关闭自动灵田",
            "开启自动宗门任务",
            "关闭自动宗门任务",
            "开启自动修炼",
            "关闭自动修炼",
            "开启自动闭关",
            "关闭自动闭关",
            "开启自动宗门闭关",
            "关闭自动宗门闭关",
            "开启自动炼丹",
            "开启自动背包炼丹",
            "自动炼丹 .+",
            "暂停自动炼丹",
            "继续自动炼丹",
            "关闭自动炼丹",
            "自动炼丹状态",
            "开启自动购买药材(?:\\s+\\d+)?",
            "关闭自动购买药材",
            "开启自动灵界升级",
            "关闭自动灵界升级",
            "自动灵界状态",
            "开启自动无尽(?:\\s+\\d+)?",
            "关闭自动无尽",
            "自动无尽状态",
            "自动状态",
        ):
            self.assertNotIn(old_command, command_regex)

        for new_command in (
            "开启悬赏",
            "关闭悬赏",
            "悬赏(修为|价值|耗时)",
            "开启秘境",
            "关闭秘境",
            "开启签到",
            "关闭签到",
            "开启领丹",
            "关闭领丹",
            "开启挖矿",
            "关闭挖矿",
            "开启灵田",
            "关闭灵田",
            "开启宗门任务",
            "关闭宗门任务",
            "开启修炼",
            "关闭修炼",
            "开启闭关",
            "关闭闭关",
            "开启宗门闭关",
            "关闭宗门闭关",
            "开启炼丹",
            "开启背包炼丹",
            "炼丹 .+",
            "暂停炼丹",
            "继续炼丹",
            "关闭炼丹",
            "炼丹状态",
            "开启购买药材(?:\\s+\\d+)?",
            "关闭购买药材",
            "开启灵界升级",
            "关闭灵界升级",
            "开启无尽(?:\\s+\\d+)?",
            "关闭无尽",
            "开启真元检测",
            "关闭真元检测",
            "设置真元检测.*",
            "任务状态",
        ):
            self.assertIn(new_command, command_regex)

        self.assertNotIn("开启无尽真元检测", command_regex)
        self.assertNotIn("关闭无尽真元检测", command_regex)
        self.assertNotIn("设置无尽真元检测.*", command_regex)
        self.assertLess(command_regex.index("开启真元检测"), command_regex.index("开启无尽"))

        for path in (
            ROOT / "bounty.py",
            ROOT / "secret.py",
            ROOT / "routine.py",
            ROOT / "sect.py",
            ROOT / "cultivate.py",
            ROOT / "auto_alchemy_optimizer.py",
            ROOT / "linjie.py",
            ROOT / "endless.py",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?:return|send_cb)\([^\\n]*(自动|智能)")

    def test_ui_config_and_docs_do_not_show_old_auto_commands(self):
        old_visible_terms = (
            "全自动",
            "开启自动",
            "关闭自动",
            "自动状态",
            "自动悬赏",
            "自动炼丹",
            "自动灵界",
            "自动无尽",
            "开启无尽真元检测",
            "关闭无尽真元检测",
            "设置无尽真元检测",
        )
        for path in (
            ROOT / "_conf_schema.json",
            ROOT / "pages" / "config" / "app.js",
            ROOT / "config.json",
            ROOT / "metadata.yaml",
            ROOT / "README.md",
        ):
            text = path.read_text(encoding="utf-8")
            for term in old_visible_terms:
                self.assertNotIn(term, text, f"{term!r} remains in {path}")

    def test_nonessential_self_receipts_stay_quiet(self):
        noisy_receipts = (
            "正在刷新悬赏令",
            "正在继续探测下一轮悬赏",
            "正在发送悬赏令结算",
            "今日悬赏已完成",
            "已选择悬赏",
            "正在查询剩余时间",
            "正在发送秘境结算",
            "正在探索秘境",
            "正在重新探测秘境状态",
            "今日秘境已完成",
            "已发送修仙签到",
            "已发送宗门丹药领取",
            "已发送挖灵石",
            "已发送灵田结算",
            "已发送灵田时间查询",
            "签到成功，10 秒后再次确认签到状态",
            "签到流程完成",
            "领丹成功，10 秒后再次确认领取状态",
            "领丹流程完成",
            "小小已确认挖灵石开始",
            "灵田已收取",
            "已加入互斥玩法队列",
            "今日宗门任务已达上限",
            "宗门任务完成，正在继续接取下一轮",
            "若 2 分钟内未收到回执将重试",
            "不在目标列表",
            "已到宗门任务刷新时间",
            "气血已恢复，2 秒后继续宗门任务接取",
            "宗门任务 2 分钟无有效回执",
        )
        for path in (
            ROOT / "bounty.py",
            ROOT / "secret.py",
            ROOT / "routine.py",
            ROOT / "sect.py",
        ):
            text = path.read_text(encoding="utf-8")
            for receipt in noisy_receipts:
                self.assertNotIn(receipt, text)

    def test_infrastructure_config_is_hidden_from_both_settings_pages(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        for key in (
            "official_bot_qq",
            "test_mode",
            "multi_account",
            "coordinator",
            "send_fail_policy",
            "market_price",
        ):
            self.assertNotIn(key, schema)
        self.assertNotIn(
            "market_command_price_format",
            schema.get("inventory_ops", {}).get("items", {}),
        )

        page_text = (ROOT / "pages" / "config" / "app.js").read_text(encoding="utf-8")
        for key in (
            "official_bot_qq",
            "test_mode",
            "multi_account",
            "coordinator",
            "send_fail_policy",
            "market_price",
            "market_command_price_format",
        ):
            self.assertNotIn(key, page_text)

    def test_custom_webui_routes_all_business_data_through_selected_account(self):
        html = (ROOT / "pages" / "config" / "index.html").read_text(encoding="utf-8")
        page_text = (ROOT / "pages" / "config" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="account-select"', html)
        self.assertIn("apiGet('accounts')", page_text)
        self.assertIn("self_id", page_text)
        self.assertIn("config/save", page_text)
        self.assertIn("alchemy_rules/save", page_text)
        self.assertIn("herb_prices/save", page_text)
        self.assertIn("accountLoadGeneration", page_text)
        self.assertIn("generation !== accountLoadGeneration", page_text)
        self.assertNotIn("confirm(", page_text)
        self.assertIn("res.ok === false", page_text)
        self.assertIn("配置已保存并生效", page_text)
        self.assertNotIn("?self_id=", page_text)
        self.assertIn("apiPost('config/load'", page_text)
        self.assertIn("apiPost('alchemy_rules/load'", page_text)
        self.assertIn("apiPost('herb_prices/load'", page_text)


if __name__ == "__main__":
    unittest.main()
