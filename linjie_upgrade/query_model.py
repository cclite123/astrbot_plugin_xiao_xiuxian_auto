"""灵界四页查询任务模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .model import PAGE_KINDS


def _validate_timestamp(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"灵界查询{field}无效")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"灵界查询{field}无效") from exc


@dataclass(frozen=True)
class LinjieQueryPolicy:
    response_timeout_seconds: int = 10
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.response_timeout_seconds <= 0 or self.max_retries < 0:
            raise ValueError("灵界查询策略无效")


@dataclass
class LinjieQueryState:
    account_id: str
    group_id: str
    status: str = "idle"
    current_index: int = 0
    pages: dict[str, str] = field(default_factory=dict)
    pending_action: str | None = None
    last_attempt_at: str | None = None
    retry_count: int = 0
    attempt_count: int = 0
    request_id: str | None = None
    processed_reply_ids: list[str] = field(default_factory=list)
    last_reply_at: str | None = None
    last_error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, *, account_id: str, group_id: str) -> "LinjieQueryState":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("灵界查询任务字段无效")
        state = cls(**data)
        if state.account_id != account_id or state.group_id != group_id:
            raise ValueError("灵界查询任务账号或群不一致")
        if not isinstance(state.status, str):
            raise ValueError("灵界查询任务阶段无效")
        if state.status not in {"idle", "collecting", "completed", "failed"}:
            raise ValueError("灵界查询任务阶段无效")
        if not isinstance(state.current_index, int) or not 0 <= state.current_index <= len(PAGE_KINDS):
            raise ValueError("灵界查询任务索引无效")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (state.retry_count, state.attempt_count)
        ):
            raise ValueError("灵界查询任务次数无效")
        if not isinstance(state.pages, dict) or any(key not in PAGE_KINDS or not isinstance(value, str) for key, value in state.pages.items()):
            raise ValueError("灵界查询页面缓存无效")
        for field in ("last_attempt_at", "last_reply_at", "started_at", "completed_at", "updated_at"):
            _validate_timestamp(getattr(state, field), field)
        return state


@dataclass(frozen=True)
class LinjieQueryCommand:
    account_id: str
    group_id: str
    text: str
    action: str
    request_id: str
    attempt_number: int
    retry_number: int


@dataclass(frozen=True)
class LinjieQueryResult:
    handled: bool
    commands: tuple[LinjieQueryCommand, ...] = ()
    completed: bool = False
