"""Persistent safety guard for commands that may cause irreversible side effects.

The guard deliberately knows nothing about game reply formats.  Feature modules own
their protocol and use this small interface to make one promise: once a command may
have reached the official bot, an absent/ambiguous reply is never treated as an
invitation to send it again.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    from .storage import JsonStore
except ImportError:  # pragma: no cover - standalone compatibility
    from storage import JsonStore


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    request_id: Optional[str] = None
    reason: str = ""


@dataclass
class GuardRecord:
    module: str
    action: str
    command: str
    request_id: str
    status: str = "pending"
    created_at: float = 0.0
    updated_at: float = 0.0
    result: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> Optional["GuardRecord"]:
        if not isinstance(value, dict):
            return None
        try:
            fields = cls.__dataclass_fields__
            defaults = cls(module="", action="", command="", request_id="")
            return cls(**{name: value.get(name, getattr(defaults, name)) for name in fields})
        except (TypeError, ValueError):
            return None


class SideEffectGuard:
    """Serialize risky actions per account/group and persist ambiguous outcomes."""

    ACTIVE_STATUSES = frozenset({"pending", "unknown"})

    def __init__(self, store: JsonStore, *, logger=None):
        self.store = store
        self.log = logger
        self._request_modules: Dict[tuple[str, str], str] = {}

    @staticmethod
    def _store_key(key: str, module: str) -> str:
        return f"automation_safety:{key}:{module}"

    async def get(self, key: str, module: str) -> Optional[GuardRecord]:
        return GuardRecord.from_dict(await self.store.get(self._store_key(key, module)))

    async def begin(self, key: str, module: str, action: str, command: str) -> GuardDecision:
        existing = await self.get(key, module)
        if existing is not None and existing.status in self.ACTIVE_STATUSES:
            label = "结果未知" if existing.status == "unknown" else "等待回执"
            return GuardDecision(False, existing.request_id, f"{module}仍在{label}：{existing.action}")
        now = time.time()
        record = GuardRecord(
            module=str(module),
            action=str(action),
            command=str(command),
            request_id=uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
        )
        await self.store.set(self._store_key(key, module), record.to_dict())
        self._request_modules[(str(key), record.request_id)] = str(module)
        return GuardDecision(True, record.request_id)

    async def confirm(self, key: str, *args, result: str = "success") -> bool:
        if len(args) == 3:
            module, request_id, result = args
        elif len(args) == 2:
            request_id, result = args
            module = self._request_modules.get((str(key), str(request_id)), "")
        elif len(args) == 1:
            request_id = args[0]
            module = self._request_modules.get((str(key), str(request_id)), "")
        else:
            return False
        if not module or request_id is None:
            return False
        record = await self.get(key, module)
        if record is None or record.request_id != str(request_id):
            return False
        record.status = "confirmed"
        record.result = str(result or "success")
        record.reason = ""
        record.updated_at = time.time()
        await self.store.set(self._store_key(key, module), record.to_dict())
        return True

    async def pause_unknown(self, key: str, *args, reason: str = "") -> bool:
        if len(args) == 3:
            module, request_id, reason = args
        elif len(args) == 2:
            request_id, reason = args
            module = self._request_modules.get((str(key), str(request_id)), "")
        elif len(args) == 1:
            request_id = args[0]
            module = self._request_modules.get((str(key), str(request_id)), "")
        else:
            return False
        if not module or request_id is None:
            return False
        record = await self.get(key, module)
        if record is None or record.request_id != str(request_id):
            return False
        record.status = "unknown"
        record.reason = str(reason or "未收到明确回执")
        record.updated_at = time.time()
        await self.store.set(self._store_key(key, module), record.to_dict())
        return True

    async def cancel_unsent(self, key: str, module: str, request_id: str, reason: str = "") -> bool:
        """Cancel only when the caller knows the command never entered the send path."""
        record = await self.get(key, module)
        if record is None or record.request_id != str(request_id) or record.status != "pending":
            return False
        record.status = "cancelled"
        record.reason = str(reason)
        record.updated_at = time.time()
        await self.store.set(self._store_key(key, module), record.to_dict())
        return True

    async def reset_module(self, key: str, module: str) -> None:
        record = GuardRecord(
            module=str(module), action="reset", command="", request_id=uuid.uuid4().hex,
            status="reset", created_at=time.time(), updated_at=time.time(), result="manual_reset",
        )
        await self.store.set(self._store_key(key, module), record.to_dict())

    async def status(self, key: str, module: str | None = None) -> Dict[str, Any]:
        if not module:
            return {"status": "unknown", "reason": "请指定模块"}
        record = await self.get(key, module)
        return record.to_dict() if record else {"module": module, "status": "idle"}
