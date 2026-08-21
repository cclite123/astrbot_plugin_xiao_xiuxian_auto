from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from linjie_upgrade import (
    QUERY_COMMANDS,
    LinjieExecutionRepository,
    LinjieExecutionState,
    LinjiePageParser,
    LinjiePlanner,
    LinjieQueryPolicy,
    LinjieQueryRepository,
    LinjieQueryService,
    LinjieSnapshotRepository,
    parse_amount,
    parse_output,
)
from linjie_upgrade.model import (
    Building,
    BuildingUpgrade,
    DisplayOutput,
    LinjieSnapshot,
    WorkerPosition,
)


PROFILE = """🏔️个人面板
👤道号：界
📚技艺道行：72
📚技艺境界：7
💰灵矿石储备：83.52亿
💰灵晶储备：37
📈总产出：16.87万（👑+5.91万）灵矿石/秒
🖱️点击产出：7084（👑+809）灵矿石/次
🏗️建筑：已拥有8/16种
👥杂役：总计650人"""

BUILDINGS = """🏗️建筑列表
|建筑名称|拥有|建造|单产|下一个价格|
|:-|:-:|:-:|-:|-:|
|补给营地|×60|一｜五｜十|14.7（👑+5.14）/s|3.52亿灵矿石|
|藏经阁|×4|一｜五｜十|4567.5（👑+1598.62）/s|822.88亿灵矿石|
|静心府|×0|建造|—|🔒|
📊总建筑数：64
📈总产出：16.87万（👑+5.91万）灵矿石/秒
💰灵矿石储备：83.47亿"""

UPGRADES = """⬆️ 可升级建筑
|建筑名称|建筑等级|升级价格|升级|
|:-|:-:|-:|:-|
|补给营地|6|—|❌️条件不足|
|藏经阁|0|—|❌️条件不足|
💰灵矿石储备：83.48亿"""

AVAILABLE_UPGRADE = """⬆️ 可升级建筑
|建筑名称|建筑等级|升级价格|升级|
|:-|:-:|-:|:-|
|灵符堂|1|199.8亿|升级|
💰灵矿石储备：200.04亿"""

UPGRADE_BUILDINGS = """🏗️建筑列表
|建筑名称|拥有|建造|单产|下一个价格|
|:-|:-:|:-:|-:|-:|
|灵符堂|×22|一｜五｜十|1008（👑+352.8）/s|270.78亿灵矿石|
📊总建筑数：22
📈总产出：16.69万（👑+5.84万）灵矿石/秒
💰灵矿石储备：200.04亿"""

WORKERS = """👥杂役概览
📊杂役总数：650/1046人
📈杂役等阶：LV22
💰灵矿石储备：83.5亿
|建筑名称|在岗/岗位|招募|产出加成|杂役单产|下一个价格|
|:-|:-:|:-:|-:|-:|-:|
|补给营地|120/120|一 / 五 / 十|182.06（👑+63.72）|1.52（👑+0.53）|1434.21万|
|采矿据点|116/200|一 / 五 / 十|1046.13（👑+366.15）|9.02（👑+3.16）|1.67亿|
[杂役升阶]（需要341.43亿灵矿石）"""

EMPTY_UPGRADES = """⬆️ 可升级建筑
|建筑名称|建筑等级|升级价格|升级|
|:-|:-:|-:|:-|
💰灵矿石储备：15"""

EMPTY_WORKERS = """👥杂役概览
📊杂役总数：0/0人
📈杂役等阶：LV0
💰灵矿石储备：18
|建筑名称|在岗/岗位|招募|产出加成|杂役单产|下一个价格|
|:-|:-:|:-:|-:|-:|-:|
杂役升阶（需要1250灵矿石）"""


class LinjieUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = LinjiePageParser()
        self.now = datetime.fromisoformat("2026-08-04T16:00:00+08:00")

    def test_execution_list_states_skips_corrupted_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad_g.json").write_text(
                json.dumps({"account_id": "bad", "group_id": "g"}), encoding="utf-8"
            )

            self.assertEqual(LinjieExecutionRepository(root).list_states(), [])

    def test_execution_load_rejects_invalid_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LinjieExecutionRepository(Path(directory))
            payload = LinjieExecutionState("a", "g").to_dict()
            payload["sent_at"] = "not-a-timestamp"
            repository._path("a", "g").write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "a_g.json"):
                repository.load("a", "g")

    def test_execution_state_migrates_oldest_generation_files(self) -> None:
        payload = LinjieExecutionState("a", "g").to_dict()
        for key in (
            "remaining_candidates", "local_balance", "query_round", "plan_snapshot_at",
            "planned_at", "confirmed_at", "strategy_confirmed", "automation_enabled",
        ):
            payload.pop(key)

        state = LinjieExecutionState.from_dict(payload, account_id="a", group_id="g")

        self.assertEqual((state.remaining_candidates, state.local_balance, state.query_round), ([], None, 0))
        self.assertFalse(state.strategy_confirmed)
        self.assertFalse(state.automation_enabled)

    def test_execution_state_infers_authorization_from_confirmed_at(self) -> None:
        payload = LinjieExecutionState("a", "g").to_dict()
        payload["confirmed_at"] = self.now.isoformat()
        payload.pop("strategy_confirmed")
        payload.pop("automation_enabled")

        state = LinjieExecutionState.from_dict(payload, account_id="a", group_id="g")

        self.assertTrue(state.strategy_confirmed)
        self.assertTrue(state.automation_enabled)

    def test_execution_state_fills_automation_from_confirmed_strategy(self) -> None:
        payload = LinjieExecutionState("a", "g").to_dict()
        payload["strategy_confirmed"] = True
        payload.pop("automation_enabled")

        state = LinjieExecutionState.from_dict(payload, account_id="a", group_id="g")

        self.assertTrue(state.automation_enabled)

    def test_execution_state_rejects_missing_strategy_confirmed(self) -> None:
        payload = LinjieExecutionState("a", "g").to_dict()
        payload["automation_enabled"] = True
        payload.pop("strategy_confirmed")

        with self.assertRaisesRegex(ValueError, "灵界动作状态字段无效"):
            LinjieExecutionState.from_dict(payload, account_id="a", group_id="g")

    def test_query_list_states_skips_corrupted_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.json").write_text(json.dumps({"broken": True}), encoding="utf-8")

            self.assertEqual(list(LinjieQueryRepository(root).list_states()), [])

    def test_query_list_states_skips_only_corrupted_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LinjieQueryRepository(Path(directory))
            path = repository.path_for("a")
            payload = {
                "schema_version": 1,
                "account_id": "a",
                "groups": {
                    "bad": {"status": "broken"},
                    "good": LinjieQueryRepository(Path(directory)).load("a", "good").to_dict(),
                },
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            states = list(repository.list_states())

            self.assertEqual([(state.account_id, state.group_id) for state in states], [("a", "good")])

    def test_query_send_failure_marks_collection_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LinjieQueryRepository(Path(directory))
            from linjie_upgrade import LinjieQueryService

            service = LinjieQueryService(
                repository,
                LinjieSnapshotRepository(Path(directory) / "snapshots"),
                LinjieQueryPolicy(),
            )
            now = self.now
            service.start("a", "g", now=now)
            command = service.reconcile(now=now)[0]

            service.mark_send_failed("a", "g", command.request_id, reason="没有可用会话地址", now=now)

            state = service.get_state("a", "g")
            self.assertEqual(state.status, "failed")
            self.assertIn("没有可用会话地址", state.last_error)

    def test_amount_and_output_use_explicit_display_values(self) -> None:
        self.assertEqual(parse_amount("83.52亿灵矿石"), 8_352_000_000)
        self.assertEqual(parse_amount("1434.21万"), 14_342_100)
        output = parse_output("14.7（👑+5.14）/s")
        self.assertAlmostEqual(output.base, 14.7)
        self.assertAlmostEqual(output.bonus, 5.14)
        self.assertAlmostEqual(output.total, 19.84)

        high_units = parse_output("1.2兆（👑+3.4京）/s")
        self.assertEqual(high_units.base, 1_200_000_000_000)
        self.assertEqual(high_units.bonus, 34_000_000_000_000_000)

    def test_four_real_pages_parse_high_and_empty_states(self) -> None:
        profile = self.parser.parse_profile(PROFILE)
        buildings = self.parser.parse_buildings(BUILDINGS)
        upgrades = self.parser.parse_upgrades(UPGRADES)
        workers = self.parser.parse_workers(WORKERS)
        self.assertEqual((profile.skill_dao, profile.balance), (72, 8_352_000_000))
        self.assertTrue(profile.has_monthly_card)
        self.assertEqual(buildings.buildings[0].count, 60)
        self.assertTrue(buildings.buildings[-1].locked)
        self.assertFalse(upgrades.upgrades[0].available)
        self.assertEqual((workers.worker_total, workers.worker_capacity), (650, 1046))
        self.assertEqual(workers.rank_cost, 34_143_000_000)
        self.assertEqual(self.parser.parse_upgrades(EMPTY_UPGRADES).upgrades, ())
        self.assertEqual(self.parser.parse_workers(EMPTY_WORKERS).workers, ())

    def test_wrong_or_incomplete_page_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.parser.parse_profile(BUILDINGS)
        with self.assertRaises(ValueError):
            self.parser.parse_buildings("🏗️建筑列表\n|建筑名称|拥有|")
        with self.assertRaises(ValueError):
            self.parser.parse_workers("👥杂役概览\n📊杂役总数：0/0人")

    def test_markdown_and_plain_table_copies_are_deduplicated(self) -> None:
        buildings = self.parser.parse_buildings(BUILDINGS + "\n" + BUILDINGS)
        workers = self.parser.parse_workers(WORKERS + "\n" + WORKERS)
        upgrades = self.parser.parse_upgrades(UPGRADES + "\n" + UPGRADES)
        self.assertEqual(len(buildings.buildings), 3)
        self.assertEqual(len(workers.workers), 2)
        self.assertEqual(len(upgrades.upgrades), 2)

        conflicting = BUILDINGS + "\n" + BUILDINGS.replace("|补给营地|×60|", "|补给营地|×61|")
        with self.assertRaises(ValueError):
            self.parser.parse_buildings(conflicting)

    def test_snapshot_is_replaced_only_after_all_four_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory) / "snapshots")
            with self.assertRaises(ValueError):
                snapshots.replace_from_pages(
                    "account-a", "group-1", {"profile": self.parser.parse_profile(PROFILE)},
                    collected_at=self.now,
                )
            self.assertIsNone(snapshots.load("account-a", "group-1"))
            snapshot = snapshots.replace_from_pages(
                "account-a",
                "group-1",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(BUILDINGS),
                    "upgrades": self.parser.parse_upgrades(UPGRADES),
                    "workers": self.parser.parse_workers(WORKERS),
                },
                collected_at=self.now,
            )
            self.assertEqual(snapshot.balance, 8_350_000_000)
            self.assertIsNone(snapshots.load("account-a", "group-2"))

    def test_planner_uses_display_candidates_and_skill_formula(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages(
                "account-a", "group-1",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(BUILDINGS),
                    "upgrades": self.parser.parse_upgrades(UPGRADES),
                    "workers": self.parser.parse_workers(WORKERS),
                },
                collected_at=self.now,
            )
            candidates = LinjiePlanner().candidates(snapshot)
            kinds = {item.kind for item in candidates}
            self.assertEqual(kinds, {"building", "worker", "worker_rank", "skill"})
            self.assertNotIn("upgrade", kinds)
            rank = next(item for item in candidates if item.kind == "worker_rank")
            self.assertEqual(rank.cost, 34_143_000_000)
            self.assertEqual(rank.command, "灵界杂役升阶")
            skill = next(item for item in candidates if item.kind == "skill")
            self.assertEqual(skill.command, "灵界技艺修行")
            self.assertEqual(skill.cost, int(15 * (1.2 ** 72)))
            self.assertAlmostEqual(skill.gain, 0.8 * 1.35)
            self.assertEqual(candidates[0], min(candidates, key=lambda item: item.roi_days))

    def test_available_building_upgrade_uses_displayed_level_count_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages(
                "account-a", "group-1",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(UPGRADE_BUILDINGS),
                    "upgrades": self.parser.parse_upgrades(AVAILABLE_UPGRADE),
                    "workers": self.parser.parse_workers(EMPTY_WORKERS),
                },
                collected_at=self.now,
            )
            candidate = next(item for item in LinjiePlanner().candidates(snapshot) if item.kind == "upgrade")
            self.assertEqual(candidate.cost, 19_980_000_000)
            self.assertEqual(candidate.command, "灵界升级建筑灵符堂")
            self.assertAlmostEqual(candidate.gain, (1008 + 352.8) / 2 * 22)

    def test_upgrade_with_displayed_cost_is_planned_when_only_balance_is_insufficient(self) -> None:
        upgrades = AVAILABLE_UPGRADE.replace("|灵符堂|1|199.8亿|升级|", "|灵符堂|1|199.8亿|❌️条件不足|")
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages(
                "account-a", "group-1",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(UPGRADE_BUILDINGS),
                    "upgrades": self.parser.parse_upgrades(upgrades),
                    "workers": self.parser.parse_workers(EMPTY_WORKERS),
                },
                collected_at=self.now,
            )
            candidate = next(item for item in LinjiePlanner().candidates(snapshot) if item.kind == "upgrade")
            self.assertEqual(candidate.cost, 19_980_000_000)

    def test_compound_route_fills_threshold_then_upgrades(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (Building("组合建筑", 14, DisplayOutput(20), 100, False),),
            (BuildingUpgrade("组合建筑", 1, 100, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot)

        self.assertEqual([item.kind for item in plan], ["building"] * 6 + ["upgrade"])
        self.assertTrue(all(item.route_name == "组合建筑" for item in plan))
        self.assertTrue(all(item.route_target_count == 20 for item in plan))
        self.assertTrue(all(item.route_target_level == 2 for item in plan))
        self.assertAlmostEqual(plan[-1].gain, 200)
        self.assertEqual(
            [item.available_after_seconds for item in plan],
            sorted(item.available_after_seconds for item in plan),
        )

    def test_multi_step_plan_starts_with_the_same_first_step_as_plan(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10**13, DisplayOutput(100), 100, 0, False,
            (
                Building("甲楼", 1, DisplayOutput(10), 100, False),
                Building("乙楼", 1, DisplayOutput(10), 100, False),
            ),
            (), 0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        multi = LinjiePlanner().multi_step_plan(snapshot, max_steps=5)
        single = LinjiePlanner().plan(snapshot)

        self.assertEqual(multi[0], single[0])
        self.assertGreaterEqual(len(multi), 2)
        self.assertEqual(
            [item.available_after_seconds for item in multi],
            sorted(item.available_after_seconds for item in multi),
        )

    def test_multi_step_plan_projects_building_cost_growth_matching_simulator(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10**13, DisplayOutput(100), 100, 0, False,
            (Building("甲楼", 1, DisplayOutput(10), 100, False),),
            (), 0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().multi_step_plan(snapshot, max_steps=3)

        # 模拟器实测买楼成本精确等比 ×1.25。
        self.assertEqual([item.cost for item in plan], [100, 125, 156])

    def test_multi_step_plan_with_locked_route_stays_on_route(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10**13, DisplayOutput(100), 100, 0, False,
            (Building("锁定建筑", 9, DisplayOutput(10), 100, False),),
            (BuildingUpgrade("锁定建筑", 0, 100, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        multi = LinjiePlanner().multi_step_plan(snapshot, ("锁定建筑", 10, 1), max_steps=20)
        single = LinjiePlanner().plan(snapshot, ("锁定建筑", 10, 1))

        self.assertEqual(multi, single)
        self.assertEqual([item.kind for item in multi], ["building", "upgrade"])

    def test_multi_step_plan_with_completed_locked_route_falls_back_to_best(self) -> None:
        # 锁定路线升级完成后重查四页，推演不能返回空计划，
        # 否则执行状态停在 exhausted，规划文本显示无候选。
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (
                Building("已完成建筑", 10, DisplayOutput(1), None, True),
                Building("即时建筑", 1, DisplayOutput(100), 1, False),
            ),
            (BuildingUpgrade("已完成建筑", 1, None, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().multi_step_plan(snapshot, ("已完成建筑", 10, 1), max_steps=20)

        self.assertTrue(plan)
        self.assertEqual(plan[0].name, "即时建筑")
        self.assertIsNone(plan[0].route_name)

    def test_multi_step_plan_merges_consecutive_recruits_into_one_batch(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10**13, DisplayOutput(100), 100, 0, False,
            (Building("空楼", 0, DisplayOutput(0), None, True),),
            (),
            0, 0, 0, 0,
            (WorkerPosition("灵符堂", 5, 100, DisplayOutput(50), DisplayOutput(10), 100),),
            self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().multi_step_plan(snapshot, max_steps=20)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].kind, "worker")
        self.assertEqual(plan[0].command, "灵界招募灵符堂 20")
        self.assertEqual(plan[0].note, "灵符堂+20杂役")
        self.assertEqual(plan[0].amount, 20)
        self.assertEqual(plan[0].cost, 5726)

    def test_multi_step_plan_does_not_merge_different_worker_names(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10**13, DisplayOutput(100), 100, 0, False,
            (Building("空楼", 0, DisplayOutput(0), None, True),),
            (),
            0, 0, 0, 0,
            (
                WorkerPosition("甲岗", 1, 100, DisplayOutput(10), DisplayOutput(10), 100),
                WorkerPosition("乙岗", 1, 100, DisplayOutput(10), DisplayOutput(10), 100),
            ),
            self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().multi_step_plan(snapshot, max_steps=3)

        self.assertEqual(len(plan), 3)
        for step in plan:
            self.assertEqual(step.amount, 1)

    def test_building_upgrade_gain_includes_current_worker_technology(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (Building("采矿据点", 20, DisplayOutput(18), 1000, False),),
            (BuildingUpgrade("采矿据点", 1, 100, True),),
            29, 80, 8, 0,
            (WorkerPosition(
                "采矿据点", 29, 80, DisplayOutput(29.29), DisplayOutput(1.01), 1000,
            ),),
            self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        upgrade = next(item for item in LinjiePlanner().candidates(snapshot) if item.kind == "upgrade")

        expected_worker_gain = 29 * (1.01 - 0.4) / 2
        self.assertAlmostEqual(upgrade.gain, 180 + expected_worker_gain)

    def test_worker_tech_formula_uses_current_official_output_for_all_buildings(self) -> None:
        planner = LinjiePlanner()
        cases = (
            (0.61, 0, False, 0.21),
            (0.81, 1, False, 0.205),
            (1.01, 0, False, 0.61),
            (2.05, 6, True, (2.05 - 0.54) / 7),
            (100.0, 5, False, (100.0 - 0.4) / 6),
        )
        for current, level, monthly, expected in cases:
            with self.subTest(current=current, level=level, monthly=monthly):
                self.assertAlmostEqual(
                    planner._worker_tech_step(current, level, monthly), expected
                )

    def test_worker_rank_formula_preserves_fixed_base_output(self) -> None:
        planner = LinjiePlanner()
        self.assertAlmostEqual(planner._worker_rank_step(1.01, 8, False), 0.61 * 0.3 / 3.4)
        self.assertAlmostEqual(planner._worker_rank_step(2.05, 22, True), 1.51 * 0.3 / 7.6)

    def test_missing_upgrade_price_is_projected_from_current_build_price(self) -> None:
        profile = self.parser.parse_profile(
            PROFILE.replace("技艺道行：72", "技艺道行：42").replace("技艺境界：7", "技艺境界：3")
        )
        buildings_text = """🏗️建筑列表
|建筑名称|拥有|建造|单产|下一个价格|
|:-|:-:|:-:|-:|-:|
|探测法阵|×19|一｜五｜十|12/s|54.12万灵矿石|
📊总建筑数：19
📈总产出：100灵矿石/秒
💰灵矿石储备：100万"""
        upgrades_text = """⬆️ 可升级建筑
|建筑名称|建筑等级|升级价格|升级|
|:-|:-:|-:|:-|
|探测法阵|1|—|❌️条件不足|
💰灵矿石储备：100万"""
        workers_text = """👥杂役概览
📊杂役总数：0/38人
📈杂役等阶：LV1
💰灵矿石储备：100万
|建筑名称|在岗/岗位|招募|产出加成|杂役单产|下一个价格|
|:-|:-:|:-:|-:|-:|-:|
|探测法阵|0/38|一 / 五 / 十|0|1|1000|
杂役升阶（需要1000灵矿石）"""
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages(
                "a", "g",
                {
                    "profile": profile,
                    "buildings": self.parser.parse_buildings(buildings_text),
                    "upgrades": self.parser.parse_upgrades(upgrades_text),
                    "workers": self.parser.parse_workers(workers_text),
                },
                collected_at=self.now,
            )
            plan = LinjiePlanner().plan(snapshot, ("探测法阵", 20, 2))
            self.assertEqual(
                [item.command for item in plan],
                ["灵界建造探测法阵 1", "灵界升级建筑探测法阵"],
            )
            self.assertGreater(plan[-1].cost, 0)

    def test_skill_uses_official_realm_without_combining_breakthrough(self) -> None:
        profile = self.parser.parse_profile(
            PROFILE.replace("技艺道行：72", "技艺道行：42").replace("技艺境界：7", "技艺境界：3")
        )
        pages = {
            "profile": profile,
            "buildings": self.parser.parse_buildings(BUILDINGS),
            "upgrades": self.parser.parse_upgrades(UPGRADES),
            "workers": self.parser.parse_workers(WORKERS),
        }
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages("a", "g", pages, collected_at=self.now)
            skill = next(item for item in LinjiePlanner().candidates(snapshot) if item.kind == "skill")
            self.assertEqual(skill.cost, int(15 * (1.2 ** 42)))
            self.assertAlmostEqual(skill.gain, 0.4 * 1.35)
            self.assertEqual(skill.command, "灵界技艺修行")

    def test_official_new_building_name_is_used_without_whitelist(self) -> None:
        buildings_text = BUILDINGS.replace(
            "|补给营地|×60|一｜五｜十|14.7（👑+5.14）/s|3.52亿灵矿石|",
            "|超维建筑|×60|一｜五｜十|14.7（👑+5.14）/s|3.52亿灵矿石|",
        )
        workers_text = WORKERS.replace("补给营地", "超维建筑")
        with tempfile.TemporaryDirectory() as directory:
            snapshots = LinjieSnapshotRepository(Path(directory))
            snapshot = snapshots.replace_from_pages(
                "a", "g",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(buildings_text),
                    "upgrades": self.parser.parse_upgrades(UPGRADES.replace("补给营地", "超维建筑")),
                    "workers": self.parser.parse_workers(workers_text),
                },
                collected_at=self.now,
            )
            commands = {item.command for item in LinjiePlanner().candidates(snapshot)}
            self.assertIn("灵界建造超维建筑 1", commands)

    def test_plan_cache_reuses_same_substantive_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = LinjieSnapshotRepository(Path(directory)).replace_from_pages(
                "a", "g",
                {
                    "profile": self.parser.parse_profile(PROFILE),
                    "buildings": self.parser.parse_buildings(BUILDINGS),
                    "upgrades": self.parser.parse_upgrades(UPGRADES),
                    "workers": self.parser.parse_workers(WORKERS),
                },
                collected_at=self.now,
            )
            planner = LinjiePlanner()

            first = planner.plan(snapshot)
            second = planner.plan(snapshot)
            first_routes = planner.candidate_routes(snapshot)
            second_routes = planner.candidate_routes(snapshot)

        self.assertIs(first, second)
        self.assertIs(first_routes, second_routes)
        self.assertEqual(first, first_routes[0])

    # ---- 模拟器实测锚点回归（2026-08，社区计算器 v1.5.2 账号 795 实测）----
    # 模拟器逐级对显示精度取整，本实现为连续乘法单次取整，允许极小差值。

    def test_real_official_furnace_building_cost_sequence_matches_simulator(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10_000_000_000_000, DisplayOutput(4110), 76, 0, False,
            (Building("锻兵房", 14, DisplayOutput(4110), 58_935_000_000, False),),
            (BuildingUpgrade("锻兵房", 1, 259_200_000_000, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, ("锻兵房", 20, 2))

        costs = [item.cost for item in plan]
        self.assertEqual([item.kind for item in plan], ["building"] * 6 + ["upgrade"])
        for previous, current in zip(costs[:6], costs[1:6]):
            self.assertAlmostEqual(current / previous, 1.25)
        # 模拟器实测序列：589.35→736.69→920.86→1151.08→1438.85→1798.56 亿
        self.assertAlmostEqual(costs[0], 58_935_000_000)
        # 模拟器“含补楼”总成本 9227.40 亿（升级 2592.00 + 补楼 6635.39）
        self.assertAlmostEqual(sum(costs), 922_740_000_000, delta=50_000_000)

    def test_real_official_skill_level_76_cost_matches_simulator(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 0, DisplayOutput(100), 76, 3, False, (), (),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )
        planner = LinjiePlanner()
        skill = planner._skill_candidate(snapshot)
        # 模拟器实测 1562.67 万（逐级取整）；公式为 15 × 1.2^76 取整。
        self.assertAlmostEqual(skill.cost, 15_626_700, delta=5_000)

    def test_real_official_monthly_card_output_factor_matches_simulator(self) -> None:
        planner = LinjiePlanner()
        # 模拟器实测皇冠加成 6.11/17.45 ≈ +35%。
        self.assertAlmostEqual(planner._worker_base_output(False), 0.4)
        self.assertAlmostEqual(planner._worker_base_output(True), 0.54)
        self.assertAlmostEqual(planner._worker_base_output(True) / planner._worker_base_output(False), 1.35)

    def test_real_official_upgrade_requires_level_x_10_buildings(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 10_000_000_000_000, DisplayOutput(100), 0, 0, False,
            (Building("修士坊市", 40, DisplayOutput(100), 89_040_000_000, False),),
            (BuildingUpgrade("修士坊市", 4, 1_183_500_000_000, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, ("修士坊市", 50, 5))

        self.assertEqual([item.kind for item in plan], ["building"] * 10 + ["upgrade"])
        # 模拟器实测：科技 4→5 级需补 10 座（含补楼 4144.23 亿，升级 1183.50 亿）
        for building in plan[:10]:
            self.assertEqual(building.route_target_count, 50)
            self.assertEqual(building.route_target_level, 5)
        self.assertAlmostEqual(plan[-1].cost, 1_183_500_000_000)

    def test_compound_roi_is_primary_sort_key(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 0, DisplayOutput(100), 100, 0, False,
            (
                Building("组合建筑", 9, DisplayOutput(10), 100, False),
                Building("普通建筑", 1, DisplayOutput(1), 5, False),
            ),
            (BuildingUpgrade("组合建筑", 0, 100, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot)

        self.assertEqual(
            [item.command for item in plan],
            ["灵界建造组合建筑 1", "灵界升级建筑组合建筑"],
        )

    def test_acquisition_time_breaks_equal_roi(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 0, DisplayOutput(10), 100, 0, False,
            (
                Building("较快建筑", 1, DisplayOutput(10), 100, False),
                Building("较慢建筑", 1, DisplayOutput(20), 200, False),
            ),
            (), 0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot)

        self.assertEqual(plan[0].name, "较快建筑")
        self.assertEqual(plan[0].available_after_seconds, 10)

    def test_time_strategy_prefers_fastest_acquisition_over_roi(self) -> None:
        # 无升级主线时退化为“等待+回本”滚雪球：快速建筑总分更低。
        snapshot = LinjieSnapshot(
            "a", "g", 0, DisplayOutput(1), 100, 0, False,
            (
                Building("高回报建筑", 1, DisplayOutput(20), 100, False),
                Building("快速建筑", 1, DisplayOutput(1), 10, False),
            ),
            (), 0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        roi_plan = LinjiePlanner().plan(snapshot)
        time_plan = LinjiePlanner().plan(snapshot, strategy="time")

        self.assertEqual(roi_plan[0].name, "高回报建筑")
        self.assertEqual(time_plan[0].name, "快速建筑")
        self.assertEqual(time_plan[0].available_after_seconds, 10)

    def test_time_strategy_refuses_side_action_when_roi_exceeds_mainline_wait(self) -> None:
        # 无限榜规则：主线等待 W 很小时，插队动作只有回本 < W 才有资格。
        # 此处零散楼回本 (100/1 秒) 明显大于等待 (~0.0001 天)，直接冲主线。
        snapshot = LinjieSnapshot(
            "a", "g", 10_000, DisplayOutput(100_000_000), 100, 0, False,
            (
                Building("主线建筑", 9, DisplayOutput(10), 1_000_000, False),
                Building("零散建筑", 1, DisplayOutput(1), 100, False),
            ),
            (BuildingUpgrade("主线建筑", 0, 1_000_000, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, strategy="time")

        self.assertEqual(plan[0].kind, "building")
        self.assertEqual(plan[0].route_name, "主线建筑")

    def test_time_strategy_interleaves_side_action_that_pays_back_within_wait(self) -> None:
        # 主线等待约 0.116 天，零散楼回本 0.0012 天 < W：
        # 先做零散楼能滚雪球净加速主线，因此插队。
        snapshot = LinjieSnapshot(
            "a", "g", 10_000, DisplayOutput(100), 100, 0, False,
            (
                Building("主线建筑", 9, DisplayOutput(10), 1_000_000, False),
                Building("零散建筑", 1, DisplayOutput(1), 100, False),
            ),
            (BuildingUpgrade("主线建筑", 0, 1_000_000, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, strategy="time")

        self.assertEqual(plan[0].name, "零散建筑")
        self.assertIsNone(plan[0].route_name)

    def test_locked_route_continues_even_when_direct_action_is_better(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (
                Building("锁定建筑", 9, DisplayOutput(1), 100, False),
                Building("即时建筑", 1, DisplayOutput(100), 1, False),
            ),
            (BuildingUpgrade("锁定建筑", 0, 100, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, ("锁定建筑", 10, 1))

        self.assertEqual(
            [item.command for item in plan],
            ["灵界建造锁定建筑 1", "灵界升级建筑锁定建筑"],
        )

    def test_locked_route_upgrades_after_building_count_overshoots_target(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (Building("锁定建筑", 21, DisplayOutput(20), 100, False),),
            (BuildingUpgrade("锁定建筑", 1, 100, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, ("锁定建筑", 20, 2))

        self.assertEqual([item.command for item in plan], ["灵界升级建筑锁定建筑"])
        self.assertAlmostEqual(plan[0].gain, 210)

    def test_invalid_locked_route_falls_back_to_current_best(self) -> None:
        snapshot = LinjieSnapshot(
            "a", "g", 100, DisplayOutput(100), 100, 0, False,
            (
                Building("已完成建筑", 10, DisplayOutput(1), None, True),
                Building("即时建筑", 1, DisplayOutput(100), 1, False),
            ),
            (BuildingUpgrade("已完成建筑", 1, None, False),),
            0, 0, 0, 0, (), self.now.isoformat(),
            {name: "test" for name in ("profile", "buildings", "upgrades", "workers")},
        )

        plan = LinjiePlanner().plan(snapshot, ("已完成建筑", 10, 1))

        self.assertEqual(plan[0].name, "即时建筑")
        self.assertIsNone(plan[0].route_name)

    def test_query_task_is_sequential_persistent_and_commits_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = LinjieSnapshotRepository(root / "snapshots")
            repository = LinjieQueryRepository(root / "tasks")
            service = LinjieQueryService(
                repository, snapshots, LinjieQueryPolicy(response_timeout_seconds=10, max_retries=3)
            )
            service.start("account-a", "group-1", now=self.now)
            first = service.reconcile(now=self.now)[0]
            self.assertEqual(first.text, QUERY_COMMANDS[0])
            service.mark_sent("account-a", "group-1", first.request_id, now=self.now)
            for index, text in enumerate((PROFILE, BUILDINGS, UPGRADES, WORKERS)):
                result = service.on_reply(
                    "account-a", "group-1", text,
                    now=self.now + timedelta(seconds=index + 1), message_id=f"m-{index}",
                )
                self.assertTrue(result.handled)
                if index < 3:
                    self.assertEqual(result.commands, ())
                    next_command = service.reconcile(now=self.now + timedelta(seconds=index + 1))[0]
                    self.assertEqual(next_command.text, QUERY_COMMANDS[index + 1])
                    service.mark_sent(
                        "account-a", "group-1", next_command.request_id,
                        now=self.now + timedelta(seconds=index + 1),
                    )
                    self.assertIsNone(snapshots.load("account-a", "group-1"))
            self.assertTrue(result.completed)
            self.assertIsNotNone(snapshots.load("account-a", "group-1"))
            restarted = LinjieQueryService(
                LinjieQueryRepository(root / "tasks"), snapshots,
                LinjieQueryPolicy(response_timeout_seconds=10, max_retries=3),
            )
            self.assertEqual(restarted.reconcile(now=self.now + timedelta(seconds=20)), [])

    def test_query_timeout_starts_only_after_successful_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = LinjieQueryService(
                LinjieQueryRepository(root / "tasks"),
                LinjieSnapshotRepository(root / "snapshots"),
                LinjieQueryPolicy(response_timeout_seconds=10, max_retries=3),
            )
            service.start("account-a", "group-1", now=self.now)
            first = service.reconcile(now=self.now)[0]
            unsent = service.reconcile(now=self.now + timedelta(seconds=30))[0]
            self.assertEqual(unsent.request_id, first.request_id)
            self.assertEqual((unsent.attempt_number, unsent.retry_number), (1, 0))
            service.mark_sent("account-a", "group-1", first.request_id, now=self.now + timedelta(seconds=30))
            self.assertEqual(service.reconcile(now=self.now + timedelta(seconds=39)), [])
            retry = service.reconcile(now=self.now + timedelta(seconds=40))[0]
            self.assertEqual((retry.attempt_number, retry.retry_number), (2, 1))


if __name__ == "__main__":
    unittest.main()
