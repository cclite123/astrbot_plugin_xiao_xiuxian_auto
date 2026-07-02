# 模块：坊市价格
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class MarketPriceProvider:
















    def __init__(
        self,
        *,
        enabled: bool = True,
        source: str = "hybrid",
        local_path: str = "",
        remote_url: str = "",
        api_key: str = "",
        ttl_seconds: int = 21600,
        refresh_interval_sec: int = 300,
        timeout_sec: float = 5.0,
        logger=None,
    ):
        self.enabled = bool(enabled)
        self.source = str(source or "hybrid").strip().lower()
        if self.source not in {"local", "remote", "hybrid"}:
            self.source = "hybrid"

        self.local_path = os.path.abspath(local_path) if local_path else ""
        self.remote_url = str(remote_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.refresh_interval_sec = max(10, int(refresh_interval_sec))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.log = logger

        self._items: Dict[str, Dict[str, Any]] = {}
        self._loaded_from = "未加载"
        self._local_mtime = 0.0
        self._last_remote_fetch_ts = 0.0
        self._last_error = ""
        self._lock = asyncio.Lock()


    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    def _warn(self, msg: str) -> None:
        self._last_error = msg
        if self.log:
            self.log.warning(msg)


    @staticmethod
    def normalize_name(name: str) -> str:

        name = str(name or "").strip()
        name = re.sub(r"\s+", "", name)
        name = name.replace("：", ":").replace("，", ",")
        return name

    @staticmethod
    def _atomic_write_json(path: str, data: dict) -> None:

        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_market_", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise

    def _normalize_payload(self, data: dict) -> Dict[str, Dict[str, Any]]:




        items = data.get("items", {}) if isinstance(data, dict) else {}
        if not isinstance(items, dict):
            raise ValueError("items 字段不是 dict")

        root_updated_at = float(data.get("updated_at", 0) or 0) if isinstance(data, dict) else 0.0


        root_unit = str(data.get("unit", "") or "").strip() if isinstance(data, dict) else ""
        normalized: Dict[str, Dict[str, Any]] = {}

        for raw_name, raw_info in items.items():
            name = self.normalize_name(raw_name)
            if not name:
                continue

            if isinstance(raw_info, dict):
                price = raw_info.get("price", 0)
                updated_at = raw_info.get("updated_at", root_updated_at)
                source = raw_info.get("source", "unknown")
                unit = str(raw_info.get("unit", root_unit) or root_unit or "万").strip()
            else:
                price = raw_info
                updated_at = root_updated_at
                source = "unknown"
                unit = root_unit or "万"

            try:
                price = int(float(price))
            except Exception:
                price = 0
            try:
                updated_at = float(updated_at or 0)
            except Exception:
                updated_at = 0.0

            if price <= 0:
                continue

            normalized[name] = {
                "price": price,
                "updated_at": updated_at,
                "source": str(source or "unknown"),
                "unit": str(unit or "万"),
            }

        return normalized

    def _load_local_if_changed(self) -> bool:
        if not self.local_path or not os.path.exists(self.local_path):
            return False
        mtime = os.path.getmtime(self.local_path)
        if mtime <= self._local_mtime and self._items:
            return True
        with open(self.local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._items = self._normalize_payload(data)
        self._local_mtime = mtime
        self._loaded_from = f"本地缓存：{self.local_path}"
        self._info(f"[market_price] 已加载本地坊市价格 {len(self._items)} 条")
        return True

    def _fetch_remote_sync(self) -> dict:
        if not self.remote_url:
            raise ValueError("remote_url 未配置")
        req = urllib.request.Request(self.remote_url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "astrbot-xiao-xiuxian-auto/market-price-client")
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)

    async def refresh(self, force: bool = False) -> bool:




        if not self.enabled:
            return False

        async with self._lock:
            now = time.time()
            should_fetch_remote = (
                self.source in {"remote", "hybrid"}
                and self.remote_url
                and (force or now - self._last_remote_fetch_ts >= self.refresh_interval_sec)
            )

            if should_fetch_remote:
                self._last_remote_fetch_ts = now
                try:
                    data = await asyncio.to_thread(self._fetch_remote_sync)
                    self._items = self._normalize_payload(data)
                    self._loaded_from = f"远程接口：{self.remote_url}"
                    self._last_error = ""
                    self._info(f"[market_price] 已拉取远程坊市价格 {len(self._items)} 条")
                    if self.local_path:
                        await asyncio.to_thread(self._atomic_write_json, self.local_path, data)
                        try:
                            self._local_mtime = os.path.getmtime(self.local_path)
                        except Exception:
                            pass
                    return bool(self._items)
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, Exception) as e:
                    self._warn(f"[market_price] 拉取远程坊市价格失败：{e}")
                    if self.source == "remote" and not self.local_path:
                        return bool(self._items)

            if self.source in {"local", "hybrid", "remote"} and self.local_path:
                try:
                    return self._load_local_if_changed() or bool(self._items)
                except Exception as e:
                    self._warn(f"[market_price] 读取本地坊市价格失败：{e}")

            return bool(self._items)

    def _price_is_valid(self, info: dict) -> bool:
        price = int(info.get("price", 0) or 0)
        if price <= 0:
            return False
        if self.ttl_seconds <= 0:
            return True
        updated_at = float(info.get("updated_at", 0) or 0)

        if updated_at <= 0:
            return True
        return time.time() - updated_at <= self.ttl_seconds

    async def get_price(self, item_name: str) -> Optional[int]:






        info = await self.get_price_info(item_name)
        if not info:
            return None
        return int(info.get("price", 0) or 0)

    async def get_price_info(self, item_name: str) -> Optional[Dict[str, Any]]:







        if not self.enabled:
            return None
        await self.refresh(force=False)
        name = self.normalize_name(item_name)
        info = self._items.get(name)
        if not info or not self._price_is_valid(info):
            return None
        return dict(info)

    async def find_price_in_text(self, text: str) -> Optional[int]:

        if not self.enabled:
            return None
        await self.refresh(force=False)
        text_norm = self.normalize_name(text)
        if not text_norm:
            return None

        best: Optional[int] = None
        for item_name, info in self._items.items():
            if item_name and item_name in text_norm and self._price_is_valid(info):
                price = int(info.get("price", 0) or 0)
                if price > 0 and (best is None or price > best):
                    best = price
        return best

    async def summary(self) -> str:
        await self.refresh(force=False)
        if not self.enabled:
            return "坊市价格：已关闭"
        parts = [
            f"坊市价格：已加载 {len(self._items)} 条",
            f"来源：{self._loaded_from}",
            f"模式：{self.source}",
        ]
        if self.remote_url:
            parts.append(f"远程：{self.remote_url}")
        if self.local_path:
            parts.append(f"本地缓存：{self.local_path}")
        if self._last_error:
            parts.append(f"最近错误：{self._last_error}")
        return "\n".join(parts)
