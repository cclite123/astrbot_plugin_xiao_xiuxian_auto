# 模块：修炼闭关
from __future__ import annotations
import asyncio
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .storage import JsonStore


UNIT_MAP = {"": 1, "万": 1e4, "亿": 1e8, "兆": 1e12, "京": 1e16}
RE_HP = re.compile(
    r"气血\s*[:：]\s*"
    r"(?P<cur>\d+(?:\.\d+)?)(?P<u1>[万亿兆京]?)\s*/\s*"
    r"(?P<max>\d+(?:\.\d+)?)(?P<u2>[万亿兆京]?)"
)


RE_CULTIVATE_DONE = re.compile(r"本次修炼增加")

RE_REST_FULL = re.compile(r"气血已回满|真元已回满")



RE_EXIT_SUCCESS = re.compile(r"修为突破|出关捷报|闭关结算")


RE_ALREADY_IDLE = re.compile(
    r"道友现在什么都没干|现在什么都没干|什么都没干|"
    r"不在(?:宗门)?闭关|未在(?:宗门)?闭关|没有在(?:宗门)?闭关|"
    r"并未(?:在)?(?:宗门)?闭关|当前没有(?:宗门)?闭关|还没有闭关|无需出关"
)

RE_SECLUSION_ENTERED = re.compile(r"闭关入定")

RE_SECT_SECLUSION_ENTERED = re.compile(r"预计闭关时长")

RE_ALREADY_IN_SECLUSION = re.compile(r"小心走火入魔")

RE_SECLUSION_TOO_SHORT = re.compile(r"闭关时间过短")


MODE_CULTIVATE = "修炼"
MODE_SECLUSION = "闭关"
MODE_SECT_SECLUSION = "宗门闭关"
VALID_MODES = (MODE_CULTIVATE, MODE_SECLUSION, MODE_SECT_SECLUSION)


@dataclass
class CultivateState:
    mode: str = ""
    is_resting: bool = False
    hp_percent: float = 100.0
    hp_check_ts: float = 0.0
    hp_check_pending: bool = False
    suspended_for_activity: bool = False
    last_action_ts: float = 0.0
    secret_hp_check_pending: bool = False
    secret_hp_recovery: bool = False
    secret_hp_previous_mode: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "CultivateState":
        if not d: return cls()
        default = cls()
        return cls(**{k: d.get(k, getattr(default, k)) for k in cls.__annotations__})


def parse_hp_percent(text: str) -> Optional[float]:

    m = RE_HP.search(text)
    if not m: return None
    try:
        cur = float(m.group("cur")) * UNIT_MAP.get(m.group("u1"), 1)
        mx = float(m.group("max")) * UNIT_MAP.get(m.group("u2"), 1)
        if mx <= 0: return None
        return max(0.0, min(100.0, cur / mx * 100.0))
    except Exception:
        return None


class CultivateController:
    HP_FRESH_SEC = 60
    REST_FULL_WAIT_SEC = 300

    def __init__(self, store: JsonStore, official_qq: str, logger=None):
        self.store = store
        self.official_qq = official_qq
        self.log = logger

        self._pending_after_exit: Dict[str, List[str]] = {}

        self._rest_locks: Dict[str, asyncio.Lock] = {}

    def _info(self, msg: str):
        if self.log: self.log.info(msg)


    def queue_pending(self, key: str, text: str):

        if text not in self._pending_after_exit.setdefault(key, []):
            self._pending_after_exit[key].append(text)

    def pop_pending(self, key: str) -> list:

        return self._pending_after_exit.pop(key, [])

    async def _get(self, key: str) -> CultivateState:
        return CultivateState.from_dict(await self.store.get(f"cultivate:{key}"))

    async def _set(self, key: str, st: CultivateState):
        await self.store.set(f"cultivate:{key}", st.to_dict())


    async def cmd_enable(self, key: str, mode: str, send_cb) -> str:
        if mode not in VALID_MODES:
            return "❌ 模式错误"
        st = await self._get(key)

        if st.mode and st.mode != mode and st.is_resting:
            await self._send_exit(st.mode, send_cb)
        st.mode = mode
        st.suspended_for_activity = False
        st.last_action_ts = time.time()
        await self._set(key, st)



        await self._enter_rest(key, send_cb, force=True)
        return (f"✅ 已开启{mode}（活动完成后会回到{mode}状态）\n"
                f"🧘 已立即发送「{mode}」相关指令；该功能没有固定结算时间，以小小回复为准。\n"
                "📊 可发送「任务状态」查看当前修炼/闭关状态。")

    async def cmd_disable(self, key: str, mode: str, send_cb) -> str:
        st = await self._get(key)
        if st.mode != mode:
            return f"⚠️ 当前模式不是「{mode}」，无需关闭"

        if st.is_resting:
            await self._send_exit(mode, send_cb)
        st.mode = ""
        st.is_resting = False
        st.suspended_for_activity = False
        await self._set(key, st)
        self.pop_pending(key)
        return f"🛑 已关闭{mode}，后续不再恢复该状态。"

    async def cmd_check_hp(self, key: str, send_cb) -> str:
        await send_cb(f"@{self.official_qq} 我的状态")
        return "🔎 已发起气血查询，收到小小状态回复后会更新「任务状态」中的气血信息。"


    async def _send_enter(self, mode: str, send_cb):
        cmd_map = {MODE_CULTIVATE: "修炼", MODE_SECLUSION: "闭关", MODE_SECT_SECLUSION: "宗门闭关"}
        await send_cb(f"@{self.official_qq} {cmd_map[mode]}")

    async def _send_exit(self, mode: str, send_cb):
        exit_map = {MODE_CULTIVATE: None, MODE_SECLUSION: "出关", MODE_SECT_SECLUSION: "宗门出关"}
        exit_cmd = exit_map.get(mode)
        if exit_cmd:
            await send_cb(f"@{self.official_qq} {exit_cmd}")

    async def _enter_rest(self, key: str, send_cb, force: bool = False):
        lock = self._rest_locks.setdefault(key, asyncio.Lock())
        async with lock:
            st = await self._get(key)
            if not st.mode:
                return


            if (not force) and st.is_resting and not st.suspended_for_activity:
                return
            st.is_resting = True
            st.suspended_for_activity = False
            st.last_action_ts = time.time()

            if st.mode in (MODE_SECLUSION, MODE_SECT_SECLUSION):
                st.hp_check_ts = 0.0
            await self._set(key, st)
            await self._send_enter(st.mode, send_cb)


    async def request_idle(self, key: str, send_cb) -> bool:





        st = await self._get(key)
        if not st.mode:
            return True
        if st.mode == MODE_CULTIVATE:

            if st.is_resting:
                st.suspended_for_activity = True
                await self._set(key, st)
                return False
            return True


        if st.is_resting:
            if st.suspended_for_activity:
                return False
            await self._send_exit(st.mode, send_cb)
            st.suspended_for_activity = True
            st.last_action_ts = time.time()
            st.hp_check_ts = 0.0
            await self._set(key, st)
            return False
        return True

    async def request_rest(self, key: str, send_cb):






        st = await self._get(key)
        if not st.mode:
            return


        await self._enter_rest(key, send_cb, force=False)

    async def mark_activity_exit_requested(self, key: str):






        st = await self._get(key)
        if not st.mode:
            return
        was_resting = st.is_resting
        if st.mode not in (MODE_SECLUSION, MODE_SECT_SECLUSION):
            st.is_resting = False
        st.suspended_for_activity = True
        st.last_action_ts = time.time()
        if st.mode in (MODE_SECLUSION, MODE_SECT_SECLUSION) and was_resting:
            st.hp_check_ts = 0.0
        await self._set(key, st)

    async def is_busy(self, key: str) -> bool:

        st = await self._get(key)
        return st.mode == MODE_CULTIVATE and st.is_resting

    async def ensure_secret_entry_hp(self, key: str, send_cb, min_pct: float = 80.0) -> bool:
        """仅在进入秘境前检查游戏要求的最低气血。"""
        st = await self._get(key)
        now = time.time()

        if st.secret_hp_recovery:
            return False

        if now - st.hp_check_ts > self.HP_FRESH_SEC:
            await send_cb(f"@{self.official_qq} 我的状态")
            st.hp_check_ts = now
            st.hp_check_pending = True
            st.secret_hp_check_pending = True
            await self._set(key, st)
            return False

        if st.hp_check_pending:
            return False

        if st.hp_percent >= min_pct:
            return True

        await self._start_secret_hp_recovery(key, st, send_cb)
        return False

    async def _start_secret_hp_recovery(self, key: str, st: CultivateState, send_cb) -> None:
        st.secret_hp_recovery = True
        st.secret_hp_previous_mode = st.mode
        st.mode = MODE_CULTIVATE
        st.is_resting = False
        st.suspended_for_activity = True
        await self._set(key, st)
        await self._enter_rest(key, send_cb, force=True)

    async def _finish_secret_hp_recovery(self, key: str, st: CultivateState, send_cb) -> None:
        st.mode = st.secret_hp_previous_mode
        st.is_resting = False
        st.suspended_for_activity = bool(st.mode)
        st.secret_hp_check_pending = False
        st.secret_hp_recovery = False
        st.secret_hp_previous_mode = ""
        await self._set(key, st)

        pendings = self.pop_pending(key)
        for text in pendings:
            await asyncio.sleep(1.0)
            await send_cb(text)


    async def on_official_text(self, key: str, text: str, send_cb):
        st = await self._get(key)


        hp = parse_hp_percent(text)
        if hp is not None:
            st.hp_percent = hp
            st.hp_check_ts = time.time()
            st.hp_check_pending = False
            await self._set(key, st)
            self._info(f"[cultivate] {key} 气血更新：{hp:.1f}%")

            if st.secret_hp_check_pending:
                st.secret_hp_check_pending = False
                if hp >= 80.0:
                    await self._finish_secret_hp_recovery(key, st, send_cb)
                else:
                    await self._start_secret_hp_recovery(key, st, send_cb)
                return

            if st.secret_hp_recovery and hp >= 80.0:
                await self._finish_secret_hp_recovery(key, st, send_cb)
                return

            if st.secret_hp_recovery and not st.is_resting:
                await self._enter_rest(key, send_cb, force=True)
                return

        if not st.mode:
            return



        if RE_ALREADY_IDLE.search(text):
            st.is_resting = False
            st.hp_percent = 100.0
            st.hp_check_ts = time.time()


            pendings = self.pop_pending(key)
            st.suspended_for_activity = bool(pendings) or st.suspended_for_activity
            await self._set(key, st)
            self._info(f"[cultivate] {key} 检测到已处于空闲状态，准备执行后续指令")
            for t in pendings:
                await asyncio.sleep(1.0)
                await send_cb(t)
            return


        if RE_EXIT_SUCCESS.search(text):
            if st.mode in (MODE_SECLUSION, MODE_SECT_SECLUSION):
                st.is_resting = False
                st.hp_percent = 100.0
                st.hp_check_ts = time.time()
                await self._set(key, st)

                self._info(f"[cultivate] {key} 检测到出关成功（{st.mode}），准备执行后续指令")


                pendings = self.pop_pending(key)
                for t in pendings:
                    await asyncio.sleep(1.0)
                    await send_cb(t)
                return


        if RE_SECLUSION_ENTERED.search(text) and st.mode == MODE_SECLUSION:
            self._info(f"[cultivate] {key} 已确认进入闭关状态")
            return
        if RE_SECT_SECLUSION_ENTERED.search(text) and st.mode == MODE_SECT_SECLUSION:
            self._info(f"[cultivate] {key} 已确认进入宗门闭关状态")
            return

        # 关键词：小心走火入魔 → 用户已处于闭关/宗门闭关状态
        if RE_ALREADY_IN_SECLUSION.search(text):
            if st.mode in (MODE_SECLUSION, MODE_SECT_SECLUSION):
                st.is_resting = True
                st.suspended_for_activity = False
                st.last_action_ts = time.time()
                await self._set(key, st)
                self._info(f"[cultivate] {key} 检测到「小心走火入魔」，确认已处于{st.mode}状态")
            else:
                self._info(f"[cultivate] {key} 检测到「小心走火入魔」，但当前模式为「{st.mode}」，未更新闭关状态")
            return

        # 关键词：闭关时间过短 → 用户已出关
        if RE_SECLUSION_TOO_SHORT.search(text):
            if st.mode in (MODE_SECLUSION, MODE_SECT_SECLUSION):
                st.is_resting = False
                st.hp_percent = 100.0
                st.hp_check_ts = time.time()
                await self._set(key, st)
                self._info(f"[cultivate] {key} 检测到「闭关时间过短」，标记为已出关状态")

                pendings = self.pop_pending(key)
                for t in pendings:
                    await asyncio.sleep(1.0)
                    await send_cb(t)
            else:
                self._info(f"[cultivate] {key} 检测到「闭关时间过短」，但当前模式为「{st.mode}」，未更新状态")
            return


        if RE_CULTIVATE_DONE.search(text) and st.mode == MODE_CULTIVATE:
            if st.secret_hp_recovery:
                st.is_resting = False
                await self._set(key, st)
                if st.hp_percent >= 80.0:
                    await self._finish_secret_hp_recovery(key, st, send_cb)
                else:
                    await self._enter_rest(key, send_cb, force=True)
                return
            st.hp_percent = 100.0
            st.hp_check_ts = time.time()
            st.is_resting = False
            await self._set(key, st)

            pendings = self.pop_pending(key)
            if pendings:
                for t in pendings:
                    await asyncio.sleep(1.0)
                    await send_cb(t)
            elif not st.suspended_for_activity:
                await self._enter_rest(key, send_cb)
            return


    async def tick(self, key: str, send_cb):
        st = await self._get(key)
        if not st.mode: return

        if st.mode == MODE_CULTIVATE and not st.is_resting and not st.suspended_for_activity:
            if time.time() - st.last_action_ts > 90:
                await self._enter_rest(key, send_cb)
