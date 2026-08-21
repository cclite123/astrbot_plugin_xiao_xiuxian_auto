"""基于官方显示值生成确定性的组合 ROI 升级路线。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .model import DisplayOutput, LinjieCandidate, LinjieSnapshot


# 社区计算器沙盒差分实测（2026-08，账号 795）的后续成本递推系数：
# 招募：锻兵房 44→45→46→47 人价格 46.76/51.43/56.57 亿，精确等比 1.1；
# 升阶：22→23→24→25 阶价格 341.43/751.14/1652.51 亿，精确等比 2.2。
RECRUIT_COST_GROWTH = 1.1
RANK_COST_GROWTH = 2.2


class LinjiePlanner:
    CACHE_MAX_ENTRIES = 32

    def __init__(self) -> None:
        self._plan_cache: dict[tuple, tuple[LinjieCandidate, ...]] = {}
        self._route_cache: dict[tuple, tuple[tuple[LinjieCandidate, ...], ...]] = {}

    def candidates(self, snapshot: LinjieSnapshot) -> tuple[LinjieCandidate, ...]:
        candidates: list[LinjieCandidate] = []
        buildings_by_name = {item.name: item for item in snapshot.buildings}
        workers_by_name = {item.name: item for item in snapshot.workers}
        upgrades_by_name = {item.name: item for item in snapshot.upgrades}
        for building in snapshot.buildings:
            if (
                building.next_cost is not None
                and building.output is not None
                and not building.locked
                and building.output.total > 0
            ):
                candidates.append(LinjieCandidate(
                    "building", building.name, building.next_cost, building.output.total,
                    f"灵界建造{building.name} 1", f"{building.name}+1座",
                ))
        for upgrade in snapshot.upgrades:
            building = buildings_by_name.get(upgrade.name)
            if (
                building is None
                or building.output is None
                or building.count <= 0
                or upgrade.level >= min(6, building.count // 10)
            ):
                continue
            cost = upgrade.cost or self._project_upgrade_cost(
                building.next_cost, building.count, upgrade.level
            )
            gain = self._upgrade_gain(
                building.output.total,
                building.count,
                upgrade.level,
                workers_by_name.get(upgrade.name),
                snapshot.has_monthly_card,
            )
            if cost is not None and gain > 0:
                candidates.append(LinjieCandidate(
                    "upgrade", upgrade.name, cost, gain,
                    f"灵界升级建筑{upgrade.name}",
                    f"{upgrade.name}建筑等级 {upgrade.level}→{upgrade.level + 1}",
                    route_name=upgrade.name,
                    route_target_count=(upgrade.level + 1) * 10,
                    route_target_level=upgrade.level + 1,
                ))
        for worker in snapshot.workers:
            if (
                worker.next_cost is not None
                and worker.workers < worker.capacity
                and worker.single_output.total > 0
            ):
                candidates.append(LinjieCandidate(
                    "worker", worker.name, worker.next_cost, worker.single_output.total,
                    f"灵界招募{worker.name} 1", f"{worker.name}+1杂役",
                    amount=1,
                ))
        rank_gain = sum(
            item.workers * self._worker_rank_step(
                item.single_output.total,
                snapshot.worker_rank,
                snapshot.has_monthly_card,
            )
            for item in snapshot.workers
        )
        if snapshot.rank_cost > 0 and rank_gain > 0:
            candidates.append(LinjieCandidate(
                "worker_rank", "杂役等阶", snapshot.rank_cost, rank_gain,
                "灵界杂役升阶",
                f"杂役等阶 LV{snapshot.worker_rank}→LV{snapshot.worker_rank + 1}",
            ))
        skill = self._skill_candidate(snapshot)
        if skill is not None:
            candidates.append(skill)
        return tuple(sorted(candidates, key=lambda item: (item.roi_days, item.cost, item.command)))

    def plan(
        self,
        snapshot: LinjieSnapshot,
        locked_route: tuple[str, int, int] | None = None,
        strategy: str = "roi",
    ) -> tuple[LinjieCandidate, ...]:
        cache_key = (self._planning_fingerprint(snapshot), locked_route, strategy)
        if cache_key in self._plan_cache:
            return self._plan_cache[cache_key]
        if locked_route is None and strategy == "time":
            plan = self._time_first_action_plan(snapshot)
        else:
            plan = self._build_plan(snapshot, locked_route, strategy)
        if len(self._plan_cache) >= self.CACHE_MAX_ENTRIES:
            self._plan_cache.pop(next(iter(self._plan_cache)))
        self._plan_cache[cache_key] = plan
        return plan

    def _time_first_action_plan(
        self, snapshot: LinjieSnapshot
    ) -> tuple[LinjieCandidate, ...]:
        """时间策略首步：主线最快化 + 等待期内回本插队（无限榜规则）。"""
        first = self._time_pick_step(snapshot)
        if first is None:
            return ()
        if first.route_name is not None:
            route = self._upgrade_route(
                snapshot, first.route_name, first.route_target_count, first.route_target_level
            )
            if route:
                return self._schedule(route, snapshot)
        return (first,)

    def _time_pick_step(self, snapshot: LinjieSnapshot) -> LinjieCandidate | None:
        """每轮动态决策。

        无限视界榜里总产与秒产同优：唯一目标是让科技主线最快点火。
        主线下一栋等待 W 天时，只有买得起且回本 < W 的单步动作才有
        资格插队（它会净加速主线）；否则直接等钱继续主线。
        """
        balance = float(snapshot.balance)
        speed = float(snapshot.total_output.total)
        routes = self.candidate_routes(snapshot, "time")
        if not routes:
            return None
        compounds = [route for route in routes if route[0].route_name is not None]
        singles = [route[0] for route in routes if route[0].route_name is None]
        if compounds:
            main = min(
                compounds,
                key=lambda route: (
                    route[-1].available_after_seconds,
                    route[0].cost,
                ),
            )
            first = main[0]
            wait_days = (
                max(0.0, first.cost - balance) / speed / 86400
                if speed > 0
                else float("inf")
            )
            if first.cost <= balance:
                return first
            eligible = [
                candidate
                for candidate in singles
                if candidate.cost <= balance and candidate.roi_days < wait_days
            ]
            if eligible:
                return min(eligible, key=lambda item: (item.roi_days, item.cost, item.command))
            return first
        if singles:
            def score(candidate: LinjieCandidate) -> tuple[float, float, int, str]:
                wait_days = (
                    max(0.0, candidate.cost - balance) / speed / 86400
                    if speed > 0
                    else float("inf")
                )
                return (
                    wait_days + candidate.roi_days,
                    candidate.roi_days,
                    candidate.cost,
                    candidate.command,
                )

            return min(singles, key=score)
        return None

    def multi_step_plan(
        self,
        snapshot: LinjieSnapshot,
        locked_route: tuple[str, int, int] | None = None,
        strategy: str = "roi",
        max_steps: int = 20,
    ) -> tuple[LinjieCandidate, ...]:
        """贪心推演长线计划，仅用于展示与执行队列填充。

        每轮按当前策略选出最优路线（单步动作或补楼组合路线）并模拟执行
        到预算步数为止；第一步与 ``plan()`` 完全一致，执行器仍然每步成功后
        重查四页重新计算，推演只是说明后续发展方向。
        """
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
            raise ValueError("长线推演步数必须是正整数")
        if locked_route is not None:
            route = self._upgrade_route(snapshot, *locked_route)
            if route:
                scheduled = self._schedule(route, snapshot)
                if scheduled:
                    return scheduled[:max_steps]
            # 锁定路线完成或失效时回退整体贪心推演（与 plan() 一致），
            # 避免返回空计划让执行停在 exhausted。

        steps: list[LinjieCandidate] = []
        balance = float(snapshot.balance)
        speed = float(snapshot.total_output.total)
        elapsed = 0.0
        working = snapshot
        recruit_bases = {
            item.name: item.next_cost / (RECRUIT_COST_GROWTH ** item.workers)
            for item in snapshot.workers
            if item.next_cost is not None
        }
        while len(steps) < max_steps:
            if strategy == "time":
                candidate = self._time_pick_step(working)
                if candidate is None:
                    break
            else:
                routes = self.candidate_routes(working, strategy)
                if not routes:
                    break
                candidate = routes[0][0]
            if balance < candidate.cost:
                if speed <= 0:
                    return self._merge_consecutive_recruits(steps)
                wait = (candidate.cost - balance) / speed
                balance += wait * speed
                elapsed += wait
            balance -= candidate.cost
            steps.append(replace(
                candidate,
                available_after_seconds=math.ceil(elapsed),
                projected_balance_after=balance,
            ))
            speed += candidate.gain
            working = self._apply_candidate(working, candidate, recruit_bases=recruit_bases)
            # _time_pick_step() 和 candidate_routes() 读取的是 working 快照，
            # 因此必须把本轮模拟后的余额/总产出同步进去。否则第二步开始
            # 仍会使用首轮余额和秒产，导致等待时间与后续排序失真。
            working = replace(
                working,
                balance=max(0, round(balance)),
                total_output=DisplayOutput(base=speed, bonus=0.0),
            )
        return self._merge_consecutive_recruits(steps)

    @staticmethod
    def _merge_consecutive_recruits(
        steps: list[LinjieCandidate],
    ) -> tuple[LinjieCandidate, ...]:
        """合并相邻的同一建筑招募，一步发出 `灵界招募X N`。

        成本按招募价 ×1.1 递增的逐人总和（与逐个招的总花费一致），
        增益为单人 ×N；完成时间与余额取本批最后一步。游戏官方
        “招募 N 人”无单次数量上限。
        """
        merged: list[LinjieCandidate] = []
        for step in steps:
            if (
                merged
                and merged[-1].kind == "worker"
                and step.kind == "worker"
                and merged[-1].name == step.name
                and merged[-1].route_name is None
                and step.route_name is None
            ):
                last = merged[-1]
                merged[-1] = replace(
                    last,
                    amount=last.amount + step.amount,
                    cost=last.cost + step.cost,
                    gain=last.gain + step.gain,
                    available_after_seconds=step.available_after_seconds,
                    projected_balance_after=step.projected_balance_after,
                    command=f"灵界招募{last.name} {last.amount + step.amount}",
                    note=f"{last.name}+{last.amount + step.amount}杂役",
                )
            else:
                merged.append(step)
        return tuple(merged)

    def _apply_candidate(
        self,
        snapshot: LinjieSnapshot,
        candidate: LinjieCandidate,
        *,
        recruit_bases: dict[str, float] | None = None,
    ) -> LinjieSnapshot:
        """按候选动作推演快照；产出与后续成本使用沙盒实测公式递推。

        推演是本地中间态，DisplayOutput 只保证 total 口径正确
        （候选增益全部取 total），base/bonus 拆分不做展示。
        """
        kind = candidate.kind
        if kind == "building":
            buildings = tuple(
                replace(
                    item,
                    count=item.count + 1,
                    next_cost=round(item.next_cost * 1.25) if item.next_cost is not None else None,
                )
                if item.name == candidate.name
                else item
                for item in snapshot.buildings
            )
            return replace(snapshot, buildings=buildings)
        if kind == "upgrade":
            # 升级后建筑单产 ×(L+2)/(L+1)，该建筑杂役单产随建筑单产同比放大。
            building = next(item for item in snapshot.buildings if item.name == candidate.name)
            upgrade = next(item for item in snapshot.upgrades if item.name == candidate.name)
            factor = (upgrade.level + 2) / (upgrade.level + 1)
            new_level = upgrade.level + 1
            buildings = tuple(
                self._rescale_building(item, candidate.name, factor)
                for item in snapshot.buildings
            )
            upgrades = tuple(
                replace(
                    item,
                    level=new_level,
                    cost=self._project_upgrade_cost(building.next_cost, building.count, new_level),
                )
                if item.name == candidate.name
                else item
                for item in snapshot.upgrades
            )
            workers = tuple(
                self._rescale_worker(item, candidate.name, snapshot.has_monthly_card, factor)
                for item in snapshot.workers
            )
            return replace(snapshot, buildings=buildings, upgrades=upgrades, workers=workers)
        if kind == "worker":
            workers = tuple(
                replace(
                    item,
                    workers=item.workers + 1,
                    next_cost=(
                        round(
                            recruit_bases[item.name]
                            * (RECRUIT_COST_GROWTH ** (item.workers + 1))
                        )
                        if recruit_bases is not None and item.name in recruit_bases
                        else round(item.next_cost * RECRUIT_COST_GROWTH)
                    )
                    if item.next_cost is not None
                    else None,
                )
                if item.name == candidate.name
                else item
                for item in snapshot.workers
            )
            return replace(snapshot, workers=workers)
        if kind == "worker_rank":
            # 升阶后杂役单产 = 基础 + 科技部分×(1+0.3r_new)/(1+0.3r_old)。
            old_rank = snapshot.worker_rank
            factor = (1 + 0.3 * (old_rank + 1)) / (1 + 0.3 * old_rank)
            workers = tuple(
                self._rescale_worker(item, item.name, snapshot.has_monthly_card, factor)
                for item in snapshot.workers
            )
            return replace(
                snapshot,
                workers=workers,
                worker_rank=old_rank + 1,
                rank_cost=round(snapshot.rank_cost * RANK_COST_GROWTH),
            )
        if kind == "skill":
            return replace(snapshot, skill_dao=snapshot.skill_dao + 1)
        return snapshot

    @staticmethod
    def _rescale_building(item, name: str, factor: float):
        """建筑单产整体缩放（无基础常数）：实测升级 1→2 时 3045→4567.5 = ×1.5。"""
        if item.name != name or item.output is None:
            return item
        total = item.output.total * factor
        return replace(item, output=replace(item.output, base=total, bonus=0.0))

    @staticmethod
    def _rescale_worker(item, name: str, has_monthly_card: bool, factor: float):
        """杂役单产按“基础 + 科技部分×factor”缩放。

        实测（锻兵房，月卡）：科技 1→2 单产 312.96→469.17 =
        0.54+(312.96-0.54)×1.5；升阶 22→23 单产 469.17→487.67 =
        0.54+(469.17-0.54)×7.9/7.6。
        """
        if item.name != name:
            return item
        base_total = LinjiePlanner._worker_base_output(has_monthly_card)
        total = base_total + (item.single_output.total - base_total) * factor
        return replace(item, single_output=replace(item.single_output, base=total, bonus=0.0))

    def candidate_routes(
        self, snapshot: LinjieSnapshot, strategy: str = "roi"
    ) -> tuple[tuple[LinjieCandidate, ...], ...]:
        cache_key = (self._planning_fingerprint(snapshot), strategy)
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        scheduled = tuple(
            route
            for route in (
                self._schedule(candidate_route, snapshot)
                for candidate_route in self._candidate_route_options(snapshot)
            )
            if route
        )
        routes = tuple(sorted(scheduled, key=lambda route: self._route_score(route, strategy)))
        if len(self._route_cache) >= self.CACHE_MAX_ENTRIES:
            self._route_cache.pop(next(iter(self._route_cache)))
        self._route_cache[cache_key] = routes
        return routes

    def _build_plan(
        self,
        snapshot: LinjieSnapshot,
        locked_route: tuple[str, int, int] | None,
        strategy: str,
    ) -> tuple[LinjieCandidate, ...]:
        if locked_route is not None:
            route = self._upgrade_route(snapshot, *locked_route)
            if route:
                return self._schedule(route, snapshot)

        routes = self.candidate_routes(snapshot, strategy)
        return routes[0] if routes else ()

    def _candidate_route_options(
        self, snapshot: LinjieSnapshot
    ) -> tuple[tuple[LinjieCandidate, ...], ...]:
        routes = [(candidate,) for candidate in self.candidates(snapshot)]
        direct_upgrade_targets = {
            (route[0].route_name, route[0].route_target_count, route[0].route_target_level)
            for route in routes
            if route[0].route_name is not None
        }
        for upgrade in snapshot.upgrades:
            if upgrade.level >= 6:
                continue
            target = (upgrade.name, (upgrade.level + 1) * 10, upgrade.level + 1)
            if target in direct_upgrade_targets:
                continue
            route = self._upgrade_route(snapshot, *target)
            if route:
                routes.append(route)
        return tuple(routes)

    def _upgrade_route(
        self,
        snapshot: LinjieSnapshot,
        name: str,
        target_count: int,
        target_level: int,
    ) -> tuple[LinjieCandidate, ...]:
        building = next((item for item in snapshot.buildings if item.name == name), None)
        upgrade = next((item for item in snapshot.upgrades if item.name == name), None)
        worker = next((item for item in snapshot.workers if item.name == name), None)
        if (
            building is None
            or upgrade is None
            or building.output is None
            or building.locked
            or building.output.total <= 0
            or target_level > 6
            or upgrade.level >= target_level
            or target_level != upgrade.level + 1
            or target_count != target_level * 10
        ):
            return ()
        fill_count = max(0, target_count - building.count)
        if fill_count and building.next_cost is None:
            return ()
        upgrade_cost = upgrade.cost or self._project_upgrade_cost(
            building.next_cost, building.count, upgrade.level
        )
        if upgrade_cost is None:
            return ()

        route: list[LinjieCandidate] = []
        next_cost = building.next_cost
        for index in range(fill_count):
            cost = round(next_cost * (1.25 ** index))
            current_count = building.count + index
            route.append(LinjieCandidate(
                "building", name, cost, building.output.total,
                f"灵界建造{name} 1",
                f"{name}+1座（组合路线 {current_count}→{target_count} 后升至{target_level}级）",
                route_name=name,
                route_target_count=target_count,
                route_target_level=target_level,
            ))
        count_after_fill = building.count + fill_count
        gain = self._upgrade_gain(
            building.output.total,
            count_after_fill,
            upgrade.level,
            worker,
            snapshot.has_monthly_card,
        )
        if gain <= 0:
            return ()
        route.append(LinjieCandidate(
            "upgrade", name, upgrade_cost, gain,
            f"灵界升级建筑{name}",
            f"{name}建筑等级 {upgrade.level}→{target_level}（组合路线完成）",
            route_name=name,
            route_target_count=target_count,
            route_target_level=target_level,
        ))
        return tuple(route)

    @staticmethod
    def _route_score(
        route: tuple[LinjieCandidate, ...], strategy: str = "roi"
    ) -> tuple[float, float, int, str]:
        total_cost = sum(item.cost for item in route)
        total_gain = sum(item.gain for item in route)
        roi_days = total_cost / total_gain / 86400
        acquisition_seconds = route[-1].available_after_seconds
        if strategy == "time":
            # 时间策略的主线选择由 _time_pick_step 动态决策；
            # candidate_routes 仅保留按获取时间的通用排序供候选预览。
            return (acquisition_seconds, roi_days, total_cost, route[0].command)
        return (roi_days, acquisition_seconds, total_cost, route[0].command)

    @staticmethod
    def _schedule(
        route: tuple[LinjieCandidate, ...], snapshot: LinjieSnapshot
    ) -> tuple[LinjieCandidate, ...]:
        balance = float(snapshot.balance)
        speed = float(snapshot.total_output.total)
        elapsed = 0.0
        planned: list[LinjieCandidate] = []
        for candidate in route:
            if balance < candidate.cost:
                if speed <= 0:
                    return ()
                wait_seconds = (candidate.cost - balance) / speed
                balance += wait_seconds * speed
                elapsed += wait_seconds
            balance -= candidate.cost
            planned.append(LinjieCandidate(
                candidate.kind,
                candidate.name,
                candidate.cost,
                candidate.gain,
                candidate.command,
                candidate.note,
                math.ceil(elapsed),
                balance,
                candidate.route_name,
                candidate.route_target_count,
                candidate.route_target_level,
            ))
            speed += candidate.gain
        return tuple(planned)

    @staticmethod
    def _planning_fingerprint(snapshot: LinjieSnapshot) -> tuple:
        return (
            snapshot.balance,
            snapshot.total_output.base,
            snapshot.total_output.bonus,
            snapshot.skill_dao,
            snapshot.skill_realm,
            snapshot.has_monthly_card,
            snapshot.worker_rank,
            snapshot.rank_cost,
            tuple(sorted(
                (
                    item.name,
                    item.count,
                    item.output.base if item.output is not None else None,
                    item.output.bonus if item.output is not None else None,
                    item.next_cost,
                    item.locked,
                )
                for item in snapshot.buildings
            )),
            tuple(sorted((item.name, item.level, item.cost, item.available) for item in snapshot.upgrades)),
            tuple(sorted(
                (
                    item.name,
                    item.workers,
                    item.capacity,
                    item.output.base,
                    item.output.bonus,
                    item.single_output.base,
                    item.single_output.bonus,
                    item.next_cost,
                )
                for item in snapshot.workers
            )),
        )

    @staticmethod
    def _upgrade_gain(
        current_single: float,
        count: int,
        level: int,
        worker,
        has_monthly_card: bool,
    ) -> float:
        gain = current_single / (level + 1) * count
        if worker is not None:
            gain += worker.workers * LinjiePlanner._worker_tech_step(
                worker.single_output.total,
                level,
                has_monthly_card,
            )
        return gain

    @staticmethod
    def _worker_base_output(has_monthly_card: bool) -> float:
        return 0.4 * (1.35 if has_monthly_card else 1.0)

    @staticmethod
    def _worker_tech_step(
        current_single: float, level: int, has_monthly_card: bool
    ) -> float:
        base_output = LinjiePlanner._worker_base_output(has_monthly_card)
        return max(0.0, current_single - base_output) / (level + 1)

    @staticmethod
    def _worker_rank_step(
        current_single: float, rank: int, has_monthly_card: bool
    ) -> float:
        base_output = LinjiePlanner._worker_base_output(has_monthly_card)
        return max(0.0, current_single - base_output) * 0.3 / (1 + 0.3 * rank)

    @staticmethod
    def _project_upgrade_cost(
        next_build_cost: int | None, count: int, level: int
    ) -> int | None:
        if next_build_cost is None:
            return None
        base_build_cost = next_build_cost / (1.25 ** count)
        return round(base_build_cost * (10 ** (level + 1)))

    @staticmethod
    def _skill_candidate(snapshot: LinjieSnapshot) -> LinjieCandidate | None:
        cost = int(15 * (1.2 ** snapshot.skill_dao))
        base_gain = (snapshot.skill_realm + 1) * 0.1
        monthly_factor = 1.35 if snapshot.has_monthly_card else 1.0
        return LinjieCandidate(
            "skill",
            "技艺道行",
            cost,
            base_gain * monthly_factor,
            "灵界技艺修行",
            f"技艺道行 {snapshot.skill_dao}→{snapshot.skill_dao + 1}",
        )
