"""Daily dual-cultivation automation with persistent, no-duplicate actions."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def _china_now() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=8)))

try:
    from .automation_safety import SideEffectGuard
    from .storage import JsonStore
except ImportError:  # pragma: no cover
    from automation_safety import SideEffectGuard
    from storage import JsonStore


@dataclass
class DualState:
    enabled: bool = False
    dao_name: str = ""
    bonded_dao_name: str = ""
    phase: str = "IDLE"
    next_action_ts: float = 0.0
    sent_at: float = 0.0
    request_id: str = ""
    pending_action: str = ""
    self_remaining: int = 0
    bonded_remaining: int = 0
    query_retries: int = 0
    cycle_date: str = ""
    completed_at: float = 0.0
    last_result: str = ""
    paused_reason: str = ""
    processed_reply_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "DualState":
        value = value if isinstance(value, dict) else {}
        default = cls()
        return cls(**{name: value.get(name, getattr(default, name)) for name in cls.__dataclass_fields__})


class DailyDualController:
    MODULE = "dual"
    QUERY = "我的双修次数"
    COUNT_RE = re.compile(r"(?<!与)(?:[^\r\n!！。]{0,30}?道友)?\s*(?:还)?剩余双修次数\s*[:：]?\s*(\d+)\s*次")
    BONDED_COUNT_RE = re.compile(r"与\s*羁绊道友(?:专属)?\s*双修剩余次数\s*[:：]?\s*(\d+)\s*次")
    PARTNER_COUNT_RE = re.compile(r"与\s*([^\r\n!！。]+?)\s*双修剩余次数\s*[:：]?\s*(\d+)\s*次")
    BONDED_INLINE_RE = re.compile(r"\[[^\]]*羁绊道友[^\]]*\]\((mqqapi://aio/inlinecmd\?[^)]*)\)", re.I)
    SUCCESS_RE = re.compile(r"双修成功|成功双修|双修完成|双修收益|一起修炼了一晚")
    NO_TIMES_RE = re.compile(r"双修次数已用尽|没有双修次数|双修次数不足|今日双修次数已用尽")
    TOO_STRONG_RE = re.compile(r"修仙大能看了看你.*不屑一顾.*扬长而去|对方修为比你高", re.S)
    BUSY_RE = re.compile(r"上一条指令(?:还没|尚未)执行完毕")

    def __init__(self, store: JsonStore, guard: SideEffectGuard, config=None, logger=None):
        cfg = dict(config or {})
        self.store = store
        self.guard = guard
        self.log = logger
        self.enabled_by_config = bool(cfg.get("enabled", True))
        self.schedule_time = str(cfg.get("schedule_time", "08:20"))
        self.response_timeout_sec = max(3.0, float(cfg.get("response_timeout_sec", 15)))
        self.query_max_retries = max(0, int(cfg.get("query_max_retries", 3)))
        self.action_delay_sec = max(0.0, float(cfg.get("action_delay_sec", 2)))

    async def _get(self, key: str) -> DualState:
        return DualState.from_dict(await self.store.get(f"daily_dual:{key}"))

    async def _set(self, key: str, state: DualState) -> None:
        await self.store.set(f"daily_dual:{key}", state.to_dict())

    def _cycle_date(self, now: Optional[datetime] = None) -> str:
        now = now or _china_now()
        return now.strftime("%Y-%m-%d")

    def _next_schedule_ts(self) -> float:
        now = _china_now()
        try:
            hour, minute = (int(part) for part in self.schedule_time.split(":", 1))
        except Exception:
            hour, minute = 8, 20
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()

    async def cmd_set_dao_name(self, key: str, name: str) -> str:
        name = str(name or "").strip()
        if not name:
            return "❌ 双修道号不能为空，例如：双修道号 赵元万一"
        state = await self._get(key)
        state.dao_name = name
        if state.phase == "PAUSED" and "更换双修道号" in state.paused_reason:
            state.phase, state.paused_reason = "IDLE", ""
        await self._set(key, state)
        return f"✅ 双修道号已设置为：{name}"

    async def cmd_enable(self, key: str, send_cb=None) -> str:
        if not self.enabled_by_config:
            return "❌ 双修模块已在配置中关闭。"
        state = await self._get(key)
        if not state.dao_name:
            return "❌ 请先设置双修道号，例如：双修道号 赵元万一"
        state.enabled = True
        state.phase = "QUERY_PENDING"
        state.next_action_ts = time.time() + 1
        state.sent_at = 0.0
        state.request_id = ""
        state.pending_action = ""
        state.query_retries = 0
        state.cycle_date = self._cycle_date()
        state.paused_reason = ""
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return f"✅ 已开启双修，道号：{state.dao_name}；即将查询今日次数。"

    async def cmd_disable(self, key: str) -> str:
        state = await self._get(key)
        state.enabled = False
        state.phase = "IDLE"
        state.next_action_ts = state.sent_at = 0.0
        state.request_id = state.pending_action = ""
        await self._set(key, state)
        return "🛑 已关闭双修"

    async def cmd_status(self, key: str) -> str:
        state = await self._get(key)
        return (f"👥 双修：{'✅开启' if state.enabled else '🛑关闭'}\n"
                f"道号：{state.dao_name or '未设置'}\n阶段：{state.phase}\n"
                f"普通/羁绊剩余：{state.self_remaining}/{state.bonded_remaining}\n"
                f"羁绊道号：{state.bonded_dao_name or '未识别'}\n"
                f"最近结果：{state.last_result or '无'}"
                + (f"\n暂停原因：{state.paused_reason}" if state.paused_reason else ""))

    async def cmd_reset(self, key: str) -> str:
        state = await self._get(key)
        state.phase = "QUERY_PENDING" if state.enabled and state.dao_name else "IDLE"
        state.next_action_ts = time.time() + 1 if state.phase == "QUERY_PENDING" else 0.0
        state.sent_at = 0.0
        state.request_id = state.pending_action = state.paused_reason = ""
        state.query_retries = 0
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return "✅ 双修执行状态已重置。"

    async def tick(self, key: str, send_cb) -> None:
        state = await self._get(key)
        if not state.enabled:
            return
        now = time.time()
        today = self._cycle_date()
        if state.cycle_date != today and now >= self._schedule_today_ts():
            state.phase, state.cycle_date = "QUERY_PENDING", today
            state.next_action_ts, state.query_retries = now, 0
            state.paused_reason = ""
            await self.guard.reset_module(key, self.MODULE)
            await self._set(key, state)
        if state.phase == "SLEEPING" or state.phase == "PAUSED":
            return
        if state.phase == "QUERY_PENDING" and now >= state.next_action_ts:
            await send_cb(self.QUERY)
            state.phase, state.sent_at = "QUERY_WAIT", now
            await self._set(key, state)
            return
        if state.phase == "QUERY_WAIT" and now - state.sent_at >= self.response_timeout_sec:
            if state.query_retries < self.query_max_retries:
                state.query_retries += 1
                state.phase, state.next_action_ts = "QUERY_PENDING", now + self.action_delay_sec
            else:
                state.phase, state.paused_reason = "PAUSED", "查询双修次数连续超时"
            await self._set(key, state)
            return
        if state.phase == "ACTION_PENDING" and now >= state.next_action_ts:
            target = state.bonded_dao_name if state.pending_action == "bonded" else state.dao_name
            command = f"双修 {target}"
            decision = await self.guard.begin(key, self.MODULE, state.pending_action or "normal", command)
            if not decision.allowed:
                state.phase, state.paused_reason = "PAUSED", decision.reason
                await self._set(key, state)
                return
            state.request_id = decision.request_id or ""
            state.sent_at = now
            state.phase = "ACTION_WAIT"
            await self._set(key, state)
            await send_cb(command)
            return
        if state.phase == "ACTION_WAIT" and now - state.sent_at >= self.response_timeout_sec:
            await self.guard.pause_unknown(key, self.MODULE, state.request_id, "双修指令等待回执超时")
            state.phase, state.paused_reason = "PAUSED", "双修结果未知，已停止且不会自动重发"
            await self._set(key, state)

    def _schedule_today_ts(self) -> float:
        now = _china_now()
        try:
            hour, minute = (int(part) for part in self.schedule_time.split(":", 1))
        except Exception:
            hour, minute = 8, 20
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()

    async def on_official_text(self, key: str, text: str, send_cb=None, message_id: str = "") -> bool:
        state = await self._get(key)
        if not state.enabled or state.phase not in {"QUERY_WAIT", "ACTION_WAIT"}:
            return False
        text = str(text or "")
        if message_id and message_id in state.processed_reply_ids:
            return True
        if state.phase == "QUERY_WAIT":
            visible = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
            own = self.COUNT_RE.search(visible)
            bonded = self.BONDED_COUNT_RE.search(visible)
            partners = [(m.group(1).strip(), int(m.group(2))) for m in self.PARTNER_COUNT_RE.finditer(visible)]
            if not own and not bonded and not partners:
                return False
            state.self_remaining = int(own.group(1)) if own else 0
            state.bonded_remaining = int(bonded.group(1)) if bonded else 0
            state.bonded_dao_name = self._bonded_name(text)
            if not state.bonded_dao_name:
                for name, count in partners:
                    if "羁绊道友" not in name:
                        state.bonded_dao_name, state.bonded_remaining = name, count
                        break
            if state.self_remaining > 0:
                self._queue_action(state, "normal")
            elif state.bonded_remaining > 0 and state.bonded_dao_name:
                self._queue_action(state, "bonded")
            elif state.bonded_remaining > 0:
                state.phase, state.paused_reason = "PAUSED", "存在羁绊次数但未识别到羁绊道号"
            else:
                self._finish_today(state, "今日双修次数已用完")
            self._remember(state, message_id)
            await self._set(key, state)
            return True
        if self.BUSY_RE.search(text):
            await self.guard.confirm(key, self.MODULE, state.request_id, "busy_not_executed")
            self._queue_action(state, state.pending_action or "normal", delay=max(2.0, self.action_delay_sec))
        elif self.SUCCESS_RE.search(text):
            await self.guard.confirm(key, self.MODULE, state.request_id, "success")
            if state.pending_action == "bonded":
                state.bonded_remaining = max(0, state.bonded_remaining - 1)
            else:
                state.self_remaining = max(0, state.self_remaining - 1)
            if state.self_remaining > 0:
                self._queue_action(state, "normal")
            elif state.bonded_remaining > 0 and state.bonded_dao_name:
                self._queue_action(state, "bonded")
            else:
                self._finish_today(state, "双修完成")
        elif self.NO_TIMES_RE.search(text):
            await self.guard.confirm(key, self.MODULE, state.request_id, "no_times")
            if state.pending_action != "bonded" and state.bonded_remaining > 0 and state.bonded_dao_name:
                state.self_remaining = 0
                self._queue_action(state, "bonded")
            else:
                self._finish_today(state, "今日双修次数已用完")
        elif self.TOO_STRONG_RE.search(text):
            await self.guard.confirm(key, self.MODULE, state.request_id, "partner_too_strong")
            state.phase, state.paused_reason = "PAUSED", "对方修为比你高，请更换双修道号"
        else:
            return False
        self._remember(state, message_id)
        await self._set(key, state)
        return True

    def _queue_action(self, state: DualState, action: str, delay: Optional[float] = None) -> None:
        state.phase = "ACTION_PENDING"
        state.pending_action = action
        state.next_action_ts = time.time() + (self.action_delay_sec if delay is None else delay)
        state.sent_at = 0.0
        state.request_id = ""

    def _finish_today(self, state: DualState, result: str) -> None:
        state.phase = "SLEEPING"
        state.completed_at = time.time()
        state.next_action_ts = self._next_schedule_ts()
        state.last_result = result
        state.sent_at = 0.0
        state.request_id = state.pending_action = ""

    @staticmethod
    def _remember(state: DualState, message_id: str) -> None:
        if message_id:
            state.processed_reply_ids = (state.processed_reply_ids + [str(message_id)])[-50:]

    def _bonded_name(self, text: str) -> str:
        for match in self.BONDED_INLINE_RE.finditer(text):
            for command in parse_qs(urlsplit(unescape(match.group(1))).query).get("command", []):
                found = re.fullmatch(r"\s*双修\s+(.+?)\s*", command)
                if found:
                    return found.group(1).strip()
        return ""
