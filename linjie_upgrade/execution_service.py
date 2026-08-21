"""逐次执行组合 ROI 路线；未知结果不重发。"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from .execution_model import LinjieExecutionCommand, LinjieExecutionResult, LinjieExecutionState
from .execution_repository import LinjieExecutionRepository
from .model import PLAN_ALGORITHM_VERSION, LinjieCandidate


class LinjieExecutionService:
    def __init__(self, repository: LinjieExecutionRepository, *, response_timeout_seconds: int = 10) -> None:
        self.repository = repository
        self.response_timeout_seconds = response_timeout_seconds

    def start_round(
        self,
        account_id: str,
        group_id: str,
        candidates: tuple[LinjieCandidate, ...],
        balance: int,
        query_round: int = 1,
        *,
        now: datetime,
    ) -> LinjieExecutionState:
        state = self.repository.load(account_id, group_id)
        if state.status in {"pending", "sending", "waiting"}:
            return state
        state.remaining_candidates = [asdict(candidate) for candidate in candidates]
        self._set_route_target(state, candidates)
        state.local_balance = balance
        state.query_round = query_round
        state.confirmed_at = now.isoformat()
        state.request_id = None
        state.sent_at = None
        state.last_error = None
        state.started_at = now.isoformat()
        state.completed_at = None
        state.updated_at = now.isoformat()
        self._prepare_next(state)
        self.repository.save(state)
        return state

    def start(self, account_id: str, group_id: str, candidate: LinjieCandidate, *, now: datetime) -> LinjieExecutionState:
        return self.start_round(account_id, group_id, (candidate,), candidate.cost, now=now)

    def mark_refreshing(self, account_id: str, group_id: str, *, now: datetime) -> None:
        state = self.repository.load(account_id, group_id)
        state.status = "refreshing"
        self._clear_current_action(state)
        state.remaining_candidates = []
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def resume_with_balance(self, account_id: str, group_id: str, balance: int, *, now: datetime) -> bool:
        state = self.repository.load(account_id, group_id)
        state.local_balance = balance
        state.updated_at = now.isoformat()
        self._prepare_next(state)
        self.repository.save(state)
        return state.status == "pending"

    def cancel(
        self, account_id: str, group_id: str, *, now: datetime, clear_authorization: bool = True
    ) -> LinjieExecutionState:
        state = self.repository.load(account_id, group_id)
        state.status = "idle"
        state.request_id = None
        state.sent_at = None
        state.remaining_candidates = []
        state.local_balance = None
        state.query_round = 0
        state.plan_snapshot_at = None
        state.planned_at = None
        state.confirmed_at = None
        state.route_name = None
        state.route_target_count = None
        state.route_target_level = None
        if clear_authorization:
            state.strategy_confirmed = False
            state.automation_enabled = False
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return state

    def get_state(self, account_id: str, group_id: str) -> LinjieExecutionState:
        return self.repository.load(account_id, group_id)

    def has_current_plan(self, account_id: str, group_id: str) -> bool:
        state = self.repository.load(account_id, group_id)
        return state.plan_algorithm_version == PLAN_ALGORITHM_VERSION

    def locked_route(self, account_id: str, group_id: str) -> tuple[str, int, int] | None:
        state = self.repository.load(account_id, group_id)
        if (
            not state.strategy_confirmed
            or not state.route_name
            or state.route_target_count is None
            or state.route_target_level is None
        ):
            return None
        return state.route_name, state.route_target_count, state.route_target_level

    def planning_strategy(self, account_id: str, group_id: str) -> str:
        return self.repository.load(account_id, group_id).planning_strategy

    def set_planning_strategy(
        self, account_id: str, group_id: str, strategy: str, *, now: datetime
    ) -> LinjieExecutionState:
        if strategy not in {"roi", "time"}:
            raise ValueError("灵界规划策略无效")
        state = self.repository.load(account_id, group_id)
        state.planning_strategy = strategy
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return state

    def balance_reaches_scheduled_step(
        self, account_id: str, group_id: str, balance: int
    ) -> bool:
        state = self.repository.load(account_id, group_id)
        if (
            not state.automation_enabled
            or not state.strategy_confirmed
            or state.status != "scheduled"
            or state.plan_algorithm_version != PLAN_ALGORITHM_VERSION
            or not state.remaining_candidates
        ):
            return False
        return LinjieCandidate(**state.remaining_candidates[0]).cost <= balance

    def invalidate_outdated_plans(self, *, now: datetime) -> list[tuple[str, str]]:
        """作废未发送的旧算法计划，并返回需要重新查询的已授权账号。"""
        refresh: list[tuple[str, str]] = []
        for state in self.repository.list_states():
            if state.plan_algorithm_version == PLAN_ALGORITHM_VERSION:
                continue
            if state.status not in {"awaiting_confirmation", "scheduled", "pending", "exhausted"}:
                continue
            should_refresh = state.automation_enabled and state.strategy_confirmed
            state.remaining_candidates = []
            state.request_id = None
            state.sent_at = None
            state.kind = None
            state.name = None
            state.cost = None
            state.command = None
            state.note = None
            state.plan_snapshot_at = None
            state.planned_at = None
            state.route_name = None
            state.route_target_count = None
            state.route_target_level = None
            state.status = "refreshing" if should_refresh else "idle"
            state.updated_at = now.isoformat()
            self.repository.save(state)
            if should_refresh:
                refresh.append((state.account_id, state.group_id))
        return refresh

    def set_automation_enabled(
        self, account_id: str, group_id: str, enabled: bool, *, now: datetime
    ) -> LinjieExecutionState:
        state = self.repository.load(account_id, group_id)
        state.automation_enabled = enabled
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return state

    def plan_candidates(self, account_id: str, group_id: str) -> tuple[LinjieCandidate, ...]:
        state = self.repository.load(account_id, group_id)
        return tuple(LinjieCandidate(**item) for item in state.remaining_candidates)

    def can_send(self, account_id: str, group_id: str, request_id: str) -> bool:
        state = self.repository.load(account_id, group_id)
        return state.status == "sending" and state.request_id == request_id

    def reconcile(self, *, now: datetime, allowed: Callable[[str, str], bool] | None = None) -> list[LinjieExecutionCommand]:
        commands: list[LinjieExecutionCommand] = []
        for state in self.repository.list_states():
            if allowed is not None and not allowed(state.account_id, state.group_id):
                continue
            if state.status == "scheduled" and self._scheduled_candidate_is_due(state, now):
                self._activate_scheduled_candidate(state, now)
                self.repository.save(state)
            if state.status == "pending" and state.command is not None:
                state.status = "sending"
                state.request_id = uuid.uuid4().hex
                state.sent_at = None
                state.updated_at = now.isoformat()
                self.repository.save(state)
                commands.append(LinjieExecutionCommand(
                    state.account_id, state.group_id, state.command, str(state.kind), state.request_id
                ))
            elif state.status == "waiting" and state.sent_at is not None:
                elapsed = (now - datetime.fromisoformat(state.sent_at)).total_seconds()
                if elapsed >= self.response_timeout_seconds:
                    self._pause(state, "资源动作等待回执超时，结果未知", now)
        return commands

    def mark_sent(self, account_id: str, group_id: str, request_id: str, *, now: datetime) -> None:
        state = self.repository.load(account_id, group_id)
        if state.status != "sending" or state.request_id != request_id:
            return
        state.status = "waiting"
        state.sent_at = now.isoformat()
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def mark_send_failed(
        self, account_id: str, group_id: str, request_id: str, *, reason: str, now: datetime
    ) -> None:
        state = self.repository.load(account_id, group_id)
        if state.status != "sending" or state.request_id != request_id:
            return
        self._pause(state, f"资源动作发送失败：{reason or '发送通道未接受消息'}", now)

    def on_reply(self, account_id: str, group_id: str, text: str, *, now: datetime, message_id: str | None) -> LinjieExecutionResult:
        state = self.repository.load(account_id, group_id)
        if state.status not in {"sending", "waiting"} or state.kind is None or state.name is None:
            return LinjieExecutionResult(False)
        reply_id = str(message_id or "").strip()
        if reply_id and reply_id in state.processed_reply_ids:
            return LinjieExecutionResult(True)
        value = str(text or "")
        success = self._is_success(state.kind, state.name, value)
        shortage = "灵矿石不足" in value
        if not success and not shortage:
            return LinjieExecutionResult(False)
        if reply_id:
            state.processed_reply_ids = (state.processed_reply_ids + [reply_id])[-50:]
        if shortage:
            state.status = "refreshing"
            state.last_error = "执行时官方返回灵矿石不足，正在刷新官方数据"
            self._clear_current_action(state)
            state.remaining_candidates = []
            state.updated_at = now.isoformat()
            self.repository.save(state)
            return LinjieExecutionResult(
                True, completed=True, notice="ℹ️ 灵界动作返回灵矿石不足，正在刷新四页并重新计算当前最优项。"
            )
        if state.local_balance is not None and state.cost is not None:
            state.local_balance -= state.cost
        state.request_id = None
        state.sent_at = None
        state.completed_at = now.isoformat()
        state.updated_at = now.isoformat()
        state.status = "refreshing"
        self._clear_current_action(state)
        state.remaining_candidates = []
        self.repository.save(state)
        return LinjieExecutionResult(True, completed=True, round_exhausted=True)

    def recover_after_restart(self, *, now: datetime) -> int:
        count = 0
        for state in self.repository.list_states():
            if state.status in {"sending", "waiting"}:
                self._pause(state, "插件重启时资源动作结果未知", now)
                count += 1
        return count

    def drain_notifications(self) -> list[tuple[str, str, str]]:
        notices: list[tuple[str, str, str]] = []
        for state in self.repository.list_states():
            if not state.notifications:
                continue
            notices.extend((state.account_id, state.group_id, text) for text in state.notifications)
            state.notifications = []
            self.repository.save(state)
        return notices

    def _pause(
        self,
        state: LinjieExecutionState,
        reason: str,
        now: datetime,
        *,
        notify: bool = True,
    ) -> None:
        state.status = "paused"
        state.request_id = None
        state.sent_at = None
        state.last_error = reason
        state.updated_at = now.isoformat()
        if notify:
            state.notifications.append(f"⚠️ {reason}，自动升级已暂停且不会自动重发。")
        self.repository.save(state)

    @staticmethod
    def _prepare_next(state: LinjieExecutionState) -> None:
        balance = state.local_balance
        if balance is None:
            state.status = "exhausted"
            return
        if state.remaining_candidates:
            candidate = LinjieCandidate(**state.remaining_candidates[0])
            if candidate.cost <= balance:
                state.status = "pending"
                state.kind = candidate.kind
                state.name = candidate.name
                state.cost = candidate.cost
                state.command = candidate.command
                state.note = candidate.note
                return
            state.status = "scheduled"
            state.kind = None
            state.name = None
            state.cost = None
            state.command = None
            state.note = candidate.note
            return
        state.status = "exhausted"
        state.kind = None
        state.name = None
        state.cost = None
        state.command = None
        state.note = None

    @staticmethod
    def _scheduled_candidate_is_due(state: LinjieExecutionState, now: datetime) -> bool:
        if not state.remaining_candidates or state.confirmed_at is None:
            return False
        candidate = LinjieCandidate(**state.remaining_candidates[0])
        elapsed = (now - datetime.fromisoformat(state.confirmed_at)).total_seconds()
        return elapsed >= candidate.available_after_seconds

    @staticmethod
    def _activate_scheduled_candidate(state: LinjieExecutionState, now: datetime) -> None:
        candidate = LinjieCandidate(**state.remaining_candidates[0])
        state.status = "pending"
        state.kind = candidate.kind
        state.name = candidate.name
        state.cost = candidate.cost
        state.command = candidate.command
        state.note = candidate.note
        state.updated_at = now.isoformat()

    @staticmethod
    def _is_success(kind: str, name: str, text: str) -> bool:
        if kind == "building":
            return "建造成功" in text and name in text
        if kind == "upgrade":
            return "升级成功" in text and name in text
        if kind == "worker":
            return "招募成功" in text and name in text
        if kind == "worker_rank":
            return "杂役技艺提升成功" in text and "杂役技艺LV" in text
        if kind == "skill":
            return "修习成功" in text and "技艺等级提升1" in text
        return False

    def prepare_plan(
        self, account_id: str, group_id: str, candidates: tuple[LinjieCandidate, ...],
        balance: int, snapshot_at: str, *, now: datetime,
    ) -> LinjieExecutionState:
        state = self.repository.load(account_id, group_id)
        state.status = (
            "refreshing" if state.strategy_confirmed and candidates else
            "awaiting_confirmation" if candidates else "exhausted"
        )
        state.remaining_candidates = [asdict(candidate) for candidate in candidates[:20]]
        self._set_route_target(state, candidates)
        state.local_balance = balance
        state.kind = None
        state.name = None
        state.cost = None
        state.command = None
        state.note = None
        state.request_id = None
        state.sent_at = None
        state.plan_snapshot_at = snapshot_at
        state.plan_algorithm_version = PLAN_ALGORITHM_VERSION
        state.planned_at = now.isoformat()
        if not state.strategy_confirmed:
            state.confirmed_at = None
            state.started_at = None
        state.completed_at = None
        state.last_error = None
        state.updated_at = now.isoformat()
        if state.strategy_confirmed and candidates:
            state.confirmed_at = now.isoformat()
            self._prepare_next(state)
        self.repository.save(state)
        return state

    def confirm_plan(self, account_id: str, group_id: str, *, now: datetime) -> LinjieExecutionState:
        state = self.repository.load(account_id, group_id)
        if state.plan_algorithm_version != PLAN_ALGORITHM_VERSION:
            raise ValueError("灵界规划算法已更新，请先刷新规划")
        if state.status != "awaiting_confirmation" or not state.remaining_candidates:
            raise ValueError("没有等待确认的灵界规划，请先发送“一键生成灵界规划”")
        state.confirmed_at = now.isoformat()
        state.strategy_confirmed = True
        state.started_at = now.isoformat()
        state.updated_at = now.isoformat()
        self._prepare_next(state)
        self.repository.save(state)
        return state

    @staticmethod
    def _clear_current_action(state: LinjieExecutionState) -> None:
        state.kind = None
        state.name = None
        state.cost = None
        state.command = None
        state.note = None
        state.request_id = None
        state.sent_at = None

    @staticmethod
    def _set_route_target(
        state: LinjieExecutionState, candidates: tuple[LinjieCandidate, ...]
    ) -> None:
        candidate = candidates[0] if candidates else None
        if (
            candidate is None
            or not candidate.route_name
            or candidate.route_target_count is None
            or candidate.route_target_level is None
        ):
            state.route_name = None
            state.route_target_count = None
            state.route_target_level = None
            return
        state.route_name = candidate.route_name
        state.route_target_count = candidate.route_target_count
        state.route_target_level = candidate.route_target_level
