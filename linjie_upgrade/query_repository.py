"""每账号一个文件的灵界查询任务仓储。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from .query_model import LinjieQueryState

try:
    from ..file_read_cache import FileReadCache
except ImportError:  # 兼容项目根目录下的独立单元测试导入
    from file_read_cache import FileReadCache


class LinjieQueryRepository:
    SCHEMA_VERSION = 1

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._file_cache = FileReadCache()

    def path_for(self, account_id: str) -> Path:
        account = str(account_id).strip()
        if not account:
            raise ValueError("account_id 不能为空")
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", account)
        digest = hashlib.sha256(account.encode("utf-8")).hexdigest()[:10]
        return self.root / f"{safe}_{digest}.json"

    def find(self, account_id: str, group_id: str) -> LinjieQueryState | None:
        account, group = str(account_id).strip(), str(group_id).strip()
        path = self.path_for(account)
        if not path.exists():
            return None
        with self._lock:
            payload = self._read(path, account)
            raw = payload["groups"].get(group)
            return None if raw is None else self._state(raw, account, group, path)

    def load(self, account_id: str, group_id: str) -> LinjieQueryState:
        state = self.find(account_id, group_id)
        return state or LinjieQueryState(str(account_id).strip(), str(group_id).strip())

    def save(self, state: LinjieQueryState) -> None:
        with self._lock:
            path = self.path_for(state.account_id)
            payload = self._read(path, state.account_id) if path.exists() else self._empty(state.account_id)
            payload["groups"][state.group_id] = state.to_dict()
            self._write(path, payload)

    def list_states(self) -> Iterable[LinjieQueryState]:
        with self._lock:
            states: list[LinjieQueryState] = []
            for path in sorted(self.root.glob("*.json")):
                try:
                    payload = self._read(path, "")
                except RuntimeError:
                    # 状态文件损坏只影响该账号，跳过而不是中断整体调度
                    continue
                for group, raw in payload["groups"].items():
                    try:
                        states.append(self._state(raw, payload["account_id"], group, path))
                    except (TypeError, ValueError, RuntimeError):
                        continue
            return states

    def _state(self, raw: Any, account: str, group: str, path: Path) -> LinjieQueryState:
        try:
            return LinjieQueryState.from_dict(raw, account_id=account, group_id=group)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"灵界查询任务文件格式错误：{path}") from exc

    def _empty(self, account: str) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "account_id": account, "groups": {}}

    def _read(self, path: Path, account: str) -> dict[str, Any]:
        text = self._file_cache.read(path)
        if text is None:
            raise RuntimeError(f"无法读取灵界查询任务文件：{path}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"无法读取灵界查询任务文件：{path}") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "account_id", "groups"}
            or payload["schema_version"] != self.SCHEMA_VERSION
            or not isinstance(payload["account_id"], str)
            or not isinstance(payload["groups"], dict)
            or account and payload["account_id"] != account
        ):
            raise RuntimeError(f"灵界查询任务文件格式错误：{path}")
        return payload

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        self._file_cache.invalidate(path)
