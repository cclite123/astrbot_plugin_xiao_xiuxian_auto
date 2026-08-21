"""灵界我的信息保活状态机。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Callable, Iterable

from .keepalive_model import LinjieKeepaliveCommand, LinjieKeepalivePolicy, LinjieKeepaliveState
from .keepalive_repository import LinjieKeepaliveRepository
from .parser import LinjiePageParser


class LinjieKeepaliveService:
    def __init__(self, repository: LinjieKeepaliveRepository, policy: LinjieKeepalivePolicy) -> None:
        self.repository = repository
        self.policy = policy
        self.parser = LinjiePageParser()

    def get_state(self, account_id: str, group_id: str) -> LinjieKeepaliveState:
        return self.repository.load(account_id, group_id)

    def mark_sent(self, account_id: str, group_id: str, request_id: str, *, now: datetime) -> None:
        state = self.repository.load(account_id, group_id)
        if state.status != "pending" or state.request_id != request_id:
            return
        state.sent_at = now.isoformat()
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def mark_send_failed(
        self, account_id: str, group_id: str, request_id: str, *, reason: str, now: datetime
    ) -> None:
        state = self.repository.load(account_id, group_id)
        if state.status != "pending" or state.request_id != request_id:
            return
        state.status = "idle"
        state.request_id = None
        state.sent_at = None
        state.next_run_at = (now + timedelta(seconds=self.policy.interval_seconds)).isoformat()
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def on_reply(self, account_id: str, group_id: str, text: str, *, now: datetime, message_id: str | None = None) -> bool:
        state = self.repository.load(account_id, group_id)
        try:
            self.parser.parse_profile(str(text or ""))
        except ValueError:
            return False
        handled = state.status == "pending"
        self._record_success(state, now)
        self.repository.save(state)
        return handled

    def mark_success(self, account_id: str, group_id: str, *, now: datetime) -> None:
        state = self.repository.load(account_id, group_id)
        self._record_success(state, now)
        self.repository.save(state)

    def cancel(self, account_id: str, group_id: str, *, now: datetime) -> None:
        state = self.repository.load(account_id, group_id)
        state.status = "idle"
        state.request_id = None
        state.sent_at = None
        state.next_run_at = None
        state.retry_count = 0
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def _record_success(self, state: LinjieKeepaliveState, now: datetime) -> None:
        state.status = "idle"
        state.request_id = None
        state.sent_at = None
        state.last_success_at = now.isoformat()
        state.next_run_at = None
        state.retry_count = 0
        state.updated_at = now.isoformat()

    def reconcile(
        self,
        *,
        now: datetime,
        accounts: Iterable[tuple[str, str]],
        allowed: Callable[[str, str], bool] | None = None,
        enabled: Callable[[str, str], bool] | None = None,
        query_active: Callable[[str, str], bool] | None = None,
    ) -> list[LinjieKeepaliveCommand]:
        commands: list[LinjieKeepaliveCommand] = []
        for account_id, group_id in accounts:
            try:
                commands.extend(
                    self._reconcile_account(
                        account_id,
                        group_id,
                        now=now,
                        allowed=allowed,
                        enabled=enabled,
                        query_active=query_active,
                    )
                )
            except RuntimeError:
                # 状态文件损坏：跳过该账号，不阻断其他账号与模块的调度
                continue
        return commands

    def _reconcile_account(
        self,
        account_id: str,
        group_id: str,
        *,
        now: datetime,
        allowed: Callable[[str, str], bool] | None,
        enabled: Callable[[str, str], bool] | None,
        query_active: Callable[[str, str], bool] | None,
    ) -> list[LinjieKeepaliveCommand]:
        if enabled is not None and not enabled(account_id, group_id):
            return []
        if allowed is not None and not allowed(account_id, group_id):
            return []
        if query_active is not None and query_active(account_id, group_id):
            return []
        state = self.repository.load(account_id, group_id)
        if state.status == "pending":
            if state.sent_at is None:
                return [self._command(state, now)]
            elapsed = (now - datetime.fromisoformat(state.sent_at)).total_seconds()
            if elapsed < self.policy.response_timeout_seconds:
                return []
            if state.retry_count < self.policy.max_retries:
                state.retry_count += 1
                state.attempt_count += 1
                state.request_id = uuid.uuid4().hex
                state.sent_at = None
                state.updated_at = now.isoformat()
                self.repository.save(state)
                return [self._command(state, now)]
            state.status = "idle"
            state.request_id = None
            state.sent_at = None
            state.next_run_at = (now + timedelta(seconds=self.policy.interval_seconds)).isoformat()
            state.updated_at = now.isoformat()
            self.repository.save(state)
            return []
        if state.next_run_at is not None:
            if now < datetime.fromisoformat(state.next_run_at):
                return []
            state.next_run_at = None
        if state.last_success_at is not None:
            elapsed = (now - datetime.fromisoformat(state.last_success_at)).total_seconds()
            if elapsed < self.policy.interval_seconds:
                return []
        state.status = "pending"
        state.request_id = uuid.uuid4().hex
        state.sent_at = None
        state.retry_count = 0
        state.attempt_count += 1
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return [self._command(state, now)]

    @staticmethod
    def _command(state: LinjieKeepaliveState, now: datetime) -> LinjieKeepaliveCommand:
        return LinjieKeepaliveCommand(state.account_id, state.group_id, "灵界我的信息", "keepalive", str(state.request_id))
