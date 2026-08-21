"""灵界资源动作执行模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def _validate_timestamp(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"灵界动作{field}无效")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"灵界动作{field}无效") from exc


@dataclass
class LinjieExecutionState:
    account_id: str
    group_id: str
    status: str = "idle"
    kind: str | None = None
    name: str | None = None
    cost: int | None = None
    command: str | None = None
    note: str | None = None
    request_id: str | None = None
    sent_at: str | None = None
    processed_reply_ids: list[str] = field(default_factory=list)
    last_error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    notifications: list[str] = field(default_factory=list)
    remaining_candidates: list[dict[str, Any]] = field(default_factory=list)
    local_balance: int | None = None
    query_round: int = 0
    plan_snapshot_at: str | None = None
    planned_at: str | None = None
    confirmed_at: str | None = None
    strategy_confirmed: bool = False
    automation_enabled: bool = False
    plan_algorithm_version: int = 0
    planning_strategy: str = "roi"
    route_name: str | None = None
    route_target_count: int | None = None
    route_target_level: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, *, account_id: str, group_id: str) -> "LinjieExecutionState":
        if not isinstance(data, dict):
            raise ValueError("灵界动作状态字段无效")
        values = dict(data)
        values.setdefault("plan_algorithm_version", 0)
        values.setdefault("route_name", None)
        values.setdefault("route_target_count", None)
        values.setdefault("route_target_level", None)
        values.setdefault("planning_strategy", "roi")
        # 老文件按字段引入代际补默认；策略/自动化授权字段缺失时由已确认时间戳推断。
        plan_fields = {"plan_snapshot_at", "planned_at", "confirmed_at", "strategy_confirmed", "automation_enabled"}
        execution_fields = {"remaining_candidates", "local_balance", "query_round"} | plan_fields
        missing = set(cls.__dataclass_fields__) - set(values)
        if missing == execution_fields:
            values.update(remaining_candidates=[], local_balance=None, query_round=0, plan_snapshot_at=None, planned_at=None, confirmed_at=None, strategy_confirmed=False, automation_enabled=False)
        elif missing == plan_fields:
            values.update(plan_snapshot_at=None, planned_at=None, confirmed_at=None, strategy_confirmed=False, automation_enabled=False)
        elif missing == {"strategy_confirmed", "automation_enabled"}:
            confirmed = bool(values.get("confirmed_at"))
            values.update(strategy_confirmed=confirmed, automation_enabled=confirmed)
        elif missing == {"automation_enabled"}:
            values.update(automation_enabled=bool(values.get("strategy_confirmed")))
        elif missing:
            raise ValueError("灵界动作状态字段无效")
        state = cls(**values)
        if state.account_id != account_id or state.group_id != group_id:
            raise ValueError("灵界动作状态账号或群不一致")
        if not isinstance(state.status, str):
            raise ValueError("灵界动作状态无效")
        if state.status not in {"idle", "awaiting_confirmation", "scheduled", "pending", "sending", "waiting", "completed", "exhausted", "refreshing", "paused"}:
            raise ValueError("灵界动作状态无效")
        if state.kind is not None and (
            not isinstance(state.kind, str)
            or state.kind not in {"building", "upgrade", "worker", "worker_rank", "skill"}
        ):
            raise ValueError("灵界动作类型无效")
        if isinstance(state.query_round, bool) or not isinstance(state.query_round, int) or state.query_round not in {0, 1, 2}:
            raise ValueError("灵界查询轮次无效")
        if isinstance(state.plan_algorithm_version, bool) or not isinstance(state.plan_algorithm_version, int) or state.plan_algorithm_version < 0:
            raise ValueError("灵界规划算法版本无效")
        if not isinstance(state.planning_strategy, str):
            raise ValueError("灵界规划策略无效")
        if state.planning_strategy not in {"roi", "time"}:
            raise ValueError("灵界规划策略无效")
        if not isinstance(state.remaining_candidates, list) or any(
            not isinstance(item, dict) for item in state.remaining_candidates
        ):
            raise ValueError("灵界候选计划无效")
        if not isinstance(state.notifications, list) or any(
            not isinstance(item, str) for item in state.notifications
        ):
            raise ValueError("灵界动作提醒无效")
        if not isinstance(state.processed_reply_ids, list) or any(
            not isinstance(item, str) for item in state.processed_reply_ids
        ):
            raise ValueError("灵界动作回执记录无效")
        for field in (
            "sent_at", "started_at", "completed_at", "updated_at",
            "planned_at", "confirmed_at",
        ):
            _validate_timestamp(getattr(state, field), field)
        route_values = (state.route_name, state.route_target_count, state.route_target_level)
        if any(value is not None for value in route_values) and not (
            state.route_name
            and isinstance(state.route_target_count, int)
            and state.route_target_count > 0
            and isinstance(state.route_target_level, int)
            and state.route_target_level > 0
            and state.route_target_level <= 6
            and state.route_target_count == state.route_target_level * 10
        ):
            raise ValueError("灵界组合升级路线无效")
        return state


@dataclass(frozen=True)
class LinjieExecutionCommand:
    account_id: str
    group_id: str
    text: str
    action: str
    request_id: str


@dataclass(frozen=True)
class LinjieExecutionResult:
    handled: bool
    completed: bool = False
    paused: bool = False
    notice: str | None = None
    round_exhausted: bool = False
