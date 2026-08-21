"""通用状态文件的只读缓存。

调度循环每秒都会读盘（list_states / find），账号多时磁盘 IO 与 JSON
解析放大。本模块按 (mtime_ns, size) 指纹缓存文件文本，写路径调用
``invalidate`` 立即失效，向外提供的仍然是每次最新内容。

前提：AstrBot 单进程运行插件，状态文件只有插件自己写入（与“先写盘后
发送”的单写者假设一致）。同进程写后由 invalidate 兜底；跨进程热替换
文件时凭 stat 指纹自然失效。不吞读取错误：只有文件不存在返回 None，
其他 OSError 仍然抛出，保留各仓储原有的错误语义。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional


class FileReadCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[tuple[int, int] | None, str]] = {}

    def read(self, path: Path) -> Optional[str]:
        """返回最新文件文本；文件不存在返回 None。"""
        token = self._stat_token(path)
        with self._lock:
            entry = self._entries.get(str(path))
        if entry is not None and entry[0] == token:
            return entry[1]
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if token is not None:
            with self._lock:
                self._entries[str(path)] = (token, text)
        return text

    def invalidate(self, path: Path) -> None:
        with self._lock:
            self._entries.pop(str(path), None)

    @staticmethod
    def _stat_token(path: Path) -> Optional[tuple[int, int]]:
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            return None