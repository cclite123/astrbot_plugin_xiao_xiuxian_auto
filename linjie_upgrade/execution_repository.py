"""灵界资源动作状态持久化。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path

try:
    from ..file_read_cache import FileReadCache
except ImportError:  # 兼容项目根目录下的独立单元测试导入
    from file_read_cache import FileReadCache

from .execution_model import LinjieExecutionState


def _safe_state_filename(account: str, group: str) -> str:
    # 空格/斜杠等字符统一折叠成“_”会把 (a_b, c) 与 (a, b_c) 映射到同一文件，
    # 也会让含路径分隔符的 ID 逃逸出 root；追加账号+群组合的短哈希消除键冲突
    # 并杜绝路径穿越。
    safe = (
        f"{re.sub(r'[^0-9A-Za-z._-]+', '_', account)}_"
        f"{re.sub(r'[^0-9A-Za-z._-]+', '_', group)}"
    )
    digest = hashlib.sha256(f"{account}\x00{group}".encode("utf-8")).hexdigest()[:10]
    return f"{safe}_{digest}.json"


class LinjieExecutionRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._file_cache = FileReadCache()

    def _path(self, account_id: str, group_id: str) -> Path:
        account = str(account_id).strip()
        group = str(group_id).strip()
        if not account or not group:
            raise ValueError("account_id 和 group_id 不能为空")
        return self.root / _safe_state_filename(account, group)

    def load(self, account_id: str, group_id: str) -> LinjieExecutionState:
        with self._lock:
            path = self._path(account_id, group_id)
            try:
                text = self._file_cache.read(path)
                if text is None:
                    return LinjieExecutionState(str(account_id), str(group_id))
                return LinjieExecutionState.from_dict(
                    json.loads(text), account_id=str(account_id), group_id=str(group_id)
                )
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"灵界资源动作状态文件格式错误或无法读取：{path}") from exc

    def save(self, state: LinjieExecutionState) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(state.account_id, state.group_id)
            # 每次保存使用唯一临时文件名，避免并发保存写坏同一个 .tmp；
            # os.replace 保证目标文件要么是旧内容要么是新内容，不会出现半截 JSON。
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self._file_cache.invalidate(path)

    def list_states(self) -> list[LinjieExecutionState]:
        states: list[LinjieExecutionState] = []
        with self._lock:
            if not self.root.exists():
                return states
            for path in self.root.glob("*.json"):
                try:
                    text = self._file_cache.read(path)
                    if text is None:
                        continue
                    data = json.loads(text)
                    states.append(LinjieExecutionState.from_dict(
                        data, account_id=data["account_id"], group_id=data["group_id"]
                    ))
                except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError, RuntimeError):
                    continue
        return states
