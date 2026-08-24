"""Automated Spirit Realm mining with no replay after an ambiguous result."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

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
class LinjieMiningState:
    enabled: bool = False
    phase: str = "IDLE"
    cycle_date: str = ""
    mined_count: int = 0
    next_action_ts: float = 0.0
    sent_at: float = 0.0
    deadline_ts: float = 0.0
    request_id: str = ""
    last_balance: int = 0
    last_result: str = ""
    paused_reason: str = ""
    processed_reply_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "LinjieMiningState":
        value = value if isinstance(value, dict) else {}
        default = cls()
        return cls(**{name: value.get(name, getattr(default, name)) for name in cls.__dataclass_fields__})


class LinjieMiningController:
    MODULE = "linjie_mining"
    COMMAND = "灵界挖灵石"
    START_RE = re.compile(r"你提起矿[稿镐]，向着灵山走去")
    DURATION_RE = re.compile(r"本次挖矿时长[:：]\s*(\d+)(?:秒|s)", re.I)
    SUCCESS_RE = re.compile(r"成功采集到([\d,.]+)[\s\S]*?灵矿石储备[:：]\s*([\d,.]+(?:万|亿|兆|京)?)")
    BUSY_RE = re.compile(r"你已经在挖矿了[（(]\s*剩余\s*(\d+)\s*(?:秒|s)\s*[）)]", re.I)
    LIMIT_RE = re.compile(r"今日不应当再次前往矿山了")

    def __init__(self, store: JsonStore, guard: SideEffectGuard, config=None, logger=None):
        cfg = dict(config or {})
        self.store, self.guard, self.log = store, guard, logger
        self.enabled_by_config = bool(cfg.get("enabled", True))
        self.schedule_time = str(cfg.get("schedule_time", "08:10"))
        self.response_timeout_sec = max(3.0, float(cfg.get("response_timeout_sec", 15)))
        self.extra_wait_sec = max(0.0, float(cfg.get("extra_wait_sec", 10)))
        self.action_delay_sec = max(0.0, float(cfg.get("action_delay_sec", 2)))

    async def _get(self, key: str) -> LinjieMiningState:
        return LinjieMiningState.from_dict(await self.store.get(f"linjie_mining:{key}"))

    async def _set(self, key: str, state: LinjieMiningState) -> None:
        await self.store.set(f"linjie_mining:{key}", state.to_dict())

    def _cycle_date(self) -> str:
        now = _china_now()
        try:
            hour, minute = (int(part) for part in self.schedule_time.split(":", 1))
        except Exception:
            hour, minute = 8, 10
        cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < cutoff:
            now -= timedelta(days=1)
        return now.strftime("%Y-%m-%d")

    async def cmd_enable(self, key: str, send_cb=None) -> str:
        if not self.enabled_by_config:
            return "❌ 灵界挖矿模块已在配置中关闭。"
        state = await self._get(key)
        current_cycle = self._cycle_date()
        if state.cycle_date != current_cycle:
            state.mined_count = 0
        state.enabled = True
        state.cycle_date = current_cycle
        state.phase = "PENDING"
        state.next_action_ts = time.time() + 1
        state.sent_at = state.deadline_ts = 0.0
        state.request_id = state.paused_reason = ""
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return "✅ 已开启灵界挖矿；将持续挖取到今日额度耗尽。"

    async def cmd_disable(self, key: str) -> str:
        state = await self._get(key)
        state.enabled, state.phase = False, "IDLE"
        state.next_action_ts = state.sent_at = state.deadline_ts = 0.0
        state.request_id = ""
        await self._set(key, state)
        return "🛑 已关闭灵界挖矿"

    async def cmd_status(self, key: str) -> str:
        state = await self._get(key)
        return (f"⛏️ 灵界挖矿：{'✅开启' if state.enabled else '🛑关闭'}\n"
                f"阶段：{state.phase}\n今日已完成：{state.mined_count} 次\n"
                f"灵矿石储备：{state.last_balance or '未知'}\n最近结果：{state.last_result or '无'}"
                + (f"\n暂停原因：{state.paused_reason}" if state.paused_reason else ""))

    async def cmd_reset(self, key: str) -> str:
        state = await self._get(key)
        state.phase = "PENDING" if state.enabled else "IDLE"
        state.next_action_ts = time.time() + 1 if state.enabled else 0.0
        state.sent_at = state.deadline_ts = 0.0
        state.request_id = state.paused_reason = ""
        await self.guard.reset_module(key, self.MODULE)
        await self._set(key, state)
        return "✅ 灵界挖矿执行状态已重置。"

    async def tick(self, key: str, send_cb) -> None:
        state = await self._get(key)
        if not state.enabled:
            return
        now = time.time()
        cycle = self._cycle_date()
        if state.cycle_date != cycle:
            state.cycle_date, state.mined_count = cycle, 0
            state.phase, state.next_action_ts = "PENDING", now
            state.paused_reason = ""
            await self.guard.reset_module(key, self.MODULE)
            await self._set(key, state)
        if state.phase == "PENDING" and now >= state.next_action_ts:
            decision = await self.guard.begin(key, self.MODULE, "mine", self.COMMAND)
            if not decision.allowed:
                state.phase, state.paused_reason = "PAUSED", decision.reason
                await self._set(key, state)
                return
            state.phase, state.sent_at = "ACTION_WAIT", now
            state.request_id = decision.request_id or ""
            await self._set(key, state)
            await send_cb(self.COMMAND)
        elif state.phase == "ACTION_WAIT" and now - state.sent_at >= self.response_timeout_sec:
            await self.guard.pause_unknown(key, self.MODULE, state.request_id, "灵界挖矿等待开始回执超时")
            state.phase, state.paused_reason = "PAUSED", "灵界挖矿结果未知，已停止且不会自动重发"
            await self._set(key, state)
        elif state.phase == "WAITING" and state.deadline_ts and now >= state.deadline_ts:
            state.phase, state.paused_reason = "PAUSED", "灵界挖矿等待结算回执超时；不会重发本次指令"
            await self._set(key, state)

    async def on_official_text(self, key: str, text: str, send_cb=None, message_id: str = "") -> bool:
        state = await self._get(key)
        if not state.enabled or state.phase not in {"ACTION_WAIT", "WAITING"}:
            return False
        if message_id and message_id in state.processed_reply_ids:
            return True
        value = str(text or "")
        if self.LIMIT_RE.search(value):
            if state.request_id:
                await self.guard.confirm(key, self.MODULE, state.request_id, "daily_limit")
            state.phase, state.last_result = "COMPLETED", "今日灵界挖矿额度已耗尽"
            state.request_id = ""
        else:
            busy = self.BUSY_RE.search(value)
            started = self.START_RE.search(value)
            success = self.SUCCESS_RE.search(value)
            if busy:
                await self.guard.confirm(key, self.MODULE, state.request_id, "busy_not_executed")
                state.phase = "PENDING"
                state.next_action_ts = time.time() + int(busy.group(1)) + 1
                state.request_id = ""
            elif started and state.phase == "ACTION_WAIT":
                duration = self.DURATION_RE.search(value)
                if not duration:
                    await self.guard.confirm(key, self.MODULE, state.request_id, "started")
                    state.phase, state.paused_reason = "PAUSED", "挖矿已开始但回执缺少时长；不会重发"
                else:
                    await self.guard.confirm(key, self.MODULE, state.request_id, "started")
                    state.phase = "WAITING"
                    state.deadline_ts = time.time() + int(duration.group(1)) + self.extra_wait_sec
                    state.last_result = "挖矿进行中"
            elif success:
                if state.request_id:
                    await self.guard.confirm(key, self.MODULE, state.request_id, "success")
                state.mined_count += 1
                state.last_balance = self._amount(success.group(2))
                state.last_result = f"成功采集 {self._amount(success.group(1))}"
                state.phase, state.next_action_ts = "PENDING", time.time() + self.action_delay_sec
                state.request_id = ""
                state.deadline_ts = 0.0
            else:
                return False
        if message_id:
            state.processed_reply_ids = (state.processed_reply_ids + [str(message_id)])[-50:]
        await self._set(key, state)
        return True

    @staticmethod
    def _amount(value: str) -> int:
        value = str(value).replace(",", "").strip()
        for suffix, factor in (("京", 10**16), ("兆", 10**12), ("亿", 10**8), ("万", 10**4)):
            if value.endswith(suffix):
                return int(float(value[:-1]) * factor)
        return int(float(value))
