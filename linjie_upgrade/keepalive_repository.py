"""灵界保活状态持久化。"""

from __future__ import annotations

import json
from pathlib import Path

from .keepalive_model import LinjieKeepaliveState

try:
    from ..file_read_cache import FileReadCache
except ImportError:  # 兼容项目根目录下的独立单元测试导入
    from file_read_cache import FileReadCache


class LinjieKeepaliveRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._file_cache = FileReadCache()

    def _path(self, account_id: str, group_id: str) -> Path:
        return self.root / f"{account_id}_{group_id}.json"

    def load(self, account_id: str, group_id: str) -> LinjieKeepaliveState:
        path = self._path(account_id, group_id)
        text = self._file_cache.read(path)
        if text is None:
            return LinjieKeepaliveState(str(account_id), str(group_id))
        try:
            data = json.loads(text)
            return LinjieKeepaliveState.from_dict(data, account_id=str(account_id), group_id=str(group_id))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"灵界保活状态文件格式错误或无法读取：{path}") from exc

    def save(self, state: LinjieKeepaliveState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.account_id, state.group_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        self._file_cache.invalidate(path)
