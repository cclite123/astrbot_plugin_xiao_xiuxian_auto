"""灵界我的信息保活任务模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


def _validate_timestamp(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"灵界保活{field}无效")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"灵界保活{field}无效") from exc


@dataclass(frozen=True)
class LinjieKeepalivePolicy:
    interval_seconds: int = 5 * 3600 + 50 * 60
    response_timeout_seconds: int = 10
    max_retries: int = 3


@dataclass
class LinjieKeepaliveState:
    account_id: str
    group_id: str
    status: str = "idle"
    request_id: str | None = None
    sent_at: str | None = None
    last_success_at: str | None = None
    next_run_at: str | None = None
    retry_count: int = 0
    attempt_count: int = 0
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, *, account_id: str, group_id: str) -> "LinjieKeepaliveState":
        if not isinstance(data, dict):
            raise ValueError("灵界保活状态字段无效")
        fields = set(cls.__dataclass_fields__)
        legacy_fields = fields - {"next_run_at"}
        if set(data) == legacy_fields:
            data = {**data, "next_run_at": None}
        elif set(data) != fields:
            raise ValueError("灵界保活状态字段无效")
        state = cls(**data)
        if state.account_id != account_id or state.group_id != group_id:
            raise ValueError("灵界保活状态账号或群不一致")
        if not isinstance(state.status, str):
            raise ValueError("灵界保活状态无效")
        if state.status not in {"idle", "pending"}:
            raise ValueError("灵界保活状态无效")
        for field in ("sent_at", "last_success_at", "next_run_at", "updated_at"):
            _validate_timestamp(getattr(state, field), field)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (state.retry_count, state.attempt_count)
        ):
            raise ValueError("灵界保活次数无效")
        return state


@dataclass(frozen=True)
class LinjieKeepaliveCommand:
    account_id: str
    group_id: str
    text: str
    action: str
    request_id: str
