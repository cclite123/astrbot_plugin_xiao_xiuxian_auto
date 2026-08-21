"""灵界资源动作状态持久化。"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from ..file_read_cache import FileReadCache
except ImportError:  # 兼容项目根目录下的独立单元测试导入
    from file_read_cache import FileReadCache

from .execution_model import LinjieExecutionState


class LinjieExecutionRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._file_cache = FileReadCache()

    def _path(self, account_id: str, group_id: str) -> Path:
        return self.root / f"{account_id}_{group_id}.json"

    def load(self, account_id: str, group_id: str) -> LinjieExecutionState:
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
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.account_id, state.group_id)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
        self._file_cache.invalidate(path)

    def list_states(self) -> list[LinjieExecutionState]:
        states: list[LinjieExecutionState] = []
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
