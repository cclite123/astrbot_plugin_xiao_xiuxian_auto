"""按账号和群原子保存完整灵界快照。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from ..file_read_cache import FileReadCache
except ImportError:  # 兼容项目根目录下的独立单元测试导入
    from file_read_cache import FileReadCache

from .model import (
    BuildingsPage,
    LinjieSnapshot,
    PAGE_KINDS,
    ProfilePage,
    UpgradesPage,
    WorkersPage,
)


class LinjieSnapshotRepository:
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

    def replace_from_pages(
        self,
        account_id: str,
        group_id: str,
        pages: dict[str, Any],
        *,
        collected_at: datetime,
    ) -> LinjieSnapshot:
        account = str(account_id).strip()
        group = str(group_id).strip()
        if not account or not group:
            raise ValueError("account_id 和 group_id 不能为空")
        if set(pages) != set(PAGE_KINDS):
            raise ValueError("四类灵界页面必须完整")
        profile = pages["profile"]
        buildings = pages["buildings"]
        upgrades = pages["upgrades"]
        workers = pages["workers"]
        if not isinstance(profile, ProfilePage) or not isinstance(buildings, BuildingsPage) or not isinstance(upgrades, UpgradesPage) or not isinstance(workers, WorkersPage):
            raise ValueError("灵界页面类型无效")
        snapshot = LinjieSnapshot(
            account_id=account,
            group_id=group,
            balance=workers.balance,
            total_output=profile.total_output,
            skill_dao=profile.skill_dao,
            skill_realm=profile.skill_realm,
            has_monthly_card=profile.has_monthly_card,
            buildings=buildings.buildings,
            upgrades=upgrades.upgrades,
            worker_total=workers.worker_total,
            worker_capacity=workers.worker_capacity,
            worker_rank=workers.worker_rank,
            rank_cost=workers.rank_cost,
            workers=workers.workers,
            collected_at=collected_at.isoformat(),
            source_texts={kind: pages[kind].raw_text for kind in PAGE_KINDS},
        )
        with self._lock:
            path = self.path_for(account)
            payload = self._read(path, account) if path.exists() else self._empty(account)
            payload["groups"][group] = snapshot.to_dict()
            self._write(path, payload)
        return snapshot

    def load(self, account_id: str, group_id: str) -> LinjieSnapshot | None:
        account = str(account_id).strip()
        group = str(group_id).strip()
        if not account or not group:
            raise ValueError("account_id 和 group_id 不能为空")
        with self._lock:
            path = self.path_for(account)
            if not path.exists():
                return None
            payload = self._read(path, account)
            raw = payload["groups"].get(group)
            if raw is None:
                return None
            try:
                snapshot = LinjieSnapshot.from_dict(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"灵界快照文件格式错误：{path}") from exc
            if snapshot.account_id != account or snapshot.group_id != group:
                raise RuntimeError(f"灵界快照账号或群不一致：{path}")
            return snapshot

    def _empty(self, account_id: str) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "account_id": account_id, "groups": {}}

    def _read(self, path: Path, account_id: str) -> dict[str, Any]:
        try:
            text = self._file_cache.read(path)
            if text is None:
                raise OSError(f"文件不存在：{path}")
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取灵界快照文件：{path}") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "account_id", "groups"}
            or payload["schema_version"] != self.SCHEMA_VERSION
            or payload["account_id"] != account_id
            or not isinstance(payload["groups"], dict)
        ):
            raise RuntimeError(f"灵界快照文件格式错误：{path}")
        try:
            for raw in payload["groups"].values():
                LinjieSnapshot.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"灵界快照文件格式错误：{path}") from exc
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
