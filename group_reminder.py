"""Independent reminder groups for activity settlement and watched market items."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

try:
    from .market_automation import MarketPage, MarketPageParser
    from .storage import JsonStore
except ImportError:  # pragma: no cover
    from market_automation import MarketPage, MarketPageParser
    from storage import JsonStore


DEFAULT_WATCHED_ITEMS = ["太虚乾元诀", "袖里乾坤", "真龙九变", "五指拳心剑", "坐忘论"]


@dataclass
class ReminderGroup:
    settlement_enabled: bool = False
    market_enabled: bool = False
    watched_items: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHED_ITEMS))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReminderState:
    groups: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    settlements: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    notices: list[Dict[str, Any]] = field(default_factory=list)
    seen_listings: Dict[str, float] = field(default_factory=dict)
    last_result: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "ReminderState":
        value = value if isinstance(value, dict) else {}
        default = cls()
        return cls(**{name: value.get(name, getattr(default, name)) for name in cls.__dataclass_fields__})


class GroupReminderController:
    SETTLEMENT_DELAY_SEC = 30

    def __init__(self, store: JsonStore, config=None, logger=None):
        cfg = dict(config or {})
        self.store, self.log = store, logger
        self.enabled_by_config = bool(cfg.get("enabled", True))
        self.listing_ttl_sec = max(60, int(cfg.get("listing_dedupe_sec", 21600)))
        self.parser = MarketPageParser()

    @staticmethod
    def _account(key: str) -> str:
        return str(key).split(":", 1)[0]

    @staticmethod
    def _group(key: str) -> str:
        return str(key).split(":", 1)[1]

    async def _get(self, account_id: str) -> ReminderState:
        return ReminderState.from_dict(await self.store.get(f"group_reminder:{account_id}"))

    async def _set(self, account_id: str, state: ReminderState) -> None:
        await self.store.set(f"group_reminder:{account_id}", state.to_dict())

    @staticmethod
    def _group_state(state: ReminderState, group_id: str) -> ReminderGroup:
        raw = state.groups.get(str(group_id), {})
        return ReminderGroup(
            settlement_enabled=bool(raw.get("settlement_enabled", False)),
            market_enabled=bool(raw.get("market_enabled", False)),
            watched_items=list(raw.get("watched_items", DEFAULT_WATCHED_ITEMS)),
        )

    async def cmd_bind(self, key: str) -> str:
        if not self.enabled_by_config:
            return "❌ 群提醒模块已在配置中关闭。"
        account, group = self._account(key), self._group(key)
        state = await self._get(account)
        if group not in state.groups:
            state.groups[group] = ReminderGroup().to_dict()
        state.last_result = f"已绑定提醒群 {group}"
        await self._set(account, state)
        return f"✅ 已将本群 {group} 绑定为提醒群；提醒开关默认关闭。"

    async def cmd_unbind(self, key: str) -> str:
        account, group = self._account(key), self._group(key)
        state = await self._get(account)
        existed = state.groups.pop(group, None) is not None
        state.notices = [notice for notice in state.notices if notice.get("group_id") != group]
        await self._set(account, state)
        return f"✅ 已解绑提醒群 {group}" if existed else "ℹ️ 本群尚未绑定为提醒群。"

    async def cmd_list(self, key: str) -> str:
        state = await self._get(self._account(key))
        if not state.groups:
            return "📋 当前没有提醒群。"
        lines = ["📋 提醒群列表"]
        for group, raw in sorted(state.groups.items()):
            cfg = self._group_state(state, group)
            lines.append(f"{group}：结算{'开' if cfg.settlement_enabled else '关'} / 坊市{'开' if cfg.market_enabled else '关'}")
        return "\n".join(lines)

    async def _toggle(self, key: str, settlement=None, market=None) -> str:
        account, group = self._account(key), self._group(key)
        state = await self._get(account)
        if group not in state.groups:
            return "❌ 请先在本群发送：绑定提醒群"
        cfg = self._group_state(state, group)
        if settlement is not None:
            cfg.settlement_enabled = bool(settlement)
        if market is not None:
            cfg.market_enabled = bool(market)
        state.groups[group] = cfg.to_dict()
        if not cfg.settlement_enabled:
            state.notices = [n for n in state.notices if not (n.get("group_id") == group and n.get("kind") == "settlement")]
        if not cfg.market_enabled:
            state.notices = [n for n in state.notices if not (n.get("group_id") == group and n.get("kind") == "market")]
        await self._set(account, state)
        return f"✅ 本群提醒：结算{'开启' if cfg.settlement_enabled else '关闭'}，坊市{'开启' if cfg.market_enabled else '关闭'}"

    async def cmd_enable_all(self, key: str) -> str:
        return await self._toggle(key, settlement=True, market=True)

    async def cmd_disable_all(self, key: str) -> str:
        return await self._toggle(key, settlement=False, market=False)

    async def cmd_enable_settlement(self, key: str) -> str:
        return await self._toggle(key, settlement=True)

    async def cmd_disable_settlement(self, key: str) -> str:
        return await self._toggle(key, settlement=False)

    async def cmd_enable_market(self, key: str) -> str:
        return await self._toggle(key, market=True)

    async def cmd_disable_market(self, key: str) -> str:
        return await self._toggle(key, market=False)

    async def cmd_watch(self, key: str, name: str, add: bool) -> str:
        account, group = self._account(key), self._group(key)
        name = self.parser.normalize_name(name)
        state = await self._get(account)
        if group not in state.groups:
            return "❌ 请先在本群发送：绑定提醒群"
        if not name:
            return "❌ 物品名不能为空。"
        cfg = self._group_state(state, group)
        if add and name not in cfg.watched_items:
            cfg.watched_items.append(name)
        if not add:
            cfg.watched_items = [item for item in cfg.watched_items if item != name]
        state.groups[group] = cfg.to_dict()
        await self._set(account, state)
        return f"✅ 已{'增加' if add else '删除'}提醒物品：{name}"

    async def cmd_reset_watch(self, key: str) -> str:
        account, group = self._account(key), self._group(key)
        state = await self._get(account)
        if group not in state.groups:
            return "❌ 请先在本群发送：绑定提醒群"
        cfg = self._group_state(state, group)
        cfg.watched_items = list(DEFAULT_WATCHED_ITEMS)
        state.groups[group] = cfg.to_dict()
        await self._set(account, state)
        return "✅ 已恢复默认坊市提醒物品。"

    async def cmd_status(self, key: str) -> str:
        account, group = self._account(key), self._group(key)
        state = await self._get(account)
        if group not in state.groups:
            return "🔔 本群未绑定为提醒群。"
        cfg = self._group_state(state, group)
        return (f"🔔 本群提醒状态\n结算：{'✅开启' if cfg.settlement_enabled else '🛑关闭'}\n"
                f"坊市：{'✅开启' if cfg.market_enabled else '🛑关闭'}\n"
                f"关注：{'、'.join(cfg.watched_items) or '无'}")

    async def observe_activity(self, key: str, module: str, settle_at_ts: float) -> None:
        if module not in {"bounty", "secret"} or settle_at_ts <= time.time():
            return
        account = self._account(key)
        state = await self._get(account)
        signature = f"{module}:{int(settle_at_ts)}"
        if signature in state.settlements:
            return
        state.settlements[signature] = {
            "module": module,
            "due_at": float(settle_at_ts) + self.SETTLEMENT_DELAY_SEC,
        }
        label = "悬赏" if module == "bounty" else "秘境"
        for group, raw in state.groups.items():
            cfg = self._group_state(state, group)
            if cfg.settlement_enabled:
                state.notices.append({
                    "key": f"start:{signature}:{group}", "group_id": group, "kind": "settlement",
                    "due_at": time.time(), "text": f"✅ 账号 {account} 已开始{label}，完成后会再次提醒。",
                })
        await self._set(account, state)

    async def observe_market(self, account_id: str, page: MarketPage) -> None:
        state = await self._get(account_id)
        now = time.time()
        state.seen_listings = {k: v for k, v in state.seen_listings.items() if now - float(v) <= self.listing_ttl_sec}
        for group in list(state.groups):
            cfg = self._group_state(state, group)
            if not cfg.market_enabled:
                continue
            watched = set(cfg.watched_items)
            for listing in page.listings:
                if listing.name not in watched:
                    continue
                fingerprint = hashlib.sha256(
                    f"{listing.name}|{listing.price}|{listing.purchase_command}".encode("utf-8")
                ).hexdigest()
                if fingerprint in state.seen_listings:
                    continue
                state.seen_listings[fingerprint] = now
                state.notices.append({
                    "key": f"market:{fingerprint}:{group}", "group_id": group, "kind": "market",
                    "due_at": now,
                    "text": f"🏪 坊市提醒\n发现「{listing.name}」\n售价：{listing.price / 10000:g}万\n位置：{page.category}第{page.page}页",
                })
        await self._set(account_id, state)

    async def tick(self, key: str, send_group_cb) -> None:
        account = self._account(key)
        state = await self._get(account)
        now = time.time()
        existing_notice_keys = {n.get("key") for n in state.notices}
        for signature, settlement in list(state.settlements.items()):
            if float(settlement.get("due_at", 0)) > now:
                continue
            module = settlement.get("module")
            label = "悬赏" if module == "bounty" else "秘境"
            for group in list(state.groups):
                cfg = self._group_state(state, group)
                key_name = f"due:{signature}:{group}"
                if cfg.settlement_enabled and key_name not in existing_notice_keys:
                    state.notices.append({
                        "key": key_name, "group_id": group, "kind": "settlement", "due_at": now,
                        "text": f"⏰ 账号 {account} 的{label}已到结算时间。",
                    })
            state.settlements.pop(signature, None)
        remaining = []
        for notice in state.notices:
            if float(notice.get("due_at", 0)) > now:
                remaining.append(notice)
                continue
            try:
                await send_group_cb(str(notice.get("group_id")), str(notice.get("text")))
            except Exception as exc:
                notice["last_error"] = str(exc)
                remaining.append(notice)
        state.notices = remaining
        await self._set(account, state)
