# 模块：日常任务
from __future__ import annotations
import random
import re
import time
from dataclasses import dataclass, asdict
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
class RoutineState:

    signin_enabled: bool = False
    sign_phase: str = "IDLE"
    sign_wake_ts: float = 0.0
    sign_action_ts: float = 0.0


    pill_enabled: bool = False
    pill_phase: str = "IDLE"
    pill_wake_ts: float = 0.0
    pill_action_ts: float = 0.0
    pill_fail_count: int = 0


    mine_enabled: bool = False
    mine_phase: str = "IDLE"
    mine_wake_ts: float = 0.0
    mine_action_ts: float = 0.0





    farm_enabled: bool = False
    farm_phase: str = "IDLE"
    farm_wake_ts: float = 0.0
    farm_action_ts: float = 0.0
    farm_query_fail_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RoutineState":
        if not d:
            return cls()
        default = cls()
        return cls(**{k: d.get(k, getattr(default, k)) for k in cls.__annotations__})



RE_SIGN_OK   = re.compile(r"签到成功.*?获取.*?灵石", re.S)
RE_SIGN_DONE = re.compile(r"贪心的人是不会有好运的", re.S)

RE_PILL_OK   = re.compile(r"成功领取到丹药", re.S)
RE_PILL_DONE = re.compile(r"已经领取过了，不要贪心哦", re.S)





RE_MINE_OK = re.compile(
    r"(?:成功采集到\s*\d+(?:\.\d+)?[万亿兆京]?\s*灵矿石"
    r"|挥动矿[稿镐].*?灵矿石"
    r"|你提起矿镐.*?向着灵山走去"
    r"|离线收益.*?灵矿石)",
    re.S,
)


RE_FARM_OK = re.compile(r"道友本次采集成果")

RE_FARM_NOT_READY = re.compile(r"灵田灵气未满|尚需孕育")

RE_FARM_NEXT_TIME = re.compile(
    r"下次收成时间[：:]\s*(\d+(?:\.\d+)?)\s*小时后"
)


PILL_MAX_FAIL = 3


FARM_BUFFER_SEC = 60
FARM_QUERY_DELAY_SEC = 5
FARM_QUERY_MAX_FAIL = 3
FARM_FALLBACK_RETRY_SEC = 30 * 60
FARM_RETRY_SEC = 300




MINE_SEND_ONCE_SLEEP = True


class RoutineController:
    def __init__(self, store: JsonStore, official_qq: str, logger=None):
        self.store = store
        self.official_qq = official_qq
        self.log = logger

    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    async def _get(self, key: str) -> RoutineState:
        return RoutineState.from_dict(await self.store.get(f"routine:{key}"))

    async def _set(self, key: str, st: RoutineState) -> None:
        await self.store.set(f"routine:{key}", st.to_dict())


    def _next_daily_ts(self, hour: int, minute: int, allow_today: bool = True) -> float:

        now = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
        jitter = random.randint(-180, 180)
        target = now.replace(hour=hour, minute=minute,
                             second=0, microsecond=0) + timedelta(seconds=jitter)
        if not allow_today or target <= now:
            jitter = random.randint(-180, 180)
            target = (now + timedelta(days=1)).replace(
                hour=hour, minute=minute,
                second=0, microsecond=0) + timedelta(seconds=jitter)
        return target.timestamp()

    def _next_mine_ts(self) -> float:

        return time.time() + 5 * 3600 + random.randint(-600, 600)

    def _parse_farm_next_ts(self, text: str) -> Optional[float]:





        m = RE_FARM_NEXT_TIME.search(text)
        if not m:
            return None
        try:
            hours = float(m.group(1))
        except (TypeError, ValueError):
            return None
        if hours < 0:
            return None
        wait_sec = hours * 3600 + FARM_BUFFER_SEC
        return time.time() + wait_sec




    async def cmd_enable_signin(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.signin_enabled = True
        st.sign_phase = "WORKING"
        st.sign_action_ts = time.time() + 2
        await self._set(key, st)
        return ("✅ 已开启自动签到\n"
                f"⏰ 首次签到时间：约 {fmt_ts(st.sign_action_ts)}\n"
                "📅 后续每日 07:05 (±3分钟) 自动签到，完成后会提示下一次时间。")

    async def cmd_disable_signin(self, key: str) -> str:
        st = await self._get(key)
        st.signin_enabled = False
        st.sign_phase = "IDLE"
        await self._set(key, st)
        return "🛑 已关闭自动签到"




    async def cmd_enable_pill(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.pill_enabled = True
        st.pill_phase = "WORKING"
        st.pill_action_ts = time.time() + 2
        st.pill_fail_count = 0
        await self._set(key, st)
        return ("✅ 已开启自动领丹\n"
                f"⏰ 首次领丹时间：约 {fmt_ts(st.pill_action_ts)}\n"
                "📅 后续每日 07:10 (±3分钟) 自动领丹，完成后会提示下一次时间。")

    async def cmd_disable_pill(self, key: str) -> str:
        st = await self._get(key)
        st.pill_enabled = False
        st.pill_phase = "IDLE"
        st.pill_fail_count = 0
        await self._set(key, st)
        return "🛑 已关闭自动领丹"




    async def cmd_enable_mine(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.mine_enabled = True
        st.mine_phase = "WORKING"
        st.mine_action_ts = time.time() + 2
        await self._set(key, st)
        return ("✅ 已开启自动挖灵石\n"
                f"⏰ 首次挖灵石时间：约 {fmt_ts(st.mine_action_ts)}\n"
                "⛏️ 发送一次后会立即进入约 5 小时静默，防止重复刷屏；下次时间会单独提示。")

    async def cmd_disable_mine(self, key: str) -> str:
        st = await self._get(key)
        st.mine_enabled = False
        st.mine_phase = "IDLE"
        await self._set(key, st)
        return "🛑 已关闭自动挖矿"




    async def cmd_enable_farm(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.farm_enabled = True
        st.farm_phase = "WORKING"
        st.farm_action_ts = time.time() + 2
        st.farm_query_fail_count = 0
        await self._set(key, st)
        return ("✅ 已开启自动灵田结算\n"
                f"⏰ 首次灵田结算时间：约 {fmt_ts(st.farm_action_ts)}\n"
                "🌾 采集成功后会自动再发一次「灵田结算」查询下次收成时间，\n"
                "并在官方时间 +1 分钟 后再次执行。")

    async def cmd_disable_farm(self, key: str) -> str:
        st = await self._get(key)
        st.farm_enabled = False
        st.farm_phase = "IDLE"
        st.farm_wake_ts = 0.0
        st.farm_action_ts = 0.0
        st.farm_query_fail_count = 0
        await self._set(key, st)
        return "🛑 已关闭自动灵田结算"




    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        updated = False


        if st.signin_enabled and st.sign_phase == "WORKING":
            if RE_SIGN_OK.search(text):
                st.sign_action_ts = time.time() + 10
                self._info(f"[routine] {key} 签到成功，10s后重发")
                if send_cb:
                    await send_cb("✅ 签到成功，10 秒后再次确认签到状态。")
                updated = True
            elif RE_SIGN_DONE.search(text):
                st.sign_phase = "SLEEPING"
                st.sign_wake_ts = self._next_daily_ts(7, 5, allow_today=False)
                self._info(f"[routine] {key} 签到完成，静默至次日")
                if send_cb:
                    await send_cb(f"📅 签到流程完成，下次签到时间约：{fmt_ts(st.sign_wake_ts)}")
                updated = True


        if st.pill_enabled and st.pill_phase == "WORKING":
            if RE_PILL_OK.search(text):
                st.pill_fail_count = 0
                st.pill_action_ts = time.time() + 10
                self._info(f"[routine] {key} 领丹成功，10s后重发")
                if send_cb:
                    await send_cb("✅ 领丹成功，10 秒后再次确认领取状态。")
                updated = True
            elif RE_PILL_DONE.search(text):
                st.pill_fail_count = 0
                st.pill_phase = "SLEEPING"
                st.pill_wake_ts = self._next_daily_ts(7, 10, allow_today=False)
                self._info(f"[routine] {key} 领丹完成，静默至次日")
                if send_cb:
                    await send_cb(f"📅 领丹流程完成，下次领丹时间约：{fmt_ts(st.pill_wake_ts)}")
                updated = True




        if st.mine_enabled and st.mine_phase in ("WORKING", "SLEEPING"):
            if RE_MINE_OK.search(text):
                st.mine_phase = "SLEEPING"
                st.mine_wake_ts = self._next_mine_ts()
                st.mine_action_ts = 0.0
                self._info(f"[routine] {key} 挖矿已触发/完成，静默约 5 小时")
                if send_cb:
                    await send_cb(f"⛏️ 小小已确认挖灵石开始，下次挖灵石时间约：{fmt_ts(st.mine_wake_ts)}")
                updated = True



        if st.farm_enabled and st.farm_phase in ("WORKING", "QUERYING"):
            next_ts = self._parse_farm_next_ts(text)
            hit_ok = bool(RE_FARM_OK.search(text))
            hit_not_ready = bool(RE_FARM_NOT_READY.search(text))


            if next_ts is not None:
                st.farm_phase = "SLEEPING"
                st.farm_wake_ts = next_ts
                st.farm_action_ts = 0.0
                st.farm_query_fail_count = 0
                wait_min = (next_ts - time.time()) / 60.0
                self._info(
                    f"[routine] {key} 灵田解析到下次收成时间，"
                    f"约 {wait_min:.1f} 分钟后再次执行（+1分钟缓冲已包含）"
                )
                if send_cb:
                    await send_cb(f"🌾 灵田下次收取时间约：{fmt_ts(st.farm_wake_ts)}")
                updated = True


            elif hit_ok and st.farm_phase == "WORKING":

                st.farm_phase = "QUERYING"
                st.farm_action_ts = time.time() + FARM_QUERY_DELAY_SEC
                st.farm_query_fail_count = 0
                self._info(
                    f"[routine] {key} 灵田采集成功，{FARM_QUERY_DELAY_SEC}s 后再次"
                    f"发送「灵田结算」以查询下次收成时间"
                )
                if send_cb:
                    await send_cb(f"🌾 灵田已收取，{FARM_QUERY_DELAY_SEC} 秒后查询下次收成时间。")
                updated = True


            elif hit_not_ready:
                if st.farm_phase == "QUERYING":

                    st.farm_query_fail_count += 1
                    if st.farm_query_fail_count >= FARM_QUERY_MAX_FAIL:

                        st.farm_phase = "SLEEPING"
                        st.farm_wake_ts = time.time() + FARM_FALLBACK_RETRY_SEC
                        st.farm_action_ts = 0.0
                        st.farm_query_fail_count = 0
                        self._info(
                            f"[routine] {key} 查询连续 {FARM_QUERY_MAX_FAIL} 次未解析到时间，"
                            f"按兜底 {FARM_FALLBACK_RETRY_SEC//60} 分钟后再试"
                        )
                        if send_cb:
                            await send_cb(f"🌾 灵田未解析到准确时间，已使用兜底计划：约 {fmt_ts(st.farm_wake_ts)} 再试。")
                    else:

                        st.farm_action_ts = time.time() + FARM_QUERY_DELAY_SEC
                        self._info(
                            f"[routine] {key} 查询阶段未解析到时间（{st.farm_query_fail_count}/"
                            f"{FARM_QUERY_MAX_FAIL}），{FARM_QUERY_DELAY_SEC}s 后再次尝试"
                        )
                        if send_cb:
                            await send_cb(f"🌾 灵田暂未解析到下次时间，{FARM_QUERY_DELAY_SEC} 秒后再次查询。")
                else:

                    st.farm_phase = "SLEEPING"
                    st.farm_wake_ts = time.time() + FARM_FALLBACK_RETRY_SEC
                    st.farm_action_ts = 0.0
                    st.farm_query_fail_count = 0
                    self._info(
                        f"[routine] {key} 灵田未成熟但未解析到时间，"
                        f"{FARM_FALLBACK_RETRY_SEC//60} 分钟后重试"
                    )
                    if send_cb:
                        await send_cb(f"🌾 灵田未成熟，暂未拿到准确下次时间；约 {fmt_ts(st.farm_wake_ts)} 重试。")
                updated = True

        if updated:
            await self._set(key, st)




    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        now = time.time()
        updated = False


        if st.signin_enabled:
            if st.sign_phase == "SLEEPING" and now >= st.sign_wake_ts:
                st.sign_phase = "WORKING"
                st.sign_action_ts = now
                updated = True

            if st.sign_phase == "WORKING" and now >= st.sign_action_ts:
                st.sign_action_ts = now + 10
                updated = True
                if updated:
                    await self._set(key, st)
                    updated = False
                await send_cb(f"@{self.official_qq} 修仙签到")
                await send_cb("📌 已发送修仙签到，完成后会提示下次签到时间。")


        if st.pill_enabled:
            if st.pill_phase == "SLEEPING" and now >= st.pill_wake_ts:
                st.pill_phase = "WORKING"
                st.pill_action_ts = now
                st.pill_fail_count = 0
                updated = True

            if st.pill_phase == "WORKING" and now >= st.pill_action_ts:
                if st.pill_fail_count >= PILL_MAX_FAIL:
                    st.pill_enabled = False
                    st.pill_phase = "IDLE"
                    st.pill_fail_count = 0
                    updated = True
                    if updated:
                        await self._set(key, st)
                        updated = False
                    await send_cb("卡比提醒：请检查是否符合领取条件！")
                    self._info(f"[routine] {key} 领丹连续失败 {PILL_MAX_FAIL} 次，已自动关闭")
                else:
                    st.pill_action_ts = now + 10
                    st.pill_fail_count += 1
                    updated = True
                    if updated:
                        await self._set(key, st)
                        updated = False
                    await send_cb(f"@{self.official_qq} 宗门丹药领取")
                    await send_cb("📌 已发送宗门丹药领取，完成后会提示下次领丹时间。")


        if st.mine_enabled:
            if st.mine_phase == "SLEEPING" and now >= st.mine_wake_ts:
                st.mine_phase = "WORKING"
                st.mine_action_ts = now
                updated = True

            if st.mine_phase == "WORKING" and now >= st.mine_action_ts:




                st.mine_phase = "SLEEPING"
                st.mine_wake_ts = self._next_mine_ts()
                st.mine_action_ts = 0.0
                updated = True
                if updated:
                    await self._set(key, st)
                    updated = False
                await send_cb(f"@{self.official_qq} 挖灵石")
                await send_cb(f"⛏️ 已发送挖灵石，下次挖灵石时间约：{fmt_ts(st.mine_wake_ts)}")
                self._info(f"[routine] {key} 已发送一次挖灵石，进入约 5 小时静默，避免重复刷屏")


        if st.farm_enabled:

            if st.farm_phase == "SLEEPING" and now >= st.farm_wake_ts:
                st.farm_phase = "WORKING"
                st.farm_action_ts = now
                st.farm_query_fail_count = 0
                updated = True


            if st.farm_phase == "WORKING" and now >= st.farm_action_ts:
                st.farm_action_ts = now + FARM_RETRY_SEC
                updated = True
                if updated:
                    await self._set(key, st)
                    updated = False
                await send_cb(f"@{self.official_qq} 灵田结算")
                await send_cb("🌾 已发送灵田结算，若收取成功会继续提示下次收取时间。")


            elif st.farm_phase == "QUERYING" and now >= st.farm_action_ts:

                st.farm_action_ts = now + FARM_RETRY_SEC
                updated = True
                if updated:
                    await self._set(key, st)
                    updated = False
                await send_cb(f"@{self.official_qq} 灵田结算")
                await send_cb("🌾 已发送灵田时间查询，解析成功后会提示下次收取时间。")

        if updated:
            await self._set(key, st)