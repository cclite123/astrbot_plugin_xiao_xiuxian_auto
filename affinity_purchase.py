"""Passive affinity-item discovery and one-shot safe purchase."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict

try:
    from .automation_safety import SideEffectGuard
    from .market_automation import MarketPage, MarketPageParser
    from .storage import JsonStore
except ImportError:  # pragma: no cover
    from automation_safety import SideEffectGuard
    from market_automation import MarketPage, MarketPageParser
    from storage import JsonStore


@dataclass
class AffinityState:
    enabled: bool = False
    target_item: str = ""
    phase: str = "IDLE"
    found_category: str = ""
    found_page: int = 0
    found_price: int = 0
    sent_at: float = 0.0
    request_id: str = ""
    purchase_command: str = ""
    last_result: str = ""
    paused_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "AffinityState":
        value = value if isinstance(value, dict) else {}
        default = cls()
        return cls(**{name: value.get(name, getattr(default, name)) for name in cls.__dataclass_fields__})


class AffinityPurchaseController:
    MODULE = "affinity_purchase"
    SUCCESS_RE = re.compile(r"道友已成功参与[^\r\n，。]*的缘定")

    def __init__(self, store: JsonStore, guard: SideEffectGuard, config=None, logger=None):
        cfg = dict(config or {})
        self.store, self.guard, self.log = store, guard, logger
        self.enabled_by_config = bool(cfg.get("enabled", True))
        self.query_timeout_sec = max(3.0, float(cfg.get("query_timeout_sec", 20)))
        self.purchase_timeout_sec = max(3.0, float(cfg.get("purchase_timeout_sec", 20)))
        self.action_delay_sec = max(0.0, float(cfg.get("action_delay_sec", 1)))
        self.parser = MarketPageParser()
        self._next_action: Dict[str, float] = {}

    async def _get(self, key: str) -> AffinityState:
        return AffinityState.from_dict(await self.store.get(f"affinity_purchase:{key}"))

    async def _set(self, key: str, state: AffinityState) -> None:
        await self.store.set(f"affinity_purchase:{key}", state.to_dict())

    async def cmd_set_target(self, key: str, name: str) -> str:
        name = self.parser.normalize_name(name)
        if not name:
            return "❌ 结缘物品不能为空，例如：结缘物品 七彩月兰"
        state = await self._get(key)
        state.target_item = name
        state.phase = "ARMED" if state.enabled else "IDLE"
        state.found_category = state.request_id = state.paused_reason = ""
        state.found_page = state.found_price = 0
        await self._set(key, state)
        return f"✅ 结缘监听物品已设置为：{name}"

    async def cmd_enable(self, key: str, send_cb=None) -> str:
        if not self.enabled_by_config:
            return "❌ 结缘模块已在配置中关闭。"
        state = await self._get(key)
        if not state.target_item:
            return "❌ 请先设置结缘物品，例如：结缘物品 七彩月兰"
        state.enabled, state.phase = True, "ARMED"
        state.paused_reason = state.request_id = ""
        account_id = key.split(":", 1)[0]
        await self.store.set(f"affinity_active:{account_id}", key)
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return f"✅ 已开启结缘监听：{state.target_item}；任意群发现后会回本任务群复查再购买。"

    async def cmd_disable(self, key: str) -> str:
        state = await self._get(key)
        state.enabled, state.phase = False, "IDLE"
        state.request_id = ""
        account_id = key.split(":", 1)[0]
        if await self.store.get(f"affinity_active:{account_id}") == key:
            await self.store.set(f"affinity_active:{account_id}", "")
        await self._set(key, state)
        return "🛑 已关闭结缘监听"

    async def cmd_status(self, key: str) -> str:
        state = await self._get(key)
        return (f"💞 结缘监听：{'✅开启' if state.enabled else '🛑关闭'}\n"
                f"目标：{state.target_item or '未设置'}\n阶段：{state.phase}\n"
                f"最近结果：{state.last_result or '无'}"
                + (f"\n暂停原因：{state.paused_reason}" if state.paused_reason else ""))

    async def cmd_reset(self, key: str) -> str:
        state = await self._get(key)
        state.phase = "ARMED" if state.enabled and state.target_item else "IDLE"
        state.sent_at = 0.0
        state.request_id = state.paused_reason = ""
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return "✅ 结缘监听执行状态已重置。"

    async def observe_any_group(self, account_id: str, observed_group_id: str, raw_text: str) -> bool:
        key = str(await self.store.get(f"affinity_active:{account_id}", "") or "")
        if not key:
            return False
        state = await self._get(key)
        if not state.enabled or state.phase != "ARMED" or not state.target_item:
            return False
        page = self.parser.parse(raw_text)
        if page is None:
            return False
        matches = [item for item in page.listings if item.name == state.target_item]
        if not matches:
            return False
        selected = min(matches, key=lambda item: (item.price, item.purchase_command))
        state.phase = "QUERY_PENDING"
        state.found_category, state.found_page = page.category, page.page
        state.found_price = selected.price
        state.last_result = f"在群 {observed_group_id} 发现目标，等待任务群复查"
        self._next_action[key] = time.time()
        await self._set(key, state)
        return True

    async def tick(self, key: str, send_cb) -> None:
        state = await self._get(key)
        if not state.enabled:
            return
        now = time.time()
        if state.phase == "QUERY_PENDING" and now >= self._next_action.get(key, 0):
            command = f"坊市查看{state.found_category}{'' if state.found_page == 1 else state.found_page}"
            await send_cb(command)
            state.phase, state.sent_at = "QUERY_WAIT", now
            await self._set(key, state)
        elif state.phase == "QUERY_WAIT" and now - state.sent_at >= self.query_timeout_sec:
            # Query is read-only; return to listening instead of guessing from a stale UUID.
            state.phase, state.last_result = "ARMED", "任务群复查超时，已恢复监听"
            state.sent_at = 0.0
            await self._set(key, state)
        elif state.phase == "PURCHASE_PENDING" and now >= self._next_action.get(key, 0):
            command = state.purchase_command
            decision = await self.guard.begin(key, self.MODULE, "purchase", command)
            if not decision.allowed:
                state.phase, state.paused_reason = "PAUSED", decision.reason
                await self._set(key, state)
                return
            state.request_id, state.phase, state.sent_at = decision.request_id or "", "PURCHASE_WAIT", now
            state.last_result = f"已发送购买：{state.target_item}（{state.found_price / 10000:g}万）"
            state.purchase_command = ""
            await self._set(key, state)
            await send_cb(command)
        elif state.phase == "PURCHASE_WAIT" and now - state.sent_at >= self.purchase_timeout_sec:
            await self.guard.pause_unknown(key, self.MODULE, state.request_id, "结缘购买等待回执超时")
            state.phase, state.paused_reason = "PAUSED", "结缘购买结果未知，已停止且不会自动重发"
            await self._set(key, state)

    async def on_official_text(self, key: str, raw_text: str, send_cb=None) -> bool:
        state = await self._get(key)
        if not state.enabled:
            return False
        if state.phase == "QUERY_WAIT":
            page = self.parser.parse(raw_text)
            if page is None or page.category != state.found_category or page.page != state.found_page:
                return False
            matches = [item for item in page.listings if item.name == state.target_item and item.purchase_command]
            if not matches:
                state.phase, state.last_result = "ARMED", "任务群复查时目标已消失，已恢复监听"
                await self._set(key, state)
                return True
            selected = min(matches, key=lambda item: (item.price, item.purchase_command))
            state.found_price = selected.price
            state.phase = "PURCHASE_PENDING"
            state.purchase_command = selected.purchase_command
            self._next_action[key] = time.time() + self.action_delay_sec
            await self._set(key, state)
            return True
        if state.phase != "PURCHASE_WAIT":
            return False
        normalized = "".join(
            char for char in str(raw_text or "") if unicodedata.category(char) not in {"Cf", "Zs"}
        )
        if self.SUCCESS_RE.search(normalized):
            await self.guard.confirm(key, self.MODULE, state.request_id, "success")
            state.enabled, state.phase = False, "COMPLETED"
            state.last_result = f"已成功参与 {state.target_item} 缘定"
            state.request_id = ""
            await self.store.set(f"affinity_active:{key.split(':', 1)[0]}", "")
            await self._set(key, state)
            return True
        # Explicit failures are safe to finish, but ambiguous text remains for timeout handling.
        if any(word in normalized for word in ("已经被买走", "灵石不足", "购买失败", "不存在")):
            await self.guard.confirm(key, self.MODULE, state.request_id, "explicit_failure")
            state.phase, state.last_result = "ARMED", "购买未成功，已恢复监听"
            state.request_id = ""
            await self._set(key, state)
            return True
        return False
