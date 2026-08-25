# 模块：秘境任务
from __future__ import annotations
import asyncio
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING_TZ = None

from .storage import JsonStore
from .time_utils import fmt_ts



@dataclass
class SecretState:
    enabled: bool = False

    phase: str = "IDLE"
    last_action_ts: float = 0.0
    settle_at_ts: float = 0.0
    next_step_ts: float = 0.0
    current_area: str = ""
    wake_at_ts: float = 0.0
    done_streak: int = 0
    daily_count: int = 0
    last_done_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SecretState":
        if not d:
            return cls()
        default_inst = cls()
        return cls(**{k: d.get(k, getattr(default_inst, k)) for k in cls.__annotations__})



RE_SECRET_START = re.compile(
    r"【秘境启程】.*?道友已踏入[:：]?\s*(?P<area>[^\s\n\r]+).*?"
    r"探索耗时[:：]\s*(?P<min>\d+(?:\.\d+)?)\s*分钟",
    re.S,
)
RE_SECRET_KEY = re.compile(r"秘境|万妖之域|东玄域|西玄域|狐鸣山|云梦泽|黑水湖|乱魔海")
# Official completion text varies depending on the bot version.  The
# completion reply is expected after the post-settlement probe, so all known
# forms are accepted as the terminal result for the second confirmation.
RE_ALREADY_DONE = re.compile(
    r"已经参加过本次秘境|今日(?:已|已经)完成秘境|今日秘境(?:已|已经)完成|"
    r"(?:本次|当前)秘境(?:探索|结算)?(?:已|已经)?(?:完成|成功)"
)
RE_BUSY_IN_SECRET = re.compile(r"正在秘境中|分身乏术")
RE_IN_PROGRESS = re.compile(
    r"进行中的[:：]?\s*(?P<area>[^\s,，]+)探索.*?预计\s*(?P<min>\d+(?:\.\d+)?)\s*分钟(?:[（(].*?[）)])?\s*后可结束",
    re.S,
)


class SecretController:


    SETTLE_GAP_SEC = 5
    VERIFY_GAP_SEC = 5
    POST_FINISH_DELAY_SEC = 60

    def __init__(
        self,
        store: JsonStore,
        official_qq: str,
        daily_start_time: str = "12:35",
        jitter_seconds: int = 600,
        logger=None,
    ):
        self.store = store
        self.official_qq = official_qq
        try:
            hh, mm = daily_start_time.split(":")
            self.daily_hour, self.daily_minute = int(hh), int(mm)
        except Exception:
            self.daily_hour, self.daily_minute = 12, 35
        self.jitter_seconds = max(0, int(jitter_seconds))
        self.log = logger

    def _info(self, msg: str) -> None:
        if self.log: self.log.info(msg)

    def _warn(self, msg: str) -> None:
        if self.log: self.log.warning(msg)

    @staticmethod
    def _skey(key: str) -> str:
        return f"secret:{key}"

    async def _get(self, key: str) -> SecretState:
        return SecretState.from_dict(await self.store.get(self._skey(key)))

    async def _set(self, key: str, st: SecretState) -> None:
        await self.store.set(self._skey(key), st.to_dict())

    def _now_beijing(self) -> datetime:
        return datetime.now(BEIJING_TZ) if BEIJING_TZ is not None else datetime.now()

    def _next_daily_run_ts(self, allow_today: bool = True) -> float:
        now = self._now_beijing()
        jitter = random.randint(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0
        target = now.replace(hour=self.daily_hour, minute=self.daily_minute,
                             second=0, microsecond=0) + timedelta(seconds=jitter)
        if not allow_today or target <= now:
            jitter = random.randint(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0
            target = (now + timedelta(days=1)).replace(
                hour=self.daily_hour, minute=self.daily_minute,
                second=0, microsecond=0) + timedelta(seconds=jitter)
        return target.timestamp()


    async def cmd_enable(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.enabled = True
        st.phase = "PROBING"
        st.done_streak = 0
        st.last_action_ts = time.time()
        await self._set(key, st)
        await send_cb(f"@{self.official_qq} 探索秘境")
        next_run = self._next_daily_run_ts(allow_today=False)
        next_dt = datetime.fromtimestamp(next_run, BEIJING_TZ) if BEIJING_TZ else datetime.fromtimestamp(next_run)
        return (f"✅ 已开启秘境\n"
                f"🔎 已立即探测当前秘境状态\n"
                f"⏰ 若今日已完成，将静默至次日（北京时间）约 {fmt_ts(next_run)} 执行\n"
                "🗺️ 进入秘境后会提示具体结算时间。")

    async def cmd_disable(self, key: str) -> str:
        st = await self._get(key)
        st.enabled = False
        st.phase = "IDLE"
        st.wake_at_ts = 0.0
        st.settle_at_ts = 0.0
        st.next_step_ts = 0.0
        await self._set(key, st)
        return "🛑 已关闭秘境"

    async def _enter_sleep_until_next_day(self, key: str, st: SecretState, send_cb, reason: str = ""):
        st.phase = "SLEEPING"
        st.wake_at_ts = self._next_daily_run_ts(allow_today=False)
        st.settle_at_ts = 0.0
        st.next_step_ts = 0.0
        st.current_area = ""
        st.done_streak = 0
        await self._set(key, st)


    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        if st.phase == "SLEEPING":
            return

        if RE_BUSY_IN_SECRET.search(text):
            if st.phase in ("PROBING", "QUERYING"):
                self._info(f"[secret] {key} 检测到正在秘境中（分身乏术），发送秘境结算查询剩余时间")
                st.last_action_ts = time.time()
                await self._set(key, st)
                await send_cb(f"@{self.official_qq} 秘境结算")
            return

        m_prog = RE_IN_PROGRESS.search(text)
        if m_prog:
            remaining_min = float(m_prog.group("min"))
            area = m_prog.group("area").strip()
            settle_at = time.time() + remaining_min * 60 + self.POST_FINISH_DELAY_SEC

            st.phase = "EXPLORING"
            st.current_area = area
            st.settle_at_ts = settle_at
            st.next_step_ts = 0.0
            st.last_action_ts = time.time()
            st.done_streak = 0
            await self._set(key, st)
            self._info(
                f"[secret] {key} 检测到秘境进行中「{area}」，"
                f"剩余 {remaining_min}min，将于 {datetime.fromtimestamp(settle_at):%H:%M:%S} 发送首次结算"
            )
            if send_cb:
                await send_cb(f"🗺️ 检测到秘境进行中：{area}\n⏰ 预计结算时间：{fmt_ts(st.settle_at_ts)}")
            return

        if RE_ALREADY_DONE.search(text):
            if st.phase not in ("VERIFYING", "PROBING", "SETTLING_1", "SETTLING_2", "EXPLORING"):
                return
            self._info(f"[secret] {key} 收到秘境完成回执")
            await self._enter_sleep_until_next_day(
                key, st, send_cb, reason=f"今日共完成 {st.daily_count} 轮")
            return

        m = RE_SECRET_START.search(text)
        if m and RE_SECRET_KEY.search(text):
            area = m.group("area").strip()
            minutes = float(m.group("min"))

            today = self._now_beijing().strftime("%Y-%m-%d")
            if st.last_done_date != today:
                st.daily_count = 0
                st.last_done_date = today

            st.phase = "EXPLORING"
            st.current_area = area
            st.settle_at_ts = time.time() + minutes * 60 + self.POST_FINISH_DELAY_SEC
            st.next_step_ts = 0.0
            st.last_action_ts = time.time()
            st.done_streak = 0
            await self._set(key, st)
            self._info(f"[secret] {key} 进入秘境「{area}」(第 {st.daily_count + 1} 轮)，耗时 {minutes}min")
            if send_cb:
                await send_cb(
                    f"🗺️ 秘境已开始：{area}（第 {st.daily_count + 1} 轮）\n"
                    f"⏰ 预计结算时间：{fmt_ts(st.settle_at_ts)}"
                )
            return


    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        now = time.time()

        if st.phase == "SLEEPING":
            if st.wake_at_ts and now >= st.wake_at_ts:
                st.phase = "PROBING"
                st.wake_at_ts = 0.0
                st.last_action_ts = now
                st.done_streak = 0
                await self._set(key, st)
                self._info(f"[secret] {key} 触发每日秘境流程")
                await send_cb(f"@{self.official_qq} 探索秘境")
            return

        if st.phase == "EXPLORING" and st.settle_at_ts and now >= st.settle_at_ts:
            st.daily_count += 1
            st.last_done_date = self._now_beijing().strftime("%Y-%m-%d")
            st.phase = "VERIFYING"
            st.settle_at_ts = 0.0
            st.next_step_ts = now + self.VERIFY_GAP_SEC
            st.current_area = ""
            st.done_streak = 0
            st.last_action_ts = now
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 秘境结算")
            return

        if st.phase == "VERIFYING" and st.next_step_ts and now >= st.next_step_ts:
            # The first settlement only closes the exploration timer.  A
            # second probe is required to receive the official
            # "已经参加过本次秘境" confirmation before sleeping and restoring
            # the previous cultivation mode.
            st.phase = "PROBING"
            st.next_step_ts = 0.0
            st.last_action_ts = now
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 探索秘境")
            return

        if st.phase == "VERIFYING" and st.last_action_ts and (now - st.last_action_ts) > 120:
            st.phase = "PROBING"
            st.next_step_ts = 0.0
            st.last_action_ts = now
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 探索秘境")
            return

        if st.phase == "PROBING" and st.last_action_ts and (now - st.last_action_ts) > 300:
            st.last_action_ts = now
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 探索秘境")
