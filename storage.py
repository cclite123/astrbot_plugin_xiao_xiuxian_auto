# 模块：数据存储
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import time
from typing import Any, Dict

def make_key(self_id: str | int, group_id: str | int) -> str:
    return f"{self_id}:{group_id}"

class JsonStore:
    def __init__(self, path: str, flush_interval: float = 2.0):
        self.path = path
        self.flush_interval = flush_interval
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._dirty = False
        self._flush_task: asyncio.Task | None = None
        self._stopped = False
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            backup = f"{self.path}.bad.{int(time.time())}"
            try: os.replace(self.path, backup)
            except Exception: pass
            self._data = {}

    def start(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._flush_task:
            self._flush_task.cancel()
            try: await self._flush_task
            except (asyncio.CancelledError, Exception): pass
        await self.flush(force=True)

    async def _flush_loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return json.loads(json.dumps(self._data.get(key, default)))

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = value
            self._dirty = True

    async def flush(self, force: bool = False) -> None:
        async with self._lock:
            if not self._dirty and not force:
                return
            dir_ = os.path.dirname(self.path) or "."
            os.makedirs(dir_, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=dir_)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.path)
                self._dirty = False
            except Exception:
                try: os.remove(tmp_path)
                except Exception: pass
                raise
