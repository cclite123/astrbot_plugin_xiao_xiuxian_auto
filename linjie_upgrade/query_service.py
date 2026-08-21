"""持久化执行灵界四页查询。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from .model import PAGE_KINDS, QUERY_COMMANDS
from .parser import LinjiePageParser
from .query_model import LinjieQueryCommand, LinjieQueryPolicy, LinjieQueryResult, LinjieQueryState
from .query_repository import LinjieQueryRepository
from .repository import LinjieSnapshotRepository


class LinjieQueryService:
    def __init__(self, repository: LinjieQueryRepository, snapshots: LinjieSnapshotRepository, policy: LinjieQueryPolicy) -> None:
        self.repository = repository
        self.snapshots = snapshots
        self.policy = policy
        self.parser = LinjiePageParser()
        self.profile_success_observer: Callable[[str, str, datetime], None] | None = None

    def start(self, account_id: str, group_id: str, *, now: datetime) -> LinjieQueryState:
        state = LinjieQueryState(
            account_id=str(account_id).strip(), group_id=str(group_id).strip(), status="collecting",
            started_at=now.isoformat(), updated_at=now.isoformat(),
        )
        if not state.account_id or not state.group_id:
            raise ValueError("account_id 和 group_id 不能为空")
        self.repository.save(state)
        return state

    def cancel(self, account_id: str, group_id: str, *, now: datetime) -> LinjieQueryState:
        state = self.repository.load(account_id, group_id)
        state.status = "idle"
        state.pending_action = None
        state.request_id = None
        state.last_attempt_at = None
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return state

    def get_state(self, account_id: str, group_id: str) -> LinjieQueryState:
        return self.repository.load(account_id, group_id)

    def mark_sent(self, account_id: str, group_id: str, request_id: str, *, now: datetime) -> None:
        state = self.repository.find(account_id, group_id)
        if state is None or state.status != "collecting" or state.request_id != request_id:
            return
        state.last_attempt_at = now.isoformat()
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def mark_send_failed(
        self, account_id: str, group_id: str, request_id: str, *, reason: str, now: datetime
    ) -> None:
        state = self.repository.find(account_id, group_id)
        if state is None or state.status != "collecting" or state.request_id != request_id:
            return
        state.status = "failed"
        state.pending_action = None
        state.request_id = None
        state.last_attempt_at = None
        state.last_error = f"灵界查询发送失败：{reason or '发送通道未接受消息'}"
        state.completed_at = now.isoformat()
        state.updated_at = now.isoformat()
        self.repository.save(state)

    def reconcile(self, *, now: datetime, allowed: Callable[[str, str], bool] | None = None) -> list[LinjieQueryCommand]:
        commands: list[LinjieQueryCommand] = []
        for state in self.repository.list_states():
            if state.status != "collecting":
                continue
            if allowed is not None and not allowed(state.account_id, state.group_id):
                continue
            if state.last_attempt_at is None:
                if state.pending_action == "query" and state.request_id:
                    commands.append(self._pending_command(state))
                    continue
                commands.append(self._attempt(state, now, retry=False))
                continue
            elapsed = (now - datetime.fromisoformat(state.last_attempt_at)).total_seconds()
            if elapsed < self.policy.response_timeout_seconds:
                continue
            if state.retry_count < self.policy.max_retries:
                commands.append(self._attempt(state, now, retry=True))
            else:
                state.status = "failed"
                state.pending_action = None
                state.request_id = None
                state.last_error = f"灵界查询等待超时，首次发送后已重试 {self.policy.max_retries} 次"
                state.completed_at = now.isoformat()
                state.updated_at = now.isoformat()
                self.repository.save(state)
        return commands

    def on_reply(self, account_id: str, group_id: str, text: str, *, now: datetime, message_id: str | None) -> LinjieQueryResult:
        state = self.repository.find(account_id, group_id)
        if state is None or state.status != "collecting" or state.pending_action != "query":
            return LinjieQueryResult(False)
        normalized_id = str(message_id or "").strip()
        if normalized_id and normalized_id in state.processed_reply_ids:
            return LinjieQueryResult(True)
        kind = PAGE_KINDS[state.current_index]
        value = str(text or "")
        try:
            self._parse(kind, value)
        except ValueError:
            return LinjieQueryResult(False)
        state.pages[kind] = value
        if kind == "profile" and self.profile_success_observer is not None:
            self.profile_success_observer(state.account_id, state.group_id, now)
        if normalized_id:
            state.processed_reply_ids = (state.processed_reply_ids + [normalized_id])[-50:]
        state.current_index += 1
        state.last_reply_at = now.isoformat()
        state.last_error = None
        state.retry_count = 0
        state.pending_action = None
        state.request_id = None
        state.last_attempt_at = None
        state.updated_at = now.isoformat()
        if state.current_index < len(PAGE_KINDS):
            self._attempt(state, now, retry=False)
            return LinjieQueryResult(True)
        pages = {page_kind: self._parse(page_kind, state.pages[page_kind]) for page_kind in PAGE_KINDS}
        self.snapshots.replace_from_pages(state.account_id, state.group_id, pages, collected_at=now)
        state.status = "completed"
        state.completed_at = now.isoformat()
        self.repository.save(state)
        return LinjieQueryResult(True, completed=True)

    def _parse(self, kind: str, text: str):
        return getattr(self.parser, f"parse_{kind}")(text)

    def _attempt(self, state: LinjieQueryState, now: datetime, *, retry: bool) -> LinjieQueryCommand:
        if retry:
            state.retry_count += 1
        state.attempt_count += 1
        state.pending_action = "query"
        state.request_id = uuid.uuid4().hex
        state.last_attempt_at = None
        state.updated_at = now.isoformat()
        self.repository.save(state)
        return self._pending_command(state)

    @staticmethod
    def _pending_command(state: LinjieQueryState) -> LinjieQueryCommand:
        return LinjieQueryCommand(
            state.account_id, state.group_id, QUERY_COMMANDS[state.current_index],
            "query", str(state.request_id), state.attempt_count, state.retry_count,
        )
