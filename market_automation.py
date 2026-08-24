"""Market page parsing, category sync and passive price observation."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

try:
    from .market_price import MarketPriceProvider
    from .storage import JsonStore
except ImportError:  # pragma: no cover
    from market_price import MarketPriceProvider
    from storage import JsonStore


MARKET_PAGE_COUNTS = {"药材": 8, "装备": 3, "技能": 9, "道具": 1}


@dataclass(frozen=True)
class MarketListing:
    name: str
    price: int
    purchase_command: str = ""


@dataclass(frozen=True)
class MarketPage:
    category: str
    page: int
    listings: tuple[MarketListing, ...]

    @property
    def prices(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for item in self.listings:
            old = result.get(item.name)
            result[item.name] = item.price if old is None else min(old, item.price)
        return result


class MarketPageParser:
    MARKER = "不鼓励不保障任何第三方交易行为"
    NEXT_RE = re.compile(r"command=坊市查看(?P<category>药材|装备|技能|道具)(?P<page>\d*)")
    PRICE_RE = re.compile(r"^价格\s*[:：]\s*(\d+(?:\.\d+)?)\s*(万|亿)?\s+(.+?)\s*$")
    LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)")

    def locate(self, raw_text: str) -> Optional[tuple[str, int]]:
        value = unquote(str(raw_text or ""))
        locations: set[tuple[str, int]] = set()
        for match in self.NEXT_RE.finditer(value):
            category = match.group("category")
            target = int(match.group("page") or "1")
            maximum = MARKET_PAGE_COUNTS[category]
            if 1 <= target <= maximum:
                locations.add((category, maximum if target == 1 else target - 1))
        if len(locations) == 1:
            return locations.pop()
        # Single-page categories and some plain-text adapters omit navigation links.
        header = re.search(r"坊市(?:查看)?(药材|装备|技能|道具).*?第?\s*(\d+)\s*页", value)
        if header:
            category, page = header.group(1), int(header.group(2))
            if 1 <= page <= MARKET_PAGE_COUNTS[category]:
                return category, page
        return None

    def parse(self, raw_text: str) -> Optional[MarketPage]:
        location = self.locate(raw_text)
        value = str(raw_text or "")
        if location is None or self.MARKER not in value:
            return None
        listings: list[MarketListing] = []
        seen: set[tuple[str, int, str]] = set()
        for raw_line in value.splitlines():
            matched = self.PRICE_RE.match(raw_line.strip())
            if not matched:
                continue
            number, unit, raw_name = matched.groups()
            linked = self.LINK_RE.match(raw_name)
            name = self.normalize_name(linked.group(1) if linked else raw_name)
            factor = 100_000_000 if unit == "亿" else 10_000 if unit == "万" else 1
            price = int(float(number) * factor)
            command = self._purchase_command(linked.group(2)) if linked else ""
            signature = (name, price, command)
            if name and signature not in seen:
                seen.add(signature)
                listings.append(MarketListing(name, price, command))
        if not listings:
            return None
        return MarketPage(location[0], location[1], tuple(listings))

    @staticmethod
    def normalize_name(name: str) -> str:
        return re.sub(r"\s+", "", str(name or "").strip())

    @staticmethod
    def _purchase_command(url: str) -> str:
        commands = parse_qs(urlparse(str(url or "")).query).get("command", [])
        if len(commands) == 1 and str(commands[0]).strip().startswith("坊市购买"):
            return str(commands[0]).strip()
        return ""


@dataclass
class MarketSyncState:
    phase: str = "IDLE"
    categories: list[str] = field(default_factory=list)
    category_index: int = 0
    page: int = 1
    sent_at: float = 0.0
    retries: int = 0
    pages: Dict[str, Dict[str, int]] = field(default_factory=dict)
    last_result: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "MarketSyncState":
        value = value if isinstance(value, dict) else {}
        default = cls()
        return cls(**{name: value.get(name, getattr(default, name)) for name in cls.__dataclass_fields__})


class MarketAutomationController:
    def __init__(self, store: JsonStore, market_price: MarketPriceProvider, config=None, logger=None):
        cfg = dict(config or {})
        self.store, self.market_price, self.log = store, market_price, logger
        self.enabled = bool(cfg.get("enabled", True))
        self.response_timeout_sec = max(3.0, float(cfg.get("response_timeout_sec", 20)))
        self.max_retries = max(0, int(cfg.get("max_retries", 2)))
        self.page_delay_sec = max(0.0, float(cfg.get("page_delay_sec", 1)))
        self.parser = MarketPageParser()
        self._next_allowed: Dict[str, float] = {}

    async def _get(self, key: str) -> MarketSyncState:
        return MarketSyncState.from_dict(await self.store.get(f"market_sync:{key}"))

    async def _set(self, key: str, state: MarketSyncState) -> None:
        await self.store.set(f"market_sync:{key}", state.to_dict())

    async def cmd_sync(self, key: str, category: str) -> str:
        category = str(category or "").strip()
        if category not in {*MARKET_PAGE_COUNTS, "全部"}:
            return "❌ 分类无效，可用：药材、装备、技能、道具、全部"
        if not self.enabled:
            return "❌ 坊市分类同步已在配置中关闭。"
        state = MarketSyncState(
            phase="PENDING",
            categories=list(MARKET_PAGE_COUNTS) if category == "全部" else [category],
            category_index=0,
            page=1,
        )
        self._next_allowed[key] = time.time()
        await self._set(key, state)
        return f"✅ 已开始同步坊市价格：{category}；所有页面成功后才提交该分类。"

    async def cmd_status(self, key: str) -> str:
        state = await self._get(key)
        category = state.categories[state.category_index] if state.categories and state.category_index < len(state.categories) else "无"
        return (f"🏪 坊市同步阶段：{state.phase}\n当前：{category} 第{state.page}页\n"
                f"最近结果：{state.last_result or '无'}" + (f"\n错误：{state.error}" if state.error else ""))

    async def cmd_reset(self, key: str) -> str:
        await self._set(key, MarketSyncState())
        self._next_allowed.pop(key, None)
        return "✅ 坊市同步执行状态已重置。"

    async def cmd_query(self, key: str, category: str, page: int = 1) -> str:
        category = str(category or "").strip()
        catalog = await self.store.get(f"market_catalog:{key}", {})
        page_data = dict(catalog.get(category, {}).get(str(page), {}) if isinstance(catalog, dict) else {})
        if not page_data:
            return f"ℹ️ 暂无{category}第{page}页的本地观察价格。"
        lines = [f"🏪 {category}第{page}页价格（{len(page_data)} 项）"]
        for name, price in sorted(page_data.items(), key=lambda item: (int(item[1]), item[0])):
            lines.append(f"{name}：{int(price) / 10000:g}万")
        return "\n".join(lines)

    async def tick(self, key: str, send_cb) -> None:
        state = await self._get(key)
        now = time.time()
        if state.phase == "PENDING" and now >= self._next_allowed.get(key, 0):
            category = state.categories[state.category_index]
            command = f"坊市查看{category}{'' if state.page == 1 else state.page}"
            await send_cb(command)
            state.phase, state.sent_at = "WAITING", now
            await self._set(key, state)
        elif state.phase == "WAITING" and now - state.sent_at >= self.response_timeout_sec:
            if state.retries < self.max_retries:
                state.retries += 1
                state.phase = "PENDING"
                self._next_allowed[key] = now + self.page_delay_sec
            else:
                state.phase = "FAILED"
                state.error = f"第{state.page}页等待回执超时，分类未提交"
            await self._set(key, state)

    async def observe_page(self, key: str, raw_text: str) -> Optional[MarketPage]:
        page = self.parser.parse(raw_text)
        if page is None:
            return None
        catalog = await self.store.get(f"market_catalog:{key}", {})
        if not isinstance(catalog, dict):
            catalog = {}
        category_pages = catalog.setdefault(page.category, {})
        category_pages[str(page.page)] = page.prices
        await self.store.set(f"market_catalog:{key}", catalog)
        await self.market_price.merge_observed_prices(page.prices, source=f"坊市被动观察 {page.category}{page.page}")

        state = await self._get(key)
        if state.phase != "WAITING" or not state.categories:
            return page
        expected_category = state.categories[state.category_index]
        if page.category != expected_category or page.page != state.page:
            return page
        state.pages[str(page.page)] = page.prices
        state.retries = 0
        maximum = MARKET_PAGE_COUNTS[expected_category]
        if state.page < maximum:
            state.page += 1
            state.phase = "PENDING"
            self._next_allowed[key] = time.time() + self.page_delay_sec
            await self._set(key, state)
            return page

        combined: Dict[str, int] = {}
        for prices in state.pages.values():
            for name, price in prices.items():
                old = combined.get(name)
                combined[name] = int(price) if old is None else min(old, int(price))
        previous_names: set[str] = set()
        old_catalog = catalog.get(expected_category, {})
        for prices in old_catalog.values() if isinstance(old_catalog, dict) else []:
            if isinstance(prices, dict):
                previous_names.update(prices)
        await self.market_price.merge_observed_prices(
            combined, source=f"坊市完整同步 {expected_category}", replace_names=previous_names
        )
        state.last_result = f"{expected_category}同步完成：{len(combined)}项"
        state.category_index += 1
        state.page, state.pages = 1, {}
        if state.category_index >= len(state.categories):
            state.phase = "COMPLETED"
        else:
            state.phase = "PENDING"
            self._next_allowed[key] = time.time() + self.page_delay_sec
        await self._set(key, state)
        return page
