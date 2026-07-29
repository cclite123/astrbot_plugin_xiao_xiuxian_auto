from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from .time_utils import BEIJING_TZ
except Exception:
    from time_utils import BEIJING_TZ


def beijing_date() -> str:
    now = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
    return now.strftime("%Y-%m-%d")


class DailySendStats:
    STORAGE_KEY = "daily_send_stats"
    CATEGORIES = ("market_view", "purchase", "alchemy")
    OFFICIAL_MENTION_RE = re.compile(r"^@\d+\s*")

    def __init__(
        self,
        store,
        date_provider: Optional[Callable[[], str]] = None,
    ):
        self.store = store
        self.date_provider = date_provider or beijing_date
        self._lock = asyncio.Lock()

    @classmethod
    def classify(cls, text: str) -> Optional[str]:
        command = cls.OFFICIAL_MENTION_RE.sub("", str(text or "").strip())
        if command.startswith("坊市查看"):
            return "market_view"
        if command.startswith("坊市购买"):
            return "purchase"
        if command.startswith("配方主药"):
            return "alchemy"
        return None

    @staticmethod
    def _count(value: Any) -> int:
        try:
            count = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, count)

    def _normalize(
        self,
        raw: Any,
        today: str,
    ) -> Tuple[Dict[str, Any], bool]:
        if not isinstance(raw, dict) or str(raw.get("date") or "") != today:
            normalized = {"date": today, "accounts": {}}
            return normalized, normalized != raw

        accounts: Dict[str, Dict[str, int]] = {}
        raw_accounts = raw.get("accounts")
        if isinstance(raw_accounts, dict):
            for raw_self_id, raw_counts in raw_accounts.items():
                self_id = str(raw_self_id or "").strip()
                if not self_id or not isinstance(raw_counts, dict):
                    continue
                accounts[self_id] = {
                    category: self._count(raw_counts.get(category))
                    for category in self.CATEGORIES
                }
        normalized = {"date": today, "accounts": accounts}
        return normalized, normalized != raw

    async def record(self, self_id: str, text: str) -> bool:
        category = self.classify(text)
        self_id = str(self_id or "").strip()
        if category is None or not self_id:
            return False
        today = str(self.date_provider())
        async with self._lock:
            raw = await self.store.get(self.STORAGE_KEY, {})
            data, _ = self._normalize(raw, today)
            counts = data["accounts"].setdefault(
                self_id,
                {name: 0 for name in self.CATEGORIES},
            )
            counts[category] += 1
            await self.store.set(self.STORAGE_KEY, data)
        return True

    async def snapshot(self, self_id: str) -> Dict[str, Any]:
        self_id = str(self_id or "").strip()
        today = str(self.date_provider())
        async with self._lock:
            raw = await self.store.get(self.STORAGE_KEY, {})
            data, changed = self._normalize(raw, today)
            if changed:
                await self.store.set(self.STORAGE_KEY, data)
            source = data["accounts"].get(self_id, {})
            counts = {
                category: self._count(source.get(category))
                for category in self.CATEGORIES
            }
        return {
            "date": today,
            "counts": counts,
            "total": sum(counts.values()),
        }
