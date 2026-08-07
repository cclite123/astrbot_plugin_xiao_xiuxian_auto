from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_xiao_xiuxian_auto.auto_alchemy_optimizer import (
    AutoAlchemyJob,
    AutoAlchemyOptimizer,
    MaterialReq,
    Recipe,
)


class SendRecorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(str(message))


def make_candidate(profit: float, pill: str = "摄魂鬼丸"):
    return {
        "recipe": Recipe(pill, "", "测试炉", [], raw=f"{pill}:{profit}"),
        "materials": [],
        "cost": 780.0 - float(profit),
        "sale": 130.0,
        "score_profit": float(profit),
        "unknown_sale": False,
        "abandoned": False,
    }


def make_inventory_recipe(name: str, materials):
    return Recipe(
        "摄魂鬼丸",
        "",
        "测试炉",
        [MaterialReq(role, herb, qty) for role, herb, qty in materials],
        raw=name,
    )


class AlchemyRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_all_candidate_selectors_honor_profit_threshold(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_alchemy_command_count": 3},
        )
        low_profit = make_candidate(5.0)

        selected, _ = controller._select_batch_primary_and_reserve(
            [low_profit],
            min_profit=50,
        )

        self.assertEqual([], selected)
        self.assertEqual(
            [],
            controller._select_profitable_best_by_pill(
                [low_profit],
                min_profit=50,
            ),
        )
        self.assertIsNone(
            controller._select_best_for_target(
                [low_profit],
                min_profit=50,
            )
        )

    def test_legacy_batch_count_migrates_to_single_command_count(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"max_batch_formula_count": 10, "max_formula_per_pill": 3},
        )

        self.assertEqual(30, controller.batch_alchemy_command_count)

    def test_no_backpack_selection_matches_configured_command_count(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_alchemy_command_count": 5},
        )

        selected, _ = controller._select_batch_primary_and_reserve(
            [make_candidate(100.0), make_candidate(80.0, "化煞魔丸")],
            min_profit=50,
        )

        self.assertEqual(5, len(selected))

    def test_backpack_batch_selection_matches_configured_command_count(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_alchemy_command_count": 5},
        )
        controller._load_recipes = lambda: [
            make_inventory_recipe(
                "batch",
                [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
            )
        ]
        job = AutoAlchemyJob(
            mode="batch",
            yield_count=6,
            backpack_counts={"甲药": 2},
            prices={"甲药": 1, "乙药": 1, "丙药": 1},
        )

        selected, _, _ = controller._select_batch_with_backpack(job, threshold=50)

        self.assertEqual(5, len(selected))

    def test_normal_candidates_reject_herb_above_global_purchase_limit(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"max_herb_purchase_price_wan": 100},
        )
        controller._load_recipes = lambda: [
            make_inventory_recipe(
                "limited",
                [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
            )
        ]

        candidates, skipped, _ = controller._compute_candidates(
            {"甲药": 1, "乙药": 101, "丙药": 1},
        )

        self.assertEqual([], candidates)
        self.assertEqual(1, skipped)

    def test_backpack_owned_expensive_herb_does_not_trigger_purchase_limit(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_alchemy_command_count": 1, "max_herb_purchase_price_wan": 100},
        )
        controller._load_recipes = lambda: [
            make_inventory_recipe(
                "backpack-limit",
                [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
            )
        ]
        prices = {"甲药": 101, "乙药": 1, "丙药": 1}
        owned_job = AutoAlchemyJob(
            mode="batch",
            yield_count=6,
            backpack_counts={"甲药": 1},
            prices=prices,
        )
        missing_job = AutoAlchemyJob(mode="batch", yield_count=6, prices=prices)

        owned_selected, _, _ = controller._select_batch_with_backpack(owned_job, threshold=50)
        missing_selected, _, _ = controller._select_batch_with_backpack(missing_job, threshold=50)

        self.assertEqual(1, len(owned_selected))
        self.assertEqual([], missing_selected)

    def test_dynamic_purchase_honors_global_purchase_limit(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"max_herb_purchase_price_wan": 100},
        )
        controller.herb_max_prices = {"甲药": 200, "乙药": 200}

        queue = controller._collect_dynamic_buy_items(
            {
                "甲药": {"price": 101, "buy_command": "坊市购买甲 1"},
                "乙药": {"price": 100, "buy_command": "坊市购买乙 1"},
            },
            1,
        )

        self.assertEqual(["乙药"], [item["name"] for item in queue])

    async def test_purchase_send_guard_skips_price_above_global_limit(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"max_herb_purchase_price_wan": 100},
        )
        key = "10001:20002"
        item = {"name": "甲药", "unit_price": 101, "buy_command": "坊市购买甲 1"}
        job = AutoAlchemyJob(
            phase="BUYING",
            batch_buy_queue=[item],
            batch_purchase_plan=[dict(item, qty=1)],
        )
        controller.jobs[key] = job
        send = SendRecorder()

        await controller._send_fresh_purchase_command(key, job, send, item)

        self.assertFalse(any("@3889001741 坊市购买" in message for message in send.messages))
        self.assertTrue(any("超过炼丹购药上限" in message for message in send.messages))

    async def test_stop_during_alchemy_delay_does_not_send_next_command(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"alchemy_send_interval_sec": 0.05},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="ALCHEMY_WAIT",
            alchemy_queue=[
                {"pill": "甲丹", "command": "配方甲", "profit": 1},
                {"pill": "乙丹", "command": "配方乙", "profit": 1},
            ],
            alchemy_index=0,
        )
        controller.jobs[key] = job
        send = SendRecorder()

        task = asyncio.create_task(
            controller._handle_alchemy_result(
                key,
                job,
                "恭喜道友成功炼成丹药",
                "恭喜道友成功炼成丹药",
                send,
            )
        )
        await asyncio.sleep(0.01)
        await controller.cmd_stop(key)
        await task

        self.assertEqual([], send.messages)

    async def test_stop_during_batch_purchase_interval_does_not_send_command(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_buy_send_interval_sec": 0.05},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="BATCH_BUY_SENT",
            batch_buy_sent=1,
            last_command_ts=time.time(),
        )
        controller.jobs[key] = job
        send = SendRecorder()

        task = asyncio.create_task(
            controller._send_fresh_purchase_command(
                key,
                job,
                send,
                {"name": "七星草", "buy_command": "坊市购买abc 1"},
            )
        )
        await asyncio.sleep(0.01)
        await controller.cmd_stop(key)
        await task

        self.assertEqual([], send.messages)

    async def test_unrelated_official_reply_does_not_fail_dynamic_purchase(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"send_interval_sec": 0},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="COLLECTING_DYN_BUY_WAIT",
            scan_pages=[1, 2],
            scan_index=0,
            current_page=1,
            dynamic_buy_queue=[{"name": "七星草", "buy_command": "坊市购买abc 1"}],
            dynamic_buy_index=0,
            dynamic_buy_current_item={"name": "七星草", "buy_command": "坊市购买abc 1"},
        )
        controller.jobs[key] = job

        handled = await controller.on_official_text(
            key,
            "修仙签到成功，获得奖励",
            SendRecorder(),
        )

        self.assertFalse(handled)
        self.assertEqual(0, job.dynamic_buy_fail)
        self.assertEqual(0, job.dynamic_buy_index)

    async def test_unrelated_official_reply_does_not_advance_batch_purchase(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="BATCH_BUY_WAIT",
            batch_buy_queue=[{"name": "七星草", "buy_command": "坊市购买abc 1"}],
            batch_current_item={"name": "七星草", "buy_command": "坊市购买abc 1"},
        )
        controller.jobs[key] = job

        handled = await controller.on_official_text(
            key,
            "修仙签到成功，获得奖励",
            SendRecorder(),
        )

        self.assertFalse(handled)
        self.assertEqual(0, job.batch_buy_index)
        self.assertEqual(0, job.batch_success_count)

    async def test_purchase_wait_does_not_timeout_and_advance_to_next_herb(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"retry_failed_after_batch": False},
        )
        key = "10001:20002"
        first = {"name": "甲药", "buy_command": "坊市购买a 1"}
        second = {"name": "乙药", "buy_command": "坊市购买b 1"}
        job = AutoAlchemyJob(
            phase="BATCH_BUY_WAIT",
            last_command_ts=time.time() - 60,
            batch_buy_queue=[first, second],
            batch_buy_index=0,
            batch_current_item=first,
        )
        controller.jobs[key] = job
        send = SendRecorder()

        await controller.tick(key, send)

        self.assertEqual(0, job.batch_buy_index)
        self.assertEqual("甲药", job.batch_current_item.get("name"))
        self.assertEqual([], send.messages)

    async def test_insufficient_material_reply_stops_without_internal_pause(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="ALCHEMY_WAIT",
            alchemy_queue=[
                {"pill": "甲丹", "command": "配方甲", "profit": 10},
                {"pill": "乙丹", "command": "配方乙", "profit": 10},
            ],
            alchemy_index=0,
        )
        controller.jobs[key] = job
        send = SendRecorder()

        handled = await controller.on_official_text(
            key,
            "请检查药材是否还在背包中，或者数量是否足够",
            send,
        )

        self.assertTrue(handled)
        self.assertNotIn(key, controller.jobs)
        self.assertFalse(any("@3889001741 配方乙" in message for message in send.messages))
        self.assertTrue(any("材料不足" in message for message in send.messages))

    async def test_alchemy_confirmation_wait_never_auto_pauses(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="ALCHEMY_WAIT",
            last_command_ts=time.time() - 60,
            alchemy_queue=[{"pill": "甲丹", "command": "配方甲", "profit": 10}],
        )
        controller.jobs[key] = job
        send = SendRecorder()

        await controller.tick(key, send)

        self.assertEqual("ALCHEMY_WAIT", job.phase)
        self.assertEqual([], send.messages)

    async def test_batch_alchemy_refreshes_backpack_before_sending_formula(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        controller.inventory_parser = object()
        key = "10001:20002"
        job = AutoAlchemyJob(
            mode="batch",
            phase="BUYING",
            backpack_counts={"甲药": 2},
        )
        controller.jobs[key] = job
        send = SendRecorder()

        await controller._start_alchemy_sequence(key, job, send)

        self.assertEqual("ALCHEMY_BAG_VERIFYING", job.phase)
        self.assertEqual({"甲药": 2}, job.planning_backpack_counts)
        self.assertEqual({}, job.backpack_counts)
        self.assertTrue(any("药材背包" in message for message in send.messages))
        self.assertFalse(any("@3889001741 配方" in message for message in send.messages))

    def test_captcha_resume_resets_alchemy_wait_timer(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="BAG_COLLECTING",
            last_command_ts=time.time() - 60,
        )
        controller.jobs[key] = job

        controller.on_captcha_resumed(key)

        self.assertGreater(job.last_command_ts, time.time() - 1)

    def test_alchemy_queue_rejects_formula_when_one_material_is_missing(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        recipe = make_inventory_recipe(
            "missing-one",
            [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
        )
        candidate = make_candidate(10.0)
        candidate["recipe"] = recipe
        candidate["materials"] = [
            {"role": "主药", "name": "甲药", "qty": 1},
            {"role": "药引", "name": "乙药", "qty": 1},
            {"role": "辅药", "name": "丙药", "qty": 1},
        ]
        job = AutoAlchemyJob(
            batch_selected=[candidate],
            backpack_counts={"甲药": 1, "乙药": 1},
        )

        queue = controller._build_alchemy_queue_from_purchased(job)

        self.assertEqual([], queue)
        self.assertTrue(any("丙药×1" in item for item in job.skipped_alchemy))

    def test_verified_backpack_is_hard_limit_even_when_purchase_count_says_complete(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        recipe = make_inventory_recipe(
            "verified-missing-one",
            [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
        )
        candidate = make_candidate(10.0)
        candidate["recipe"] = recipe
        candidate["materials"] = [
            {"role": "主药", "name": "甲药", "qty": 1},
            {"role": "药引", "name": "乙药", "qty": 1},
            {"role": "辅药", "name": "丙药", "qty": 1},
        ]
        job = AutoAlchemyJob(
            batch_selected=[candidate],
            purchased_counts={"甲药": 1, "乙药": 1, "丙药": 1},
            backpack_counts={"甲药": 1, "乙药": 1},
            inventory_verified_after_buy=True,
        )

        queue = controller._build_alchemy_queue_from_purchased(job)

        self.assertEqual([], queue)
        self.assertTrue(any("丙药×1" in item for item in job.skipped_alchemy))

    def test_alchemy_queue_rejects_formula_with_missing_material_role(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        candidate = make_candidate(10.0)
        candidate["recipe"] = make_inventory_recipe(
            "missing-guide-role",
            [("主药", "甲药", 1), ("药引", "乙药", 1), ("辅药", "丙药", 1)],
        )
        candidate["materials"] = [
            {"role": "主药", "name": "甲药", "qty": 1},
            {"role": "辅药", "name": "丙药", "qty": 1},
        ]
        job = AutoAlchemyJob(
            batch_selected=[candidate],
            backpack_counts={"甲药": 1, "乙药": 1, "丙药": 1},
        )

        queue = controller._build_alchemy_queue_from_purchased(job)

        self.assertEqual([], queue)
        self.assertTrue(any("材料角色不完整" in item for item in job.skipped_alchemy))

    def test_price_reuse_requires_mode_profit_threshold_and_plan_lock(self):
        locked = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_mode_profit_threshold": 50, "batch_mode_plan_lock": True},
        )
        unlocked = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_mode_profit_threshold": 50, "batch_mode_plan_lock": False},
        )
        job = AutoAlchemyJob(mode="batch", min_profit=130)
        candidate = make_candidate(1.0)

        self.assertFalse(locked._candidate_reusable_after_price_refresh(job, candidate))
        candidate["score_profit"] = 60.0
        self.assertTrue(locked._candidate_reusable_after_price_refresh(job, candidate))
        self.assertFalse(unlocked._candidate_reusable_after_price_refresh(job, candidate))

    def test_cached_batch_pages_use_batch_profit_threshold(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"min_profit_6pill": 100, "batch_mode_profit_threshold": 50},
        )
        candidate = make_candidate(60.0)
        candidate["materials"] = [{"name": "七星草", "qty": 1, "page": 3}]
        controller._compute_candidates = lambda *args, **kwargs: ([candidate], 0, 0)

        pages = controller._batch_pages_from_cached_snapshot({}, {}, {"七星草": 3})

        self.assertEqual([3], pages)

    def test_stale_snapshot_is_available_only_for_page_planning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": int(time.time()) - 60,
                        "prices": {"七星草": 10},
                        "pages_by_name": {"七星草": 1},
                        "buy_commands": {"七星草": "坊市购买abc 1"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller = AutoAlchemyOptimizer(
                official_qq="3889001741",
                recipe_path="",
                snapshot_path=str(snapshot_path),
                config={"batch_snapshot_max_age_sec": 1},
            )

            self.assertEqual({}, controller._read_snapshot())
            planning_snapshot = controller._read_snapshot(allow_stale=True)

            self.assertTrue(planning_snapshot["stale"])
            self.assertEqual(1, planning_snapshot["pages_by_name"]["七星草"])

    def test_target_snapshot_refreshes_only_best_formula_pages(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"min_profit_6pill": 50},
        )
        best = make_candidate(100.0, "摄魂鬼丸")
        best["materials"] = [
            {"name": "甲药", "page": 2},
            {"name": "乙药", "page": 5},
        ]
        other = make_candidate(80.0, "摄魂鬼丸")
        other["materials"] = [{"name": "丙药", "page": 7}]
        controller._compute_candidates = lambda *args, **kwargs: ([other, best], 0, 0)

        pages = controller._target_pages_from_cached_snapshot(
            "摄魂鬼丸",
            {"甲药": 1},
            {},
            {"甲药": 2, "乙药": 5, "丙药": 7},
        )

        self.assertEqual([2, 5], pages)

    def test_herb_alias_matches_fixed_market_page_catalog(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )

        self.assertEqual("苦曼藤", controller.normalize_name("苦蔓藤"))
        self.assertIn("苦曼藤", controller.herb_props)

    def test_account_page_corrections_override_and_complete_fixed_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "catalog.json"
            account_path = Path(temp_dir) / "account.json"
            catalog_path.write_text(
                json.dumps({"七星草": 1, "三叶青芝": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            account_path.write_text(
                json.dumps({"七星草": 2}, ensure_ascii=False),
                encoding="utf-8",
            )
            controller = AutoAlchemyOptimizer(
                official_qq="3889001741",
                recipe_path="",
                page_index_path=str(account_path),
                page_index_catalog_path=str(catalog_path),
                config={},
            )

            self.assertEqual(
                {"七星草": 2, "三叶青芝": 1},
                controller._read_page_index(),
            )

    async def test_partial_page_scan_falls_back_to_unseen_pages(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"send_interval_sec": 0},
        )
        key = "10001:20002"
        job = AutoAlchemyJob(
            phase="COLLECTING",
            pages_seen=[2, 5],
        )
        controller.jobs[key] = job
        send = SendRecorder()

        continued = await controller._scan_remaining_market_pages(
            key,
            job,
            send,
            "测试候选页失效",
        )

        self.assertTrue(continued)
        self.assertEqual([1, 3, 4, 6, 7, 8], job.scan_pages)
        self.assertTrue(any("补扫剩余页" in message for message in send.messages))
        self.assertEqual("@3889001741 坊市查看药材1", send.messages[-1])

    def test_price_reselection_never_exceeds_command_count(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"batch_alchemy_command_count": 3},
        )
        old_candidates = [make_candidate(60.0, "旧丹一"), make_candidate(60.0, "旧丹二")]
        new_candidates = [
            make_candidate(80.0, "新丹一"),
            make_candidate(80.0, "新丹二"),
            make_candidate(80.0, "新丹三"),
        ]

        merged, frozen_count = controller._merge_existing_queue_candidates_with_new(
            old_candidates,
            new_candidates,
            3,
        )

        self.assertEqual(3, len(merged))
        self.assertEqual(2, frozen_count)

    def test_done_report_only_counts_successfully_executed_queue_items(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={},
        )
        job = AutoAlchemyJob(
            batch_selected=[make_candidate(999.0)],
            alchemy_queue=[
                {"pill": "甲丹", "command": "配方甲", "profit": 10},
                {"pill": "乙丹", "command": "配方乙", "profit": 20},
            ],
            alchemy_success=1,
        )

        report = controller._format_full_done_report(job)

        self.assertIn("预计利润：10万", report)
        self.assertIn("甲丹", report)
        self.assertNotIn("乙丹", report)
        self.assertNotIn("999万", report)

    def test_backpack_selection_uses_lookahead_to_consume_more_inventory(self):
        controller = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path="",
            config={"backpack_min_profit_6pill": 0},
        )
        controller._load_recipes = lambda: [
            make_inventory_recipe(
                "large-now",
                [("主药", "A", 2), ("药引", "B", 1), ("辅药", "C", 1)],
            ),
            make_inventory_recipe(
                "better-pair-1",
                [("主药", "A", 1), ("药引", "D", 1), ("辅药", "E", 1)],
            ),
            make_inventory_recipe(
                "better-pair-2",
                [("主药", "A", 1), ("药引", "F", 1), ("辅药", "C", 1)],
            ),
        ]
        job = AutoAlchemyJob(
            mode="backpack",
            yield_count=6,
            backpack_counts={"A": 2, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1},
            prices={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1},
        )

        selected, _, _ = controller._select_backpack_best_candidates(job)

        self.assertGreaterEqual(
            sum(int(c.get("backpack_used_total") or 0) for c in selected),
            6,
        )

    def test_webui_exposes_one_batch_count_and_no_dead_merge_setting(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        auto_schema = schema["auto_alchemy"]["items"]

        self.assertIn("batch_alchemy_command_count", auto_schema)
        self.assertNotIn("max_batch_formula_count", auto_schema)
        self.assertNotIn("max_formula_per_pill", auto_schema)
        self.assertNotIn("merge_buy_queue", auto_schema)
        self.assertNotIn("batch_buy_result_timeout_sec", auto_schema)
        self.assertNotIn("captcha_wait_sec", auto_schema)
        self.assertNotIn("purchase_response_timeout_sec", auto_schema)
        self.assertNotIn("alchemy_confirm_timeout_sec", auto_schema)


if __name__ == "__main__":
    unittest.main()
