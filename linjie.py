# 模块：自动灵界升级
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from .storage import JsonStore
    from .time_utils import fmt_ts
    from .linjie_upgrade import (
        LinjiePageParser,
        LinjiePlanner,
        LinjieSnapshot,
        LinjieSnapshotRepository,
    )
except ImportError:
    from storage import JsonStore
    from time_utils import fmt_ts
    from linjie_upgrade import (
        LinjiePageParser,
        LinjiePlanner,
        LinjieSnapshot,
        LinjieSnapshotRepository,
    )


BUILDING_ORDER = [
    "补给营地",
    "探测法阵",
    "采矿据点",
    "修士坊市",
    "炼丹楼",
    "灵符堂",
    "锻兵房",
    "藏经阁",
    "静心府",
    "超维建筑 2号",
    "超维建筑 3号",
    "超维建筑 4号",
]

CAP_PER_BUILDING = {
    "补给营地": 2,
    "探测法阵": 2,
    "采矿据点": 4,
    "修士坊市": 4,
    "炼丹楼": 6,
    "灵符堂": 6,
    "锻兵房": 8,
    "藏经阁": 8,
    "静心府": 10,
    "超维建筑 2号": 10,
    "超维建筑 3号": 12,
    "超维建筑 4号": 12,
}

BASE_BUILD_COST = {
    "补给营地": 540.0,
    "探测法阵": 7020.0,
    "采矿据点": 91800.0,
    "修士坊市": 1183500.0,
    "炼丹楼": 15300000.0,
    "灵符堂": 200000000.0,
    "锻兵房": 2592000000.0,
    "藏经阁": 33705000000.0,
    "静心府": 438048000000.0,
    "超维建筑 2号": 5690000000000.0,
    "超维建筑 3号": 74030000000000.0,
    "超维建筑 4号": 962390000000000.0,
}

BASE_OUTPUT = {
    "补给营地": 2.1,
    "探测法阵": 6.3,
    "采矿据点": 18.9,
    "修士坊市": 57.75,
    "炼丹楼": 168.0,
    "灵符堂": 504.0,
    "锻兵房": 1522.5,
    "藏经阁": 4567.5,
    "静心府": 13700.0,
    "超维建筑 2号": 41100.0,
    "超维建筑 3号": 123300.0,
    "超维建筑 4号": 370000.0,
}

BASE_WORKER_COST = {
    "补给营地": 154.0,
    "探测法阵": 331.0,
    "采矿据点": 2639.0,
    "修士坊市": 32400.0,
    "炼丹楼": 416600.0,
    "灵符堂": 5439100.0,
    "锻兵房": 70560100.0,
    "藏经阁": 918000000.0,
    "静心府": 11925000000.0,
    "超维建筑 2号": 155020000000.0,
    "超维建筑 3号": 2020000000000.0,
    "超维建筑 4号": 26200000000000.0,
}

BASE_WORKER_OUTPUT = {
    "补给营地": 0.55,
    "探测法阵": 0.84,
    "采矿据点": 1.72,
    "修士坊市": 4.44,
    "炼丹楼": 12.16,
    "灵符堂": 35.68,
    "锻兵房": 106.98,
    "藏经阁": 320.13,
    "静心府": 959.58,
    "超维建筑 2号": 2877.93,
    "超维建筑 3号": 8632.98,
    "超维建筑 4号": 25900.0,
}

TECH_BASE_COST = {
    "补给营地": 5400.0,
    "探测法阵": 70200.0,
    "采矿据点": 918000.0,
    "修士坊市": 11835000.0,
    "炼丹楼": 153000000.0,
    "灵符堂": 1998000000.0,
    "锻兵房": 25920000000.0,
    "藏经阁": 337050000000.0,
    "静心府": 4380480000000.0,
    "超维建筑 2号": 56950000000000.0,
    "超维建筑 3号": 740300000000000.0,
    "超维建筑 4号": 9623910000000000.0,
}

WORKER_TECH_COEF = {
    "补给营地": 0.26,
    "探测法阵": 0.44,
    "采矿据点": 0.62,
    "修士坊市": 0.91,
}

QUERY_COMMANDS = ["灵界我的信息", "灵界建筑列表", "灵界升级列表", "灵界杂役名录"]
MONEY_UNITS = {"万": 1e4, "亿": 1e8, "兆": 1e12, "京": 1e16}


@dataclass
class LinjieCandidate:
    kind: str
    name: str
    cost: float
    gain: float
    command: str
    note: str = ""
    available_after_seconds: int = 0
    projected_balance_after: float = 0.0
    route_name: Optional[str] = None
    route_target_count: Optional[int] = None
    route_target_level: Optional[int] = None
    amount: int = 1

    @property
    def roi_days(self) -> float:
        if self.cost <= 0 or self.gain <= 0:
            return float("inf")
        return self.cost / self.gain / 86400.0


@dataclass
class LinjieState:
    enabled: bool = False
    phase: str = "IDLE"
    next_action_ts: float = 0.0
    wake_at_ts: float = 0.0
    failure_count: int = 0
    query_index: int = 0
    awaiting_query: str = ""
    after_query: str = ""
    query_commands: List[str] = field(default_factory=list)
    pending_action: Dict[str, Any] = field(default_factory=dict)
    blocked_commands: List[str] = field(default_factory=list)

    balance: float = 0.0
    total_speed: float = 0.0
    skill_dao: int = -1
    skill_realm: int = -1
    worker_rank: int = -1
    worker_total: int = 0
    worker_capacity: int = 0
    worker_rank_cost: float = 0.0
    monthly_card: bool = False
    abundance: bool = True
    buildings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_plan: Dict[str, Any] = field(default_factory=dict)
    # 混合规划器使用的四页原文和严格解析快照。保留在旧状态文件中，
    # 使现有命令入口/账号键继续可用，同时允许模块化 planner 逐步接管。
    page_texts: Dict[str, str] = field(default_factory=dict)
    module_snapshot: Dict[str, Any] = field(default_factory=dict)
    last_query_ts: float = 0.0
    last_update_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "LinjieState":
        if not d:
            return cls()
        inst = cls()
        for k in cls.__annotations__:
            if k in d:
                setattr(inst, k, d[k])
        if not isinstance(inst.buildings, dict):
            inst.buildings = {}
        if not isinstance(inst.blocked_commands, list):
            inst.blocked_commands = []
        if not isinstance(inst.query_commands, list):
            inst.query_commands = []
        return inst


def _clean_text(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\[\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_money(text: str) -> float:
    text = str(text or "").replace(",", "").strip()
    if not text or "—" in text or "🔒" in text:
        return 0.0
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([万亿兆京]?)", text)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2)
    return value * MONEY_UNITS.get(unit, 1.0)


def parse_output_value(text: str) -> float:
    text = str(text or "").replace(",", "")
    if not text or "—" in text:
        return 0.0
    base = parse_money(text)
    crown = 0.0
    m = re.search(r"👑\+\s*(-?\d+(?:\.\d+)?)\s*([万亿兆京]?)", text)
    if m:
        crown = float(m.group(1)) * MONEY_UNITS.get(m.group(2), 1.0)
    return base + crown


def format_money(value: float) -> str:
    value = float(value or 0.0)
    for unit, factor in (("京", 1e16), ("兆", 1e12), ("亿", 1e8), ("万", 1e4)):
        if abs(value) >= factor:
            num = f"{value / factor:.2f}".rstrip("0").rstrip(".")
            return f"{num}{unit}"
    return f"{value:.0f}"


def format_speed(value: float) -> str:
    value = float(value or 0.0)
    if abs(value) >= 10000:
        return f"{value / 10000:.2f}万/s"
    return f"{value:.2f}/s".rstrip("0").rstrip(".").replace("./", "/")


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 60:
        return f"{seconds:.0f}秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f}分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}小时"
    return f"{seconds / 86400:.1f}天"


def _worker_tech_factor(name: str, tech: int) -> float:
    coef = WORKER_TECH_COEF.get(name, 1.0)
    return 1.0 + max(0, int(tech)) * coef


def _worker_rank_factor(rank: int) -> float:
    if rank < 0:
        return 1.0
    return max(0.1, 1.0 + (int(rank) - 20) * 0.0424)


class LinjieUpgradeController:
    def __init__(self, store: JsonStore, official_qq: str, config: Optional[Dict[str, Any]] = None, logger=None):
        cfg = dict(config or {})
        self.store = store
        self.official_qq = official_qq
        self.log = logger
        self.module_enabled = bool(cfg.get("enabled", True))
        self.reserve_lingkuang = float(cfg.get("reserve_lingkuang", 0.0) or 0.0)
        self.query_timeout_sec = max(5.0, float(cfg.get("query_timeout_sec", 20.0)))
        self.action_timeout_sec = max(5.0, float(cfg.get("action_timeout_sec", 25.0)))
        self.success_delay_sec = max(0.0, float(cfg.get("success_delay_sec", 0.5)))
        self.max_failures = max(1, int(cfg.get("max_failures", 3)))
        self.cache_ttl_sec = max(60.0, float(cfg.get("cache_ttl_sec", 21600.0)))
        self.confirm_after_success = bool(cfg.get("confirm_after_success", False))
        self.roi_formula_source = str(cfg.get("roi_formula_source", "excel_formula") or "excel_formula").strip()
        self.default_abundance = bool(cfg.get("default_abundance", True))
        self.include_skill_training = bool(cfg.get("include_skill_training", True))
        self.include_skill_breakthrough = bool(cfg.get("include_skill_breakthrough", False))
        self.max_sim_steps = max(3, int(cfg.get("max_sim_steps", 15)))
        self.planner_engine = str(cfg.get("planner_engine", "hybrid") or "hybrid").strip().lower()
        if self.planner_engine not in {"legacy", "hybrid", "module"}:
            self.planner_engine = "hybrid"
        self.planning_strategy = str(cfg.get("planning_strategy", "roi") or "roi").strip().lower()
        if self.planning_strategy not in {"roi", "time"}:
            self.planning_strategy = "roi"
        snapshot_root = str(
            cfg.get("snapshot_root")
            or os.path.join(os.path.dirname(os.path.abspath(str(getattr(store, "path", "linjie_state.json")))), "linjie_snapshots")
        )
        self.module_parser = LinjiePageParser()
        self.module_planner = LinjiePlanner()
        self.module_snapshots = LinjieSnapshotRepository(snapshot_root)

    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    def _debug(self, msg: str) -> None:
        if self.log:
            self.log.debug(msg)

    def _warning(self, msg: str) -> None:
        if self.log:
            self.log.warning(msg)

    @staticmethod
    def _module_group_id() -> str:
        """旧控制器使用 ``self_id:group_id`` 作为单键，模块仓储使用固定兼容群键。"""
        return "legacy"

    def _module_snapshot(self, st: LinjieState) -> Optional[LinjieSnapshot]:
        if self.planner_engine == "legacy" or not st.module_snapshot:
            return None
        try:
            snapshot = LinjieSnapshot.from_dict(st.module_snapshot)
        except (TypeError, ValueError):
            return None
        try:
            collected_at = datetime.fromisoformat(snapshot.collected_at)
            if (datetime.now(collected_at.tzinfo) - collected_at).total_seconds() > self.cache_ttl_sec:
                return None
        except (TypeError, ValueError):
            return None
        return snapshot

    def _can_use_cached_state(self, st: LinjieState) -> bool:
        """首次迁移时强制建立严格快照；格式不兼容时允许明确回退旧解析。"""
        return (
            self.planner_engine == "legacy"
            or self._module_snapshot(st) is not None
            or bool(st.last_plan.get("module_parse_failed"))
        )

    def _module_candidate(self, candidate) -> LinjieCandidate:
        kind = {"upgrade": "tech", "worker_rank": "rank"}.get(candidate.kind, candidate.kind)
        return LinjieCandidate(
            kind=kind,
            name=candidate.name,
            cost=float(candidate.cost),
            gain=float(candidate.gain),
            command=candidate.command,
            note=candidate.note,
            available_after_seconds=int(candidate.available_after_seconds),
            projected_balance_after=float(candidate.projected_balance_after),
            route_name=candidate.route_name,
            route_target_count=candidate.route_target_count,
            route_target_level=candidate.route_target_level,
            amount=int(candidate.amount or 1),
        )

    def _module_candidates(self, st: LinjieState) -> List[LinjieCandidate]:
        snapshot = self._module_snapshot(st)
        if snapshot is None:
            return []
        try:
            return [self._module_candidate(item) for item in self.module_planner.candidates(snapshot)]
        except (TypeError, ValueError, StopIteration) as exc:
            self._warning(f"[linjie] 模块化灵界候选生成失败，回退旧公式：{exc}")
            return []

    def _module_plan(self, st: LinjieState) -> List[LinjieCandidate]:
        snapshot = self._module_snapshot(st)
        if snapshot is None:
            return []
        try:
            return [self._module_candidate(item) for item in self.module_planner.plan(snapshot, strategy=self.planning_strategy)]
        except (TypeError, ValueError, StopIteration) as exc:
            self._warning(f"[linjie] 模块化灵界路线生成失败，回退旧公式：{exc}")
            return []

    def _refresh_module_snapshot(self, key: str, st: LinjieState) -> bool:
        page_texts = dict(st.page_texts or {})
        if set(page_texts) != {"profile", "buildings", "upgrades", "workers"}:
            st.last_plan["module_parse_failed"] = True
            return False
        try:
            pages = {
                "profile": self.module_parser.parse_profile(page_texts["profile"]),
                "buildings": self.module_parser.parse_buildings(page_texts["buildings"]),
                "upgrades": self.module_parser.parse_upgrades(page_texts["upgrades"]),
                "workers": self.module_parser.parse_workers(page_texts["workers"]),
            }
            snapshot = self.module_snapshots.replace_from_pages(
                str(key),
                self._module_group_id(),
                pages,
                collected_at=datetime.now().astimezone(),
            )
        except (TypeError, ValueError, OSError) as exc:
            st.last_plan["module_parse_failed"] = True
            self._warning(f"[linjie] 四页严格快照解析失败，继续使用兼容解析：{exc}")
            return False
        st.module_snapshot = snapshot.to_dict()
        # 以官方快照的总量字段校正旧状态，旧命令/状态页仍然读取这些字段。
        st.balance = float(snapshot.balance)
        st.total_speed = float(snapshot.total_output.total)
        st.skill_dao = int(snapshot.skill_dao)
        st.skill_realm = int(snapshot.skill_realm)
        st.worker_rank = int(snapshot.worker_rank)
        st.worker_total = int(snapshot.worker_total)
        st.worker_capacity = int(snapshot.worker_capacity)
        st.worker_rank_cost = float(snapshot.rank_cost)
        st.monthly_card = bool(snapshot.has_monthly_card)
        st.page_texts = {}
        st.last_plan["module_parse_failed"] = False
        st.last_plan["planner_engine"] = "module"
        st.last_plan["planner_strategy"] = self.planning_strategy
        return True

    async def _get(self, key: str) -> LinjieState:
        return LinjieState.from_dict(await self.store.get(f"linjie:{key}"))

    async def _set(self, key: str, st: LinjieState) -> None:
        await self.store.set(f"linjie:{key}", st.to_dict())

    def _cache_ready(self, st: LinjieState) -> bool:
        if st.balance <= 0:
            return False
        return bool(st.buildings) and st.last_query_ts > 0

    def _cache_fresh(self, st: LinjieState) -> bool:
        return self._cache_ready(st) and (time.time() - st.last_query_ts <= self.cache_ttl_sec)

    async def cmd_enable(self, key: str, send_cb) -> str:
        if not self.module_enabled:
            return "🛑 灵界升级模块已在配置中关闭。"
        st = await self._get(key)
        st.enabled = True
        st.failure_count = 0
        st.blocked_commands = []
        if self._cache_fresh(st) and self._can_use_cached_state(st):
            st.phase = "RUNNING"
            st.next_action_ts = time.time() + self.success_delay_sec
            st.after_query = ""
            await self._set(key, st)
            return "✅ 已开启灵界升级，将使用当前缓存按 ROI 执行。"
        self._start_query(st, "RUN")
        await self._set(key, st)
        return "✅ 已开启灵界升级，正在查询灵界信息并建立缓存。"

    async def cmd_disable(self, key: str) -> str:
        st = await self._get(key)
        st.enabled = False
        st.phase = "IDLE"
        st.next_action_ts = 0.0
        st.wake_at_ts = 0.0
        st.pending_action = {}
        st.awaiting_query = ""
        st.after_query = ""
        await self._set(key, st)
        return "🛑 已关闭灵界升级。"

    async def cmd_status(self, key: str) -> str:
        st = await self._get(key)
        status = "✅开启" if st.enabled else "🛑关闭"
        next_text = self._phase_label(st)
        plan = self._best_affordable_or_waiting(st)
        plan_text = self._format_candidate_line(plan[0], st, plan[1]) if plan[0] else "暂无可用规划"
        return (
            "📊【灵界升级状态】\n"
            f"状态：{status} / {next_text}\n"
            f"灵矿石：{format_money(st.balance)}\n"
            f"秒产估算：{format_speed(st.total_speed)}\n"
            f"连续失败：{st.failure_count}/{self.max_failures}\n"
            f"下一步：{plan_text}"
        )

    async def cmd_plan(self, key: str, send_cb) -> str:
        st = await self._get(key)
        if self._cache_fresh(st) and self._can_use_cached_state(st):
            cand, affordable = self._best_affordable_or_waiting(st)
            return self._format_plan_reply(st, cand, affordable)
        self._start_query(st, "PLAN")
        await self._set(key, st)
        return "🔎 当前没有可用灵界缓存，正在查询：我的信息 → 建筑列表 → 升级列表 → 杂役名录。"

    async def cmd_refresh_plan(self, key: str, send_cb) -> str:
        st = await self._get(key)
        self._start_query(st, "PLAN")
        await self._set(key, st)
        return "🔄 已强制刷新灵界规划缓存，正在重新查询：我的信息 → 建筑列表 → 升级列表 → 杂役名录。"

    async def cmd_plan_detail(self, key: str, send_cb) -> str:
        st = await self._get(key)
        if self._cache_fresh(st) and self._can_use_cached_state(st):
            return self._format_plan_detail_reply(st)
        self._start_query(st, "PLAN_DETAIL")
        await self._set(key, st)
        return "🔎 当前没有可用灵界缓存，正在查询后输出规划详情。"

    async def cmd_plan_sequence(self, key: str, send_cb) -> str:
        st = await self._get(key)
        if self._cache_fresh(st) and self._can_use_cached_state(st):
            return self._format_plan_sequence_reply(st)
        self._start_query(st, "PLAN_SEQUENCE")
        await self._set(key, st)
        return "🔎 当前没有可用灵界缓存，正在查询后输出多步规划序列。"

    def summary_line(self, st: LinjieState) -> str:
        if not st.enabled:
            return "已关闭"
        return self._phase_label(st)

    def _phase_label(self, st: LinjieState) -> str:
        if not st.enabled and st.phase == "IDLE":
            return "已关闭"
        if st.phase == "QUERYING":
            commands = st.query_commands or QUERY_COMMANDS
            current = st.awaiting_query or (commands[st.query_index] if st.query_index < len(commands) else "灵界信息")
            return f"查询中：{current}"
        if st.phase == "WAITING_RESULT":
            action = st.pending_action.get("note") or st.pending_action.get("command") or "升级回执"
            return f"等待回执：{action}"
        if st.phase == "SLEEPING" and st.wake_at_ts:
            return f"攒矿中，下次 {fmt_ts(st.wake_at_ts)}"
        if st.next_action_ts:
            return f"下一动作 {fmt_ts(st.next_action_ts)}"
        return {
            "RUNNING": "运行中",
            "IDLE": "待机",
            "PAUSED": "已暂停（结果未知，避免重发）",
        }.get(st.phase, st.phase or "待机")

    def _start_query(self, st: LinjieState, after_query: str, commands: Optional[List[str]] = None) -> None:
        query_commands = list(commands or QUERY_COMMANDS)
        st.phase = "QUERYING"
        st.query_index = 0
        st.awaiting_query = ""
        st.after_query = after_query
        st.query_commands = query_commands
        st.next_action_ts = time.time()
        st.wake_at_ts = 0.0
        st.pending_action = {}
        if query_commands == QUERY_COMMANDS:
            self._reset_snapshot_cache(st)

    def _reset_snapshot_cache(self, st: LinjieState) -> None:
        st.balance = 0.0
        st.total_speed = 0.0
        st.skill_dao = -1
        st.skill_realm = -1
        st.worker_rank = -1
        st.worker_total = 0
        st.worker_capacity = 0
        st.worker_rank_cost = 0.0
        st.monthly_card = False
        st.abundance = self.default_abundance
        st.buildings = {}
        st.last_plan = {}
        st.page_texts = {}
        st.module_snapshot = {}
        st.last_query_ts = 0.0

    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        now = time.time()

        if st.phase == "QUERYING":
            if st.next_action_ts and now >= st.next_action_ts:
                if st.awaiting_query:
                    st.failure_count += 1
                    if st.failure_count >= self.max_failures:
                        await self._stop_for_failures(key, st, send_cb, f"查询「{st.awaiting_query}」超时")
                        return
                await self._send_next_query(key, st, send_cb)
            return

        if not st.enabled:
            return

        if st.phase == "SLEEPING" and st.wake_at_ts and now >= st.wake_at_ts:
            self._start_query(st, "RUN")
            await self._set(key, st)
            return

        if st.phase in ("RUNNING", "IDLE") and st.next_action_ts and now >= st.next_action_ts:
            await self._run_once(key, st, send_cb)
            return

        if st.phase == "WAITING_RESULT" and st.next_action_ts and now >= st.next_action_ts:
            # 资源动作可能已经在官方侧成功，超时后自动重发会造成重复建造/招募。
            # 保留现有状态并暂停，交由用户重新开启或刷新后再继续。
            await self._pause_for_unknown_result(key, st, send_cb, "等待灵界动作回执超时，结果未知")
            return

    async def _send_next_query(self, key: str, st: LinjieState, send_cb) -> None:
        commands = st.query_commands or QUERY_COMMANDS
        if st.query_index >= len(commands):
            st.awaiting_query = ""
            st.last_query_ts = time.time()
            await self._finish_query(key, st, send_cb)
            return
        cmd = commands[st.query_index]
        st.awaiting_query = cmd
        st.next_action_ts = time.time() + self.query_timeout_sec
        await self._set(key, st)
        if send_cb:
            await send_cb(f"@{self.official_qq} {cmd}")

    async def _finish_query(self, key: str, st: LinjieState, send_cb) -> None:
        st.failure_count = 0
        st.blocked_commands = []
        self._refresh_module_snapshot(key, st)
        after_query = st.after_query
        st.after_query = ""
        st.query_commands = []
        if after_query == "PLAN":
            st.phase = "IDLE" if not st.enabled else "RUNNING"
            st.next_action_ts = 0.0 if not st.enabled else time.time() + self.success_delay_sec
            await self._set(key, st)
            if send_cb:
                cand, affordable = self._best_affordable_or_waiting(st)
                await send_cb(self._format_plan_reply(st, cand, affordable))
            return
        if after_query == "PLAN_DETAIL":
            st.phase = "IDLE" if not st.enabled else "RUNNING"
            st.next_action_ts = 0.0 if not st.enabled else time.time() + self.success_delay_sec
            await self._set(key, st)
            if send_cb:
                await send_cb(self._format_plan_detail_reply(st))
            return
        if after_query == "PLAN_SEQUENCE":
            st.phase = "IDLE" if not st.enabled else "RUNNING"
            st.next_action_ts = 0.0 if not st.enabled else time.time() + self.success_delay_sec
            await self._set(key, st)
            if send_cb:
                await send_cb(self._format_plan_sequence_reply(st))
            return
        st.phase = "RUNNING" if st.enabled else "IDLE"
        st.next_action_ts = time.time() + self.success_delay_sec if st.enabled else 0.0
        await self._set(key, st)

    async def _run_once(self, key: str, st: LinjieState, send_cb) -> None:
        if not self._cache_ready(st):
            self._start_query(st, "RUN")
            await self._set(key, st)
            return

        cand, affordable = self._best_affordable_or_waiting(st)
        if cand is None:
            st.phase = "IDLE"
            st.next_action_ts = 0.0
            await self._set(key, st)
            if send_cb:
                await send_cb("ℹ️ 灵界升级：当前没有可执行候选。")
            return

        if not affordable:
            wait_sec = self._estimate_wait_seconds(st, cand.cost)
            st.phase = "SLEEPING"
            st.wake_at_ts = time.time() + wait_sec
            st.next_action_ts = 0.0
            st.last_plan = self._candidate_to_dict(cand)
            await self._set(key, st)
            if send_cb:
                await send_cb(
                    "💤 灵界升级：灵矿石不足，暂时等待。\n"
                    f"下一目标：{cand.note}\n"
                    f"需要：{format_money(cand.cost)}，当前：{format_money(st.balance)}，还差：{format_money(cand.cost - st.balance)}\n"
                    f"预计：{format_duration(wait_sec)}后，约 {fmt_ts(st.wake_at_ts)} 再查询确认。"
                )
            return

        st.phase = "WAITING_RESULT"
        st.pending_action = self._candidate_to_dict(cand)
        st.last_plan = self._candidate_to_dict(cand)
        st.next_action_ts = time.time() + self.action_timeout_sec
        await self._set(key, st)
        if send_cb:
            await send_cb(f"@{self.official_qq} {cand.command}")

    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        text = _clean_text(text)
        if not text:
            return

        changed = False
        parsed_kind = self._parse_snapshot_text(st, text)
        if parsed_kind:
            changed = True

        if st.phase == "QUERYING" and st.awaiting_query:
            if self._query_response_matches(st.awaiting_query, parsed_kind, text):
                page_kind = {
                    "灵界我的信息": "profile",
                    "灵界建筑列表": "buildings",
                    "灵界升级列表": "upgrades",
                    "灵界杂役名录": "workers",
                }.get(st.awaiting_query)
                if page_kind:
                    st.page_texts[page_kind] = text
                st.awaiting_query = ""
                st.query_index += 1
                st.next_action_ts = time.time() + self.success_delay_sec
                changed = True

        if st.phase == "WAITING_RESULT":
            result = self._parse_action_result(st, text)
            if result == "success":
                pending = dict(st.pending_action or {})
                st.failure_count = 0
                st.pending_action = {}
                st.blocked_commands = []
                if self._module_snapshot(st) is not None:
                    # 模块化规划依赖完整官方快照；每次成功后四页原子重查，
                    # 避免只更新局部页面造成组合路线成本/门槛过期。
                    confirm_commands = list(QUERY_COMMANDS)
                else:
                    confirm_commands = self._confirm_commands_for_action(pending) if (self.confirm_after_success or self._success_needs_confirm(st, pending)) else []
                if confirm_commands:
                    self._start_query(st, "RUN" if st.enabled else "", confirm_commands)
                else:
                    st.phase = "RUNNING" if st.enabled else "IDLE"
                    st.next_action_ts = time.time() + self.success_delay_sec if st.enabled else 0.0
                changed = True
            elif result == "insufficient":
                st.failure_count += 1
                pending = self._candidate_from_dict(st.pending_action)
                st.pending_action = {}
                if st.failure_count >= self.max_failures:
                    await self._stop_for_failures(key, st, send_cb, "灵矿石不足回执连续触发")
                    return
                target_cost = pending.cost if pending is not None else st.balance + 1
                wait_sec = self._estimate_wait_seconds(st, max(target_cost, st.balance + 1))
                st.phase = "SLEEPING"
                st.wake_at_ts = time.time() + wait_sec
                st.next_action_ts = 0.0
                changed = True
                if send_cb:
                    await send_cb(f"💤 灵矿石不足，已等待到约 {fmt_ts(st.wake_at_ts)} 后重新查询。")
            elif result == "failed":
                st.failure_count += 1
                cmd = str(st.pending_action.get("command") or "")
                if cmd and cmd not in st.blocked_commands:
                    st.blocked_commands.append(cmd)
                st.pending_action = {}
                if st.failure_count >= self.max_failures:
                    await self._stop_for_failures(key, st, send_cb, "升级失败次数达到上限")
                    return
                st.phase = "RUNNING" if st.enabled else "IDLE"
                st.next_action_ts = time.time() + self.success_delay_sec if st.enabled else 0.0
                changed = True

        if parsed_kind == "offline" and st.enabled and st.phase == "SLEEPING":
            st.phase = "RUNNING"
            st.wake_at_ts = 0.0
            st.next_action_ts = time.time() + self.success_delay_sec
            changed = True

        if changed:
            st.last_update_ts = time.time()
            await self._set(key, st)

    def _query_response_matches(self, cmd: str, parsed_kind: str, text: str) -> bool:
        if cmd == "灵界我的信息":
            return parsed_kind in {"profile", "offline"} or "个人面板" in text
        if cmd == "灵界建筑列表":
            return parsed_kind == "buildings" or "建筑列表" in text
        if cmd == "灵界升级列表":
            return parsed_kind == "tech" or "可升级建筑" in text
        if cmd == "灵界杂役名录":
            return parsed_kind == "workers" or "杂役概览" in text
        return False

    def _confirm_commands_for_action(self, action: Dict[str, Any]) -> List[str]:
        kind = str((action or {}).get("kind") or "")
        if kind == "building":
            return ["灵界建筑列表"]
        if kind == "tech":
            return ["灵界升级列表"]
        if kind in {"worker", "rank"}:
            return ["灵界杂役名录"]
        if kind == "skill":
            return ["灵界我的信息"]
        return []

    def _success_needs_confirm(self, st: LinjieState, action: Dict[str, Any]) -> bool:
        if st.balance <= 0 or st.total_speed <= 0:
            return True
        kind = str((action or {}).get("kind") or "")
        name = str((action or {}).get("name") or "")
        item = st.buildings.get(name, {}) if name else {}
        if kind == "building":
            return not item.get("count") or not item.get("build_cost")
        if kind == "tech":
            return name and not item
        if kind == "worker":
            return name and (not item.get("workers") or not item.get("worker_cost"))
        if kind == "rank":
            return st.worker_rank < 0 or st.worker_rank_cost <= 0
        if kind == "skill":
            return st.skill_dao < 0
        return False

    def _parse_snapshot_text(self, st: LinjieState, text: str) -> str:
        kind = ""
        if any(token in text for token in ("总产出", "单产", "产出加成", "杂役单产", "离线收益", "自动产出")):
            st.monthly_card = "👑+" in text
        st.abundance = self.default_abundance
        if "欢迎回来" in text and "离线收益" in text:
            self._parse_offline(st, text)
            kind = "offline"
        if "个人面板" in text or "技艺道行" in text or "技艺境界" in text:
            self._parse_profile(st, text)
            kind = kind or "profile"
        # 用表格表头特征词路由，避免底部链接[建筑列表](url)清理后误触发
        # 建筑列表表头: |建筑名称|拥有|建造|单产|下一个价格|
        # 升级列表表头: |建筑名称|建筑等级|升级价格|升级|
        # 杂役名录表头: |建筑名称|在岗/岗位|招募|产出加成|杂役单产|下一个价格|
        if "|拥有|" in text:
            self._parse_building_list(st, text)
            kind = "buildings"
        elif "|建筑等级|" in text:
            self._parse_tech_list(st, text)
            kind = "tech"
        elif "|在岗" in text or "杂役单产" in text:
            self._parse_worker_list(st, text)
            kind = "workers"
        self._parse_any_balance(st, text)
        return kind

    def _parse_profile(self, st: LinjieState, text: str) -> None:
        m = re.search(r"技艺道行[：:]\s*(\d+)", text)
        if m:
            st.skill_dao = int(m.group(1))
        m = re.search(r"技艺境界[：:]\s*(\d+)", text)
        if m:
            st.skill_realm = int(m.group(1))
        m = re.search(r"总产出[：:]\s*([^\n]+?)灵矿石/秒", text)
        if m:
            st.total_speed = parse_output_value(m.group(1))
        m = re.search(r"杂役[：:]\s*总计\s*(\d+)人", text)
        if m:
            st.worker_total = int(m.group(1))

    def _parse_offline(self, st: LinjieState, text: str) -> None:
        m = re.search(r"有效时长[：:]\s*(\d+(?:\.\d+)?)\s*小时", text)
        hours = float(m.group(1)) if m else 0.0
        m = re.search(r"离线收益[：:]\s*([^\n]+?)灵矿石", text)
        if m and hours > 0:
            st.total_speed = parse_output_value(m.group(1)) / (hours * 3600.0)
        self._parse_any_balance(st, text)

    def _parse_any_balance(self, st: LinjieState, text: str) -> None:
        matches = re.findall(r"(?:灵矿石储备|剩余储备)[：:]\s*([0-9.,]+[万亿兆京]?)(?:\s*灵矿石)?", text)
        if matches:
            st.balance = parse_money(matches[-1])
            return
        m = re.search(r"持有\s*([0-9.,]+[万亿兆京]?)", text)
        if m:
            st.balance = parse_money(m.group(1))

    def _ensure_building(self, st: LinjieState, name: str) -> Dict[str, Any]:
        item = st.buildings.setdefault(name, {"name": name})
        item.setdefault("count", 0)
        item.setdefault("tech", 0)
        item.setdefault("workers", 0)
        item.setdefault("capacity", 0)
        return item

    def _parse_building_list(self, st: LinjieState, text: str) -> None:
        parsed_any = False
        for row in self._iter_table_rows(text):
            if len(row) < 5 or row[0] == "建筑名称":
                continue
            name, owned, _, output, price = row[:5]
            if not name or name.startswith(":-"):
                continue
            # 跳过杂役名录行：建筑列表的价格列必含"灵矿石"或"🔒"
            # 杂役名录的第5列是"杂役单产"(如"23.92（👑+8.37）")，不含"灵矿石"
            if "灵矿石" not in price and "🔒" not in price:
                continue
            # 跳过杂役名录行：建筑列表的拥有列含"×"或"建造"
            # 杂役名录的第2列是"在岗/岗位"(如"43/104")，不含"×"
            if "×" not in owned and "建造" not in owned:
                continue
            item = self._ensure_building(st, name)
            m = re.search(r"×\s*(\d+)", owned)
            if m:
                item["count"] = int(m.group(1))
            else:
                self._debug(f"[linjie] 建筑列表行count解析失败: name={name} owned={owned!r}")
            item["building_output"] = parse_output_value(output)
            item["build_cost"] = parse_money(price)
            item["build_locked"] = bool("🔒" in price)
            parsed_any = True
        if not parsed_any:
            self._warning("[linjie] 建筑列表未解析到任何建筑行，可能游戏回执格式不匹配")
        m = re.search(r"总建筑数[：:]\s*(\d+)", text)
        if m:
            st.last_plan["building_total"] = int(m.group(1))
        m = re.search(r"总产出[：:]\s*([^\n]+?)灵矿石/秒", text)
        if m:
            st.total_speed = parse_output_value(m.group(1))

    def _parse_tech_list(self, st: LinjieState, text: str) -> None:
        for row in self._iter_table_rows(text):
            if len(row) < 4 or row[0] == "建筑名称":
                continue
            name, level, price, action = row[:4]
            if not name or name.startswith(":-"):
                continue
            # 跳过非升级列表行：建筑列表的level列含"×"，杂役名录的含"/"
            if "×" in level or "/" in level:
                continue
            item = self._ensure_building(st, name)
            try:
                item["tech"] = int(re.search(r"\d+", level).group(0))
            except Exception:
                pass
            item["tech_cost"] = parse_money(price)
            item["tech_available"] = bool(item.get("tech_cost", 0) > 0 and "升级" in action)

    def _parse_worker_list(self, st: LinjieState, text: str) -> None:
        m = re.search(r"杂役总数[：:]\s*(\d+)\s*/\s*(\d+)人", text)
        if m:
            st.worker_total = int(m.group(1))
            st.worker_capacity = int(m.group(2))
        m = re.search(r"杂役等阶[：:]\s*LV\s*(\d+)", text, re.I)
        if m:
            st.worker_rank = int(m.group(1))
        m = re.search(r"杂役升阶[^\n]*?需要\s*([0-9.,]+[万亿兆京]?)\s*灵矿石", text)
        if m:
            st.worker_rank_cost = parse_money(m.group(1))
        parsed_any = False
        for row in self._iter_table_rows(text):
            if len(row) < 6 or row[0] == "建筑名称":
                continue
            name, slot, _, worker_output, worker_single, price = row[:6]
            if not name or name.startswith(":-"):
                continue
            item = self._ensure_building(st, name)
            m = re.search(r"(\d+)\s*/\s*(\d+)", slot)
            if m:
                item["workers"] = int(m.group(1))
                item["capacity"] = int(m.group(2))
            else:
                self._debug(f"[linjie] 杂役名录行解析失败: name={name} slot={slot!r}")
            item["worker_output"] = parse_output_value(worker_output)
            item["worker_single"] = parse_output_value(worker_single)
            item["worker_cost"] = parse_money(price)
            if item.get("count") and item.get("capacity"):
                item["capacity_per_building"] = float(item["capacity"]) / max(1.0, float(item["count"]))
            parsed_any = True
        if not parsed_any:
            self._warning("[linjie] 杂役名录未解析到任何建筑行，可能游戏回执格式不匹配")

    def _iter_table_rows(self, text: str) -> List[List[str]]:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|") or line.count("|") < 3:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and not all(set(c) <= {":", "-"} for c in cells):
                rows.append(cells)
        return rows

    def _parse_action_result(self, st: LinjieState, text: str) -> str:
        if "灵矿石不足" in text:
            self._parse_any_balance(st, text)
            m = re.search(r"需求\s*([0-9.,]+[万亿兆京]?)", text)
            if m and st.pending_action:
                st.pending_action["cost"] = parse_money(m.group(1))
            return "insufficient"

        if any(k in text for k in ("条件不足", "无法", "失败", "错误", "不存在", "已满", "达到上限")) and "成功" not in text:
            self._parse_any_balance(st, text)
            return "failed"

        if "建造成功" in text:
            self._apply_build_success(st, text)
            return "success"
        if "升级成功" in text:
            self._apply_tech_success(st, text)
            return "success"
        if "招募成功" in text:
            self._apply_worker_success(st, text)
            return "success"
        if "杂役技艺提升成功" in text or "杂役升阶成功" in text:
            self._apply_rank_success(st, text)
            return "success"
        if "修习成功" in text or ("技艺" in text and "成功" in text and ("道行" in text or "修行" in text or "修习" in text or "突破" in text)):
            self._apply_skill_success(st, text)
            return "success"
        return ""

    def _apply_common_success(self, st: LinjieState, text: str) -> Tuple[float, float]:
        spent = 0.0
        m = re.search(r"花费[：:]\s*([0-9.,]+[万亿兆京]?)", text)
        if m:
            spent = parse_money(m.group(1))
        before_after = re.findall(r"自动产出[：:]\s*(.+?)→(.+?)灵矿石/秒", text)
        if before_after:
            st.total_speed = parse_output_value(before_after[-1][1])
        self._parse_any_balance(st, text)
        if st.balance <= 0 and spent > 0:
            st.balance = max(0.0, st.balance - spent)
        return spent, st.total_speed

    def _apply_build_success(self, st: LinjieState, text: str) -> None:
        pending = dict(st.pending_action or {})
        m = re.search(r"建造成功！\s*(.+?)×\s*(\d+)", text)
        name = m.group(1).strip() if m else str(pending.get("name") or "")
        qty = int(m.group(2)) if m else 1
        self._apply_common_success(st, text)
        if not name:
            return
        item = self._ensure_building(st, name)
        m2 = re.search(rf"{re.escape(name)}[：:]\s*(\d+)\s*→\s*(\d+)", text)
        if m2:
            item["count"] = int(m2.group(2))
        else:
            item["count"] = int(item.get("count", 0)) + qty
        if item.get("build_cost"):
            item["build_cost"] = float(item["build_cost"]) * (1.25 ** qty)
        cap = item.get("capacity_per_building") or CAP_PER_BUILDING.get(name, 0)
        if cap:
            item["capacity"] = int(round(float(item.get("capacity", 0)) + float(cap) * qty))
        self._refresh_unlocked_tech_cost(item, name)

    def _apply_tech_success(self, st: LinjieState, text: str) -> None:
        pending = dict(st.pending_action or {})
        m = re.search(r"升级成功！\s*(.+?)LV\s*(\d+)\s*→\s*.+?LV\s*(\d+)", text)
        name = m.group(1).strip() if m else str(pending.get("name") or "")
        old = int(m.group(2)) if m else int(self._ensure_building(st, name).get("tech", 0))
        new = int(m.group(3)) if m else old + 1
        self._apply_common_success(st, text)
        if not name:
            return
        item = self._ensure_building(st, name)
        item["tech"] = new
        if item.get("building_output") and old >= 0:
            item["building_output"] = float(item["building_output"]) * (new + 1) / max(1.0, old + 1)
        if item.get("worker_single"):
            old_factor = _worker_tech_factor(name, old)
            new_factor = _worker_tech_factor(name, new)
            item["worker_single"] = float(item["worker_single"]) * new_factor / max(0.0001, old_factor)
            item["worker_output"] = float(item.get("workers", 0)) * float(item["worker_single"])
        self._refresh_unlocked_tech_cost(item, name)

    def _apply_worker_success(self, st: LinjieState, text: str) -> None:
        pending = dict(st.pending_action or {})
        m = re.search(r"招募成功！\s*(.+?)的杂役数量增加\s*(\d+)", text)
        name = m.group(1).strip() if m else str(pending.get("name") or "")
        qty = int(m.group(2)) if m else 1
        self._apply_common_success(st, text)
        if not name:
            return
        item = self._ensure_building(st, name)
        m2 = re.search(rf"{re.escape(name)}杂役数量[：:]\s*(\d+)\s*→\s*(\d+)", text)
        if m2:
            item["workers"] = int(m2.group(2))
        else:
            item["workers"] = int(item.get("workers", 0)) + qty
        st.worker_total = max(st.worker_total + qty, sum(int(v.get("workers", 0)) for v in st.buildings.values()))
        if item.get("worker_cost"):
            item["worker_cost"] = float(item["worker_cost"]) * (1.1 ** qty)
        if item.get("worker_single"):
            item["worker_output"] = float(item.get("workers", 0)) * float(item["worker_single"])

    def _apply_rank_success(self, st: LinjieState, text: str) -> None:
        old = st.worker_rank
        m = re.search(r"LV\s*(\d+)\s*→\s*LV\s*(\d+)", text)
        if m:
            old = int(m.group(1))
            st.worker_rank = int(m.group(2))
        elif st.worker_rank >= 0:
            st.worker_rank += 1
        self._apply_common_success(st, text)
        if old >= 0 and st.worker_rank >= 0:
            ratio = _worker_rank_factor(st.worker_rank) / max(0.0001, _worker_rank_factor(old))
            for item in st.buildings.values():
                if item.get("worker_single"):
                    item["worker_single"] = float(item["worker_single"]) * ratio
                    item["worker_output"] = float(item.get("workers", 0)) * float(item["worker_single"])
        if st.worker_rank_cost:
            st.worker_rank_cost = float(st.worker_rank_cost) * 2.2

    def _apply_skill_success(self, st: LinjieState, text: str) -> None:
        m = re.search(r"(\d+)\s*→\s*(\d+)", text)
        if m:
            st.skill_dao = int(m.group(2))
        elif st.skill_dao >= 0:
            st.skill_dao += 1
        self._apply_common_success(st, text)

    def _refresh_unlocked_tech_cost(self, item: Dict[str, Any], name: str) -> None:
        tech = int(item.get("tech", 0) or 0)
        count = int(item.get("count", 0) or 0)
        tech_limit = min(6, count // 10)
        if tech >= 6 or tech >= tech_limit:
            item["tech_available"] = False
            item["tech_cost"] = 0.0
            return
        base = TECH_BASE_COST.get(name, 0.0)
        if base:
            item["tech_cost"] = base * (10 ** tech)
            item["tech_available"] = True

    def _best_affordable_or_waiting(self, st: LinjieState) -> Tuple[Optional[LinjieCandidate], bool]:
        module_plan = self._module_plan(st)
        if module_plan:
            spendable = max(0.0, st.balance - self.reserve_lingkuang)
            unblocked = [c for c in module_plan if c.command not in st.blocked_commands] or module_plan
            best = unblocked[0]
            return best, best.cost <= spendable
        candidates = self._build_candidates(st)
        if not candidates:
            return None, False
        spendable = max(0.0, st.balance - self.reserve_lingkuang)
        unblocked = [c for c in candidates if c.command not in st.blocked_commands] or candidates
        best = min(unblocked, key=lambda c: c.roi_days)
        return best, best.cost <= spendable

    def _build_candidates(self, st: LinjieState) -> List[LinjieCandidate]:
        module_candidates = self._module_candidates(st)
        if module_candidates:
            return module_candidates
        if self.roi_formula_source != "game_display":
            return self._build_candidates_excel(st)
        return self._build_candidates_display(st)

    def _build_candidates_display(self, st: LinjieState) -> List[LinjieCandidate]:
        candidates: List[LinjieCandidate] = []
        for name, item in st.buildings.items():
            build_cost = float(item.get("build_cost", 0) or 0)
            build_gain = float(item.get("building_output", 0) or 0)
            if build_cost > 0 and build_gain > 0 and not item.get("build_locked"):
                candidates.append(LinjieCandidate("building", name, build_cost, build_gain, f"灵界建造{name} 1", f"{name}+1座"))

            worker_cost = float(item.get("worker_cost", 0) or 0)
            worker_gain = float(item.get("worker_single", 0) or 0)
            workers = int(item.get("workers", 0) or 0)
            capacity = int(item.get("capacity", 0) or 0)

            tech_cost = float(item.get("tech_cost", 0) or 0)
            if item.get("tech_available") and tech_cost > 0:
                gain = self._estimate_tech_gain(name, item)
                if gain > 0:
                    tech = int(item.get("tech", 0) or 0)
                    candidates.append(LinjieCandidate("tech", name, tech_cost, gain, f"灵界升级建筑{name}", f"{name}建筑等级 {tech}→{tech + 1}"))

            if worker_cost > 0 and worker_gain > 0 and capacity > 0 and workers < capacity:
                candidates.append(LinjieCandidate("worker", name, worker_cost, worker_gain, f"灵界招募{name} 1", f"{name}+1杂役"))

        if st.worker_rank_cost > 0:
            gain = self._estimate_rank_gain(st)
            if gain > 0:
                rank = st.worker_rank if st.worker_rank >= 0 else 0
                candidates.append(LinjieCandidate("rank", "杂役等阶", st.worker_rank_cost, gain, "灵界杂役升阶", f"杂役等阶 LV{rank}→LV{rank + 1}"))

        if self.include_skill_training and st.skill_dao >= 0:
            cost = 15.0 * (1.2 ** st.skill_dao)
            gain = 0.01 * st.skill_dao + 0.1
            candidates.append(LinjieCandidate("skill", "技艺道行", cost, gain, "灵界技艺修行", f"技艺道行 {st.skill_dao}→{st.skill_dao + 1}"))

        return [c for c in candidates if c.cost > 0 and c.gain > 0]

    def _build_candidates_excel(self, st: LinjieState) -> List[LinjieCandidate]:
        candidates: List[LinjieCandidate] = []
        monthly_factor = 1.35 if st.monthly_card else 1.0
        abundance_factor = 1.0 if st.abundance else 1.0 / 1.05
        rank = st.worker_rank if st.worker_rank >= 0 else 20
        rank_factor = _worker_rank_factor(rank)

        parsed_names = set()
        for name, item in st.buildings.items():
            count = int(item.get("count", 0) or 0)
            tech = int(item.get("tech", 0) or 0)
            workers = int(item.get("workers", 0) or 0)
            capacity = int(item.get("capacity", 0) or 0)
            if capacity <= 0 and count > 0:
                formula_cap = CAP_PER_BUILDING.get(name, 0) * count
                if formula_cap > 0:
                    self._debug(
                        f"[linjie] {name} 解析capacity={capacity}异常,回退公式={formula_cap}"
                    )
                    capacity = formula_cap
            parsed_names.add(name)

            build_cost = float(item.get("build_cost", 0) or 0)
            if build_cost <= 0 and name in BASE_BUILD_COST:
                build_cost = BASE_BUILD_COST[name] * (1.25 ** count)
            build_gain = BASE_OUTPUT.get(name, 0.0) * (tech + 1) * abundance_factor * monthly_factor
            if build_cost > 0 and build_gain > 0 and not item.get("build_locked"):
                candidates.append(LinjieCandidate("building", name, build_cost, build_gain, f"灵界建造{name} 1", f"{name}+1座"))

            worker_cost = float(item.get("worker_cost", 0) or 0)
            if worker_cost <= 0 and name in BASE_WORKER_COST:
                worker_cost = BASE_WORKER_COST[name] * (1.1 ** workers)
            worker_gain = (
                BASE_WORKER_OUTPUT.get(name, 0.0)
                * _worker_tech_factor(name, tech)
                * rank_factor
                * abundance_factor
                * monthly_factor
            )

            tech_cost = float(item.get("tech_cost", 0) or 0)
            if tech_cost <= 0 and name in TECH_BASE_COST:
                tech_cost = TECH_BASE_COST[name] * (10 ** tech)
            tech_limit = min(6, count // 10)
            if tech < 6 and tech < tech_limit and tech_cost > 0:
                gain = self._estimate_tech_gain_excel(name, count, tech, workers, rank, st.abundance, st.monthly_card)
                if gain > 0:
                    candidates.append(LinjieCandidate("tech", name, tech_cost, gain, f"灵界升级建筑{name}", f"{name}建筑等级 {tech}→{tech + 1}"))

            if worker_cost > 0 and worker_gain > 0 and capacity > 0 and workers < capacity:
                candidates.append(LinjieCandidate("worker", name, worker_cost, worker_gain, f"灵界招募{name} 1", f"{name}+1杂役"))

        missing = [n for n in BUILDING_ORDER if n not in parsed_names]
        if missing:
            self._info(f"[linjie] 缓存中缺失建筑数据: {', '.join(missing)}")

        rank_cost = st.worker_rank_cost or (1000.0 * (2.2 ** max(rank, 0)))
        rank_gain = self._estimate_rank_gain_excel(st, rank)
        if rank_cost > 0 and rank_gain > 0:
            candidates.append(LinjieCandidate("rank", "杂役等阶", rank_cost, rank_gain, "灵界杂役升阶", f"杂役等阶 LV{rank}→LV{rank + 1}"))

        if self.include_skill_training and st.skill_dao >= 0:
            cost = 15.0 * (1.2 ** st.skill_dao)
            gain = (0.01 * st.skill_dao + 0.1) * monthly_factor
            candidates.append(LinjieCandidate("skill", "技艺道行", cost, gain, "灵界技艺修行", f"技艺道行 {st.skill_dao}→{st.skill_dao + 1}"))

        return [c for c in candidates if c.cost > 0 and c.gain > 0]

    def _module_multi_step_plan(self, st: LinjieState) -> List[Dict[str, Any]]:
        snapshot = self._module_snapshot(st)
        if snapshot is None:
            return []
        try:
            planned = self.module_planner.multi_step_plan(
                snapshot,
                strategy=self.planning_strategy,
                max_steps=self.max_sim_steps,
            )
        except (TypeError, ValueError, StopIteration) as exc:
            self._warning(f"[linjie] 模块化灵界长线推演失败，回退旧模拟：{exc}")
            return []
        balance = float(snapshot.balance)
        speed = max(0.0, float(snapshot.total_output.total))
        steps: List[Dict[str, Any]] = []
        for candidate in planned:
            cost = float(candidate.cost)
            gain = float(candidate.gain)
            affordable = balance >= cost
            wait_sec = 0.0
            if not affordable:
                wait_sec = (cost - balance) / max(1.0, speed)
                balance = 0.0
            balance = max(0.0, balance - cost)
            steps.append({
                "kind": candidate.kind,
                "name": candidate.name,
                "cost": cost,
                "gain": gain,
                "command": candidate.command,
                "note": candidate.note,
                "affordable": affordable,
                "wait_sec": wait_sec,
                "available_after_seconds": candidate.available_after_seconds,
                "projected_balance_after": candidate.projected_balance_after,
                "route_name": candidate.route_name,
                "route_target_count": candidate.route_target_count,
                "route_target_level": candidate.route_target_level,
                "amount": candidate.amount,
            })
            speed += gain
        return steps

    def _simulate_multi_step_plan(self, st: LinjieState) -> List[Dict[str, Any]]:
        """多步滚动 ROI 贪心模拟，与 Excel 保持一致。
        每一步：从当前模拟态生成全部候选 → 选 ROI 最优 → 应用状态变更 → 进入下一步。
        """
        if self._module_snapshot(st) is not None:
            return self._module_multi_step_plan(st)
        sim_buildings: Dict[str, Dict[str, Any]] = {}
        for name, item in st.buildings.items():
            sim_buildings[name] = {
                "count": int(item.get("count", 0) or 0),
                "tech": int(item.get("tech", 0) or 0),
                "workers": int(item.get("workers", 0) or 0),
                "build_locked": bool(item.get("build_locked", False)),
            }
        sim_state: Dict[str, Any] = {
            "balance": float(st.balance),
            "worker_rank": st.worker_rank if st.worker_rank >= 0 else 20,
            "skill_dao": st.skill_dao,
        }

        steps: List[Dict[str, Any]] = []
        for _ in range(self.max_sim_steps):
            candidates = self._sim_build_candidates(
                sim_buildings, sim_state["balance"],
                sim_state["worker_rank"], sim_state["skill_dao"],
                st.abundance, st.monthly_card,
            )
            if not candidates:
                break
            best = min(candidates, key=lambda c: c.roi_days)
            step_data = self._candidate_to_dict(best)
            if sim_state["balance"] >= best.cost:
                step_data["affordable"] = True
                step_data["wait_sec"] = 0.0
            else:
                step_data["affordable"] = False
                speed = max(1.0, float(st.total_speed or 0.0))
                step_data["wait_sec"] = (best.cost - sim_state["balance"]) / speed
            steps.append(step_data)
            self._sim_apply_step(sim_buildings, sim_state, best)
            if sim_state["balance"] >= best.cost:
                sim_state["balance"] -= best.cost
            else:
                sim_state["balance"] = 0.0
        return steps

    def _sim_build_candidates(
        self,
        buildings: Dict[str, Dict[str, Any]],
        balance: float,
        worker_rank: int,
        skill_dao: int,
        abundance: bool,
        monthly_card: bool,
    ) -> List[LinjieCandidate]:
        """基于模拟态生成候选，公式与 _build_candidates_excel 完全一致。"""
        candidates: List[LinjieCandidate] = []
        monthly_factor = 1.35 if monthly_card else 1.0
        abundance_factor = 1.0 if abundance else 1.0 / 1.05
        rank_factor = _worker_rank_factor(worker_rank)

        for name, item in buildings.items():
            count = int(item.get("count", 0))
            tech = int(item.get("tech", 0))
            workers = int(item.get("workers", 0))
            cap = CAP_PER_BUILDING.get(name, 0) * count

            build_cost = BASE_BUILD_COST.get(name, 0.0) * (1.25 ** count)
            build_gain = BASE_OUTPUT.get(name, 0.0) * (tech + 1) * abundance_factor * monthly_factor
            if build_cost > 0 and build_gain > 0 and not item.get("build_locked"):
                candidates.append(LinjieCandidate(
                    "building", name, build_cost, build_gain,
                    f"灵界建造{name} 1", f"{name}+1座",
                ))

            worker_cost = BASE_WORKER_COST.get(name, 0.0) * (1.1 ** workers)
            worker_gain = (
                BASE_WORKER_OUTPUT.get(name, 0.0)
                * _worker_tech_factor(name, tech)
                * rank_factor
                * abundance_factor
                * monthly_factor
            )

            # 建筑技艺
            tech_cost = TECH_BASE_COST.get(name, 0.0) * (10 ** tech)
            tech_limit = min(6, count // 10)
            if tech < 6 and tech < tech_limit and tech_cost > 0:
                gain = self._estimate_tech_gain_excel(
                    name, count, tech, workers, worker_rank, abundance, monthly_card,
                )
                if gain > 0:
                    candidates.append(LinjieCandidate(
                        "tech", name, tech_cost, gain,
                        f"灵界升级建筑{name}", f"{name}建筑等级 {tech}→{tech + 1}",
                    ))

            if worker_cost > 0 and worker_gain > 0 and cap > 0 and workers < cap:
                candidates.append(LinjieCandidate(
                    "worker", name, worker_cost, worker_gain,
                    f"灵界招募{name} 1", f"{name}+1杂役",
                ))

        # 杂役等阶
        rank_cost = 1000.0 * (2.2 ** max(worker_rank, 0))
        rank_gain = self._estimate_rank_gain_from_sim(
            buildings, worker_rank, abundance, monthly_card,
        )
        if rank_cost > 0 and rank_gain > 0:
            candidates.append(LinjieCandidate(
                "rank", "杂役等阶", rank_cost, rank_gain,
                "灵界杂役升阶", f"杂役等阶 LV{worker_rank}→LV{worker_rank + 1}",
            ))

        # 技艺道行
        if self.include_skill_training and skill_dao >= 0:
            cost = 15.0 * (1.2 ** skill_dao)
            gain = (0.01 * skill_dao + 0.1) * monthly_factor
            candidates.append(LinjieCandidate(
                "skill", "技艺道行", cost, gain,
                "灵界技艺修行", f"技艺道行 {skill_dao}→{skill_dao + 1}",
            ))

        return [c for c in candidates if c.cost > 0 and c.gain > 0]

    def _estimate_rank_gain_from_sim(
        self,
        buildings: Dict[str, Dict[str, Any]],
        rank: int,
        abundance: bool,
        monthly_card: bool,
    ) -> float:
        abundance_factor = 1.0 if abundance else 1.0 / 1.05
        monthly_factor = 1.35 if monthly_card else 1.0
        total = 0.0
        for name, item in buildings.items():
            workers = int(item.get("workers", 0))
            tech = int(item.get("tech", 0))
            total += workers * BASE_WORKER_OUTPUT.get(name, 0.0) * _worker_tech_factor(name, tech)
        return total * (_worker_rank_factor(rank + 1) - _worker_rank_factor(rank)) * abundance_factor * monthly_factor

    def _sim_apply_step(
        self,
        buildings: Dict[str, Dict[str, Any]],
        sim_state: Dict[str, Any],
        cand: LinjieCandidate,
    ) -> None:
        """将候选操作应用到模拟态，更新建筑数量/技艺/杂役等。"""
        name = cand.name
        item = buildings.setdefault(name, {
            "count": 0, "tech": 0, "workers": 0, "build_locked": False,
        })
        if cand.kind == "building":
            item["count"] = int(item.get("count", 0)) + 1
        elif cand.kind == "tech":
            item["tech"] = int(item.get("tech", 0)) + 1
        elif cand.kind == "worker":
            item["workers"] = int(item.get("workers", 0)) + 1
        elif cand.kind == "rank":
            sim_state["worker_rank"] = int(sim_state.get("worker_rank", 20)) + 1
        elif cand.kind == "skill":
            sim_state["skill_dao"] = int(sim_state.get("skill_dao", 0)) + 1

    def _estimate_tech_gain(self, name: str, item: Dict[str, Any]) -> float:
        tech = int(item.get("tech", 0) or 0)
        count = int(item.get("count", 0) or 0)
        building_single = float(item.get("building_output", 0) or 0)
        building_gain = count * building_single / max(1.0, tech + 1)

        workers = int(item.get("workers", 0) or 0)
        worker_single = float(item.get("worker_single", 0) or 0)
        coef = WORKER_TECH_COEF.get(name, 1.0)
        worker_gain = workers * worker_single * coef / max(0.0001, _worker_tech_factor(name, tech))
        return building_gain + worker_gain

    def _estimate_tech_gain_excel(self, name: str, count: int, tech: int, workers: int, rank: int, abundance: bool, monthly_card: bool) -> float:
        monthly_factor = 1.35 if monthly_card else 1.0
        abundance_factor = 1.0 if abundance else 1.0 / 1.05
        building_gain = count * BASE_OUTPUT.get(name, 0.0) * abundance_factor

        old_worker = (
            workers
            * BASE_WORKER_OUTPUT.get(name, 0.0)
            * _worker_tech_factor(name, tech)
            * _worker_rank_factor(rank)
            * abundance_factor
        )
        new_worker = (
            workers
            * BASE_WORKER_OUTPUT.get(name, 0.0)
            * _worker_tech_factor(name, tech + 1)
            * _worker_rank_factor(rank)
            * abundance_factor
        )
        return (building_gain + (new_worker - old_worker)) * monthly_factor

    def _estimate_rank_gain(self, st: LinjieState) -> float:
        worker_total_output = sum(float(v.get("worker_output", 0) or 0) for v in st.buildings.values())
        if worker_total_output <= 0:
            worker_total_output = sum(float(v.get("workers", 0) or 0) * float(v.get("worker_single", 0) or 0) for v in st.buildings.values())
        if st.worker_rank < 0:
            return worker_total_output * 0.08
        old = _worker_rank_factor(st.worker_rank)
        new = _worker_rank_factor(st.worker_rank + 1)
        return worker_total_output * (new / max(0.0001, old) - 1.0)

    def _estimate_rank_gain_excel(self, st: LinjieState, rank: int) -> float:
        abundance_factor = 1.0 if st.abundance else 1.0 / 1.05
        monthly_factor = 1.35 if st.monthly_card else 1.0
        total = 0.0
        for name, item in st.buildings.items():
            workers = int(item.get("workers", 0) or 0)
            tech = int(item.get("tech", 0) or 0)
            total += workers * BASE_WORKER_OUTPUT.get(name, 0.0) * _worker_tech_factor(name, tech)
        return total * (_worker_rank_factor(rank + 1) - _worker_rank_factor(rank)) * abundance_factor * monthly_factor

    def _estimate_wait_seconds(self, st: LinjieState, target_cost: float) -> float:
        shortage = max(0.0, float(target_cost or 0.0) + self.reserve_lingkuang - float(st.balance or 0.0))
        speed = max(1.0, float(st.total_speed or 0.0))
        return max(60.0, shortage / speed)

    def _candidate_to_dict(self, cand: LinjieCandidate) -> Dict[str, Any]:
        return {
            "kind": cand.kind,
            "name": cand.name,
            "cost": cand.cost,
            "gain": cand.gain,
            "command": cand.command,
            "note": cand.note,
            "available_after_seconds": cand.available_after_seconds,
            "projected_balance_after": cand.projected_balance_after,
            "route_name": cand.route_name,
            "route_target_count": cand.route_target_count,
            "route_target_level": cand.route_target_level,
            "amount": cand.amount,
        }

    def _candidate_from_dict(self, data: Dict[str, Any]) -> Optional[LinjieCandidate]:
        if not isinstance(data, dict) or not data.get("command"):
            return None
        try:
            return LinjieCandidate(
                kind=str(data.get("kind") or ""),
                name=str(data.get("name") or ""),
                cost=float(data.get("cost") or 0.0),
                gain=float(data.get("gain") or 0.0),
                command=str(data.get("command") or ""),
                note=str(data.get("note") or ""),
                available_after_seconds=int(data.get("available_after_seconds") or 0),
                projected_balance_after=float(data.get("projected_balance_after") or 0.0),
                route_name=(str(data.get("route_name")) if data.get("route_name") else None),
                route_target_count=(int(data.get("route_target_count")) if data.get("route_target_count") is not None else None),
                route_target_level=(int(data.get("route_target_level")) if data.get("route_target_level") is not None else None),
                amount=int(data.get("amount") or 1),
            )
        except Exception:
            return None

    def _format_candidate_line(self, cand: LinjieCandidate, st: LinjieState, affordable: bool) -> str:
        if cand is None:
            return "暂无"
        prefix = "可执行" if affordable else "待攒矿"
        return (
            f"{prefix}：{cand.note}，需要 {format_money(cand.cost)}，"
            f"增产约 {format_speed(cand.gain)}，ROI {cand.roi_days:.2f}天"
        )

    def _format_plan_reply(self, st: LinjieState, cand: Optional[LinjieCandidate], affordable: bool) -> str:
        if cand is None:
            return "📋【灵界规划】当前缓存里没有可规划项目。"
        lines = [
            "📋【灵界规划】",
            (
                f"规划器：官方显示值组合路线（{self.planning_strategy}）"
                if self._module_snapshot(st) is not None
                else f"ROI模式：{'Excel公式' if self.roi_formula_source != 'game_display' else '游戏显示值'}"
            ),
            f"灵矿石：{format_money(st.balance)}",
            f"秒产估算：{format_speed(st.total_speed)}",
            self._format_candidate_line(cand, st, affordable),
        ]
        if affordable:
            lines.append("预计发送：当前灵矿石足够，开启灵界升级后会立即执行。")
        else:
            wait_sec = self._estimate_wait_seconds(st, cand.cost)
            lines.append(f"还差：{format_money(cand.cost - st.balance)}，预计 {format_duration(wait_sec)}")
            lines.append(f"预计发送：约 {fmt_ts(time.time() + wait_sec)}")
        lines.append(f"指令：{cand.command}")
        return "\n".join(lines)

    def _format_plan_detail_reply(self, st: LinjieState) -> str:
        candidates = self._build_candidates(st)
        if not candidates:
            return "📋【灵界规划详情】当前缓存里没有可规划项目。"
        spendable = max(0.0, st.balance - self.reserve_lingkuang)
        candidates = [c for c in candidates if c.command not in st.blocked_commands]
        candidates.sort(key=lambda c: c.roi_days)
        lines = [
            "📋【灵界规划详情】",
            (
                f"规划器：官方显示值组合路线（{self.planning_strategy}）"
                if self._module_snapshot(st) is not None
                else f"ROI模式：{'Excel公式' if self.roi_formula_source != 'game_display' else '游戏显示值'}"
            ),
            f"灵矿石：{format_money(st.balance)}，可用：{format_money(spendable)}",
            f"月卡：{'是' if st.monthly_card else '否'}，丰饶：{'是' if st.abundance else '否'}，杂役等阶：LV{st.worker_rank if st.worker_rank >= 0 else '未知'}",
            f"缓存时间：{format_duration(max(0.0, time.time() - st.last_query_ts))}前",
            "前几名：",
        ]
        for idx, cand in enumerate(candidates[:8], 1):
            affordable = cand.cost <= spendable
            lines.append(
                f"{idx}. {'✅' if affordable else '⏳'} {cand.note} "
                f"成本{format_money(cand.cost)} 增产{format_speed(cand.gain)} ROI{cand.roi_days:.2f}天"
            )
        lingfu = [c for c in candidates if "灵符堂" in c.name or "灵符堂" in c.note]
        if lingfu:
            best = min(lingfu, key=lambda c: c.roi_days)
            lines.append(f"灵符堂最佳：{best.note} 成本{format_money(best.cost)} 增产{format_speed(best.gain)} ROI{best.roi_days:.2f}天")
        else:
            item = st.buildings.get("灵符堂")
            if item:
                lines.append("灵符堂未进入候选：可能已满员、价格/增益为0，或当前建筑等级/数量条件不足。")
            else:
                lines.append("灵符堂未进入候选：当前缓存没有解析到灵符堂数据。")

        # 建筑数据诊断
        lines.append("")
        lines.append("📊 建筑数据诊断：")
        for bname in BUILDING_ORDER:
            item = st.buildings.get(bname)
            if not item:
                lines.append(f"  {bname}: ❌未获取")
                continue
            count = int(item.get("count", 0) or 0)
            tech = int(item.get("tech", 0) or 0)
            workers = int(item.get("workers", 0) or 0)
            cap = int(item.get("capacity", 0) or 0)
            if cap <= 0 and count > 0:
                cap = CAP_PER_BUILDING.get(bname, 0) * count
                cap_mark = f"{cap}(公式回退)"
            else:
                cap_mark = str(cap)
            locked = "🔒" if item.get("build_locked") else ""
            lines.append(f"  {bname}: 数量{count} 等级{tech} 杂役{workers}/{cap_mark} {locked}")
        # 追加多步序列摘要
        steps = self._simulate_multi_step_plan(st)
        if steps:
            lines.append("")
            lines.append("🔢 多步滚动模拟前5步：")
            remaining = float(st.balance)
            for idx, step in enumerate(steps[:5], 1):
                cost = float(step.get("cost", 0))
                gain = float(step.get("gain", 0))
                roi = cost / gain / 86400.0 if gain > 0 else float("inf")
                affordable = bool(step.get("affordable", True))
                if affordable:
                    remaining = max(0.0, remaining - cost)
                else:
                    remaining = 0.0
                lines.append(
                    f"  {idx}. {'✅' if affordable else '⏳'} {step.get('note')} "
                    f"成本{format_money(cost)} 增产{format_speed(gain)} ROI{roi:.2f}天"
                )
            if len(steps) > 5:
                lines.append(f"  …完整序列共{len(steps)}步，使用「灵界规划序列」查看全部。")
        return "\n".join(lines)

    def _format_plan_sequence_reply(self, st: LinjieState) -> str:
        steps = self._simulate_multi_step_plan(st)
        if not steps:
            return "📋【灵界规划序列】当前缓存没有可规划的模拟步骤。"
        lines = [
            "📋【灵界规划序列】多步 ROI 滚动模拟",
            (
                f"规划器：官方显示值组合路线（{self.planning_strategy}）"
                if self._module_snapshot(st) is not None
                else f"ROI模式：{'Excel公式' if self.roi_formula_source != 'game_display' else '游戏显示值'}"
            ),
            f"灵矿石：{format_money(st.balance)}，月卡：{'是' if st.monthly_card else '否'}，丰饶：{'是' if st.abundance else '否'}，杂役等阶：LV{st.worker_rank if st.worker_rank >= 0 else '未知'}",
            "",
        ]
        remaining = float(st.balance)
        total_cost = 0.0
        total_gain = 0.0
        for idx, step in enumerate(steps, 1):
            cost = float(step.get("cost", 0))
            gain = float(step.get("gain", 0))
            roi = cost / gain / 86400.0 if gain > 0 else float("inf")
            affordable = bool(step.get("affordable", True))
            wait_sec = float(step.get("wait_sec", 0.0))
            if affordable:
                remaining = max(0.0, remaining - cost)
            else:
                remaining = 0.0
            total_cost += cost
            total_gain += gain
            wait_tag = "" if affordable else f"(攒矿{format_duration(wait_sec)})"
            lines.append(
                f"{idx}. {'✅' if affordable else '⏳'} {step.get('note')} "
                f"成本{format_money(cost)} 增产{format_speed(gain)} "
                f"ROI{roi:.2f}天 {wait_tag}"
            )
        lines.append("")
        if total_gain > 0:
            overall_roi = total_cost / total_gain / 86400.0
            lines.append(f"合计：{len(steps)}步 总成本{format_money(total_cost)} 总增产{format_speed(total_gain)} 综合ROI{overall_roi:.2f}天")
        if total_cost > st.balance:
            shortage = total_cost - st.balance
            wait_sec = self._estimate_wait_seconds(st, total_cost)
            lines.append(f"总成本超出{format_money(shortage)}，预计攒矿{format_duration(wait_sec)}")
        return "\n".join(lines)

    async def _stop_for_failures(self, key: str, st: LinjieState, send_cb, reason: str) -> None:
        st.enabled = False
        st.phase = "IDLE"
        st.next_action_ts = 0.0
        st.wake_at_ts = 0.0
        st.pending_action = {}
        await self._set(key, st)
        if send_cb:
            await send_cb(f"🛑 灵界升级已停止：{reason}，连续失败 {st.failure_count}/{self.max_failures}。")

    async def _pause_for_unknown_result(self, key: str, st: LinjieState, send_cb, reason: str) -> None:
        st.phase = "PAUSED"
        st.next_action_ts = 0.0
        st.wake_at_ts = 0.0
        st.pending_action = {}
        await self._set(key, st)
        if send_cb:
            await send_cb(f"⚠️ 灵界升级已暂停：{reason}，为避免重复执行不会自动重发；确认官方状态后请重新开启或刷新规划。")
