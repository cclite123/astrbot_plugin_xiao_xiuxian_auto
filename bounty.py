# 模块：悬赏任务
from __future__ import annotations
import asyncio
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING_TZ = None

from .storage import JsonStore, make_key
from .time_utils import fmt_ts


TIER_PRICES: Dict[str, int] = {
    "黄阶下品": 10, "黄阶中品": 20, "黄阶上品": 40, "黄阶极品": 80,
    "玄阶下品": 60, "玄阶中品": 100, "玄阶上品": 160, "玄阶极品": 240,
    "地阶下品": 120, "地阶中品": 180, "地阶上品": 260, "地阶极品": 360,
    "人阶下品": 180, "人阶中品": 240, "人阶上品": 320, "人阶极品": 440,
    "天阶下品": 240, "天阶中品": 300, "天阶上品": 380, "天阶极品": 520,
    "仙阶下品": 320, "仙阶中品": 420, "仙阶上品": 560, "仙阶极品": 760,
    "九品药材": 50, "八品药材": 80, "七品药材": 130, "六品药材": 200,
    "五品药材": 300, "四品药材": 460, "三品药材": 700, "二品药材": 1100, "一品药材": 1800,
}



PRIORITY_SPECIAL_ITEMS: List[str] = [
    "五指拳心剑",
    "真龙九变",
    "坐忘论",
    "无瑕七绝剑",
]

def estimate_extra_value(extra: str) -> int:
    if not extra:
        return 0
    for tier in sorted(TIER_PRICES.keys(), key=len, reverse=True):
        if extra.startswith(tier) or tier in extra[:10]:
            return TIER_PRICES[tier]
    return 0


@dataclass
class BountyOption:
    index: int
    title: str
    success_rate: int
    minutes: int
    base_exp: int
    extra: str

    @property
    def effective_exp(self) -> int:
        return self.base_exp * 2 if self.success_rate >= 100 else self.base_exp

    @property
    def extra_value(self) -> int:
        return estimate_extra_value(self.extra)


@dataclass
class BountyState:
    enabled: bool = False
    strategy: str = "修为"
    phase: str = "IDLE"
    last_action_ts: float = 0.0
    settle_at_ts: float = 0.0
    current_title: str = ""
    wake_at_ts: float = 0.0
    stats: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "BountyState":
        if not d:
            return cls()
        default_inst = cls()
        return cls(**{k: d.get(k, getattr(default_inst, k)) for k in cls.__annotations__})



RE_NO_TASK = re.compile(r"当前未查询到道友的悬赏令信息")
RE_LIST_HEADER = re.compile(r"天机悬赏令")
RE_OPTION = re.compile(
    r"悬赏(?P<idx_zh>[壹贰叁])·(?P<title>.+?)\s*"
    r"[✅✓]?成功率[:：]\s*(?P<rate>\d+)%\s*"
    r"[⏳]?预计耗时[:：]\s*(?P<min>\d+)\s*分钟\s*"
    r"[✨]?基础奖励\s*(?P<exp>\d+)\s*修为\s*"
    r"[🎁]?额外机缘[:：]\s*(?P<extra>[^\n\r]+)",
    re.S,
)
RE_ACCEPT_OK = re.compile(
    r"悬赏令接取成功.*?【(?P<title>[^】]+)】.*?"
    r"(?:预计时间[:：]\s*(?P<min>\d+(?:\.\d+)?)\s*分钟)",
    re.S,
)
RE_RUNNING = re.compile(r"悬赏令进行中")
RE_DONE = re.compile(r"悬赏令结算\s*·\s*任务达成")
RE_USED_UP = re.compile(r"今日悬赏令刷新次数已用尽")
RE_REWARD_EXP = re.compile(r"增加修为\s*(\d+)")
RE_REWARD_EXTRA = re.compile(r"额外奖励[:：]\s*([^\n\r]+)")

_ZH_NUM = {"壹": 1, "贰": 2, "叁": 3, "一": 1, "二": 2, "三": 3}


def parse_options(text: str) -> List[BountyOption]:
    opts: List[BountyOption] = []
    for m in RE_OPTION.finditer(text):
        idx = _ZH_NUM.get(m.group("idx_zh"), 0)
        if not idx:
            continue
        opts.append(BountyOption(
            index=idx, title=m.group("title").strip(),
            success_rate=int(m.group("rate")), minutes=int(m.group("min")),
            base_exp=int(m.group("exp")), extra=m.group("extra").strip(),
        ))
    seen, uniq = set(), []
    for o in sorted(opts, key=lambda x: x.index):
        if o.index in seen:
            continue
        seen.add(o.index)
        uniq.append(o)
    return uniq


def choose_option(opts: List[BountyOption], strategy: str) -> Optional[BountyOption]:
    if not opts:
        return None



    for special in PRIORITY_SPECIAL_ITEMS:
        for o in opts:
            if special in (o.extra or "") or special in (o.title or ""):
                return o


    if strategy == "价值":
        return max(opts, key=lambda o: (o.extra_value, o.effective_exp, -o.minutes))
    if strategy == "耗时":
        return min(opts, key=lambda o: (o.minutes, -o.effective_exp))

    return max(opts, key=lambda o: (o.effective_exp, o.extra_value, -o.minutes))



class BountyController:
    def __init__(self, store: JsonStore, official_qq: str, default_strategy: str = "修为",
                 retry_when_running_sec: int = 30, post_finish_delay_sec: int = 30,
                 next_morning_hour: int = 8, daily_start_time: str = "08:30",
                 jitter_seconds: int = 600, logger=None, market_price=None):
        self.store = store
        self.official_qq = official_qq
        self.default_strategy = default_strategy
        self.retry_when_running_sec = retry_when_running_sec
        self.post_finish_delay_sec = post_finish_delay_sec
        self.next_morning_hour = next_morning_hour


        self.market_price = market_price
        try:
            hh, mm = daily_start_time.split(":")
            self.daily_hour, self.daily_minute = int(hh), int(mm)
        except Exception:
            self.daily_hour, self.daily_minute = 8, 30

        self.jitter_seconds = max(0, int(jitter_seconds))
        self.log = logger


    def _info(self, msg: str) -> None:
        if self.log: self.log.info(msg)

    def _warn(self, msg: str) -> None:
        if self.log: self.log.warning(msg)

    async def _get(self, key: str) -> BountyState:
        return BountyState.from_dict(await self.store.get(key))

    async def _set(self, key: str, st: BountyState) -> None:
        await self.store.set(key, st.to_dict())


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


    async def estimate_extra_value_dynamic(self, extra: str) -> int:







        extra = str(extra or "").strip()
        if not extra:
            return 0

        if self.market_price:
            try:
                price = await self.market_price.find_price_in_text(extra)
                if price is not None and int(price) > 0:
                    return int(price)
            except Exception as e:
                self._warn(f"[bounty] 获取坊市价格失败，回退内置估价：{e}")

        return estimate_extra_value(extra)

    async def choose_option_dynamic(self, opts: List[BountyOption], strategy: str) -> Optional[BountyOption]:

        if not opts:
            return None


        for special in PRIORITY_SPECIAL_ITEMS:
            for o in opts:
                if special in (o.extra or "") or special in (o.title or ""):
                    return o

        value_cache: Dict[int, int] = {}

        async def value_of(o: BountyOption) -> int:
            if o.index not in value_cache:
                value_cache[o.index] = await self.estimate_extra_value_dynamic(o.extra)
            return value_cache[o.index]

        scored = []
        for o in opts:
            scored.append((o, await value_of(o)))

        if strategy == "价值":
            return max(scored, key=lambda pair: (pair[1], pair[0].effective_exp, -pair[0].minutes))[0]
        if strategy == "耗时":
            return min(scored, key=lambda pair: (pair[0].minutes, -pair[0].effective_exp, -pair[1]))[0]

        return max(scored, key=lambda pair: (pair[0].effective_exp, pair[1], -pair[0].minutes))[0]


    async def cmd_enable(self, key: str, send_cb) -> str:





        st = await self._get(key)
        st.enabled = True
        st.phase = "PROBING"
        st.wake_at_ts = 0.0
        st.last_action_ts = time.time()
        await self._set(key, st)


        await send_cb(f"@{self.official_qq} 悬赏令查看")

        next_run = self._next_daily_run_ts(allow_today=False)
        next_dt = datetime.fromtimestamp(next_run, BEIJING_TZ) if BEIJING_TZ else datetime.fromtimestamp(next_run)
        return (f"✅ 已开启悬赏（策略：{st.strategy or self.default_strategy}）\n"
                f"🔎 已立即探测当前悬赏状态\n"
                f"⏰ 若今日已完成，将静默至次日（北京时间）约 {fmt_ts(next_run)} 执行\n"
                "🎯 接取成功后会提示具体结算时间。")

    async def cmd_disable(self, key: str) -> str:
        st = await self._get(key)
        st.enabled = False
        st.phase = "IDLE"
        st.wake_at_ts = 0.0
        st.settle_at_ts = 0.0
        await self._set(key, st)
        return "🛑 已关闭悬赏"

    async def cmd_set_strategy(self, key: str, strategy: str) -> str:
        if strategy not in ("修为", "价值", "耗时"):
            return "❌ 策略必须是：修为 / 价值 / 耗时"
        st = await self._get(key)
        st.strategy = strategy
        await self._set(key, st)
        return f"🎯 悬赏策略已切换为：{strategy}"

    async def cmd_stats(self, key: str) -> str:
        st = await self._get(key)
        today = self._now_beijing().strftime("%Y-%m-%d")
        daily = st.stats.get(today, {"exp": 0, "value": 0})
        return (f"📊 【今日悬赏统计】\n"
                f"📅 日期：{today}\n"
                f"✨ 累计获得修为：{daily['exp']}\n"
                f"💎 累计额外价值：{daily['value']}")


    async def _enter_sleep_until_next_day(self, key: str, st: BountyState, send_cb, reason: str = ""):
        st.phase = "SLEEPING"
        st.wake_at_ts = self._next_daily_run_ts(allow_today=False)
        st.settle_at_ts = 0.0
        st.current_title = ""
        await self._set(key, st)


    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return

        if st.phase == "SLEEPING":
            return

        self._info(f"[bounty-debug] 进入 on_official_text: phase={st.phase} text_head={text[:80]!r}")


        if RE_USED_UP.search(text):
            await self._enter_sleep_until_next_day(key, st, send_cb, reason="次数用尽")
            return


        if RE_NO_TASK.search(text):
            st.phase = "REFRESHING"
            st.last_action_ts = time.time()
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 悬赏令刷新")
            return


        if RE_LIST_HEADER.search(text):
            opts = parse_options(text)
            if len(opts) < 1:
                self._warn(f"[bounty-debug] 解析选项失败: {text[:100]}")
                return
            chosen = await self.choose_option_dynamic(opts, st.strategy or self.default_strategy)
            if not chosen:
                return
            st.phase = "CHOOSING"
            st.current_title = chosen.title
            await self._set(key, st)

            if any(s in (chosen.extra or "") or s in (chosen.title or "") for s in PRIORITY_SPECIAL_ITEMS):
                self._info(f"[bounty] 命中特殊机缘物品，优先抢取：编号{chosen.index}「{chosen.title}」 额外：{chosen.extra}")

            await send_cb(f"@{self.official_qq} 悬赏令接取{chosen.index}")
            return


        m_accept = RE_ACCEPT_OK.search(text)
        if m_accept:
            minutes = float(m_accept.group("min"))
            st.phase = "WORKING"
            st.current_title = m_accept.group("title").strip()
            st.settle_at_ts = time.time() + minutes * 60 + self.post_finish_delay_sec
            await self._set(key, st)
            if send_cb:
                await send_cb(
                    f"🎯 悬赏已接收：{st.current_title}\n"
                    f"⏰ 预计结算时间：{fmt_ts(st.settle_at_ts)}"
                )
            return


        if RE_DONE.search(text):
            today = self._now_beijing().strftime("%Y-%m-%d")
            exp_match = RE_REWARD_EXP.search(text)
            extra_match = RE_REWARD_EXTRA.search(text)
            exp_gained = int(exp_match.group(1)) if exp_match else 0
            extra_text = extra_match.group(1).strip() if extra_match else ""
            val_gained = await self.estimate_extra_value_dynamic(extra_text)

            if today not in st.stats:
                st.stats[today] = {"exp": 0, "value": 0}
            st.stats[today]["exp"] += exp_gained
            st.stats[today]["value"] += val_gained



            st.phase = "QUERYING"
            st.current_title = ""
            st.settle_at_ts = 0.0
            st.last_action_ts = time.time()
            st.wake_at_ts = 0.0
            await self._set(key, st)
            if send_cb:
                await send_cb(f"@{self.official_qq} 悬赏令查看")
            return


        if RE_RUNNING.search(text):
            st.phase = "WORKING"
            st.settle_at_ts = time.time() + self.retry_when_running_sec
            await self._set(key, st)
            if send_cb:
                await send_cb(f"⏳ 悬赏仍在进行中，将于 {fmt_ts(st.settle_at_ts)} 再次尝试结算。")
            return


    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        now = time.time()


        if st.phase == "SLEEPING":
            if st.wake_at_ts and now >= st.wake_at_ts:
                st.phase = "QUERYING"
                st.wake_at_ts = 0.0
                st.last_action_ts = now
                await self._set(key, st)
                self._info(f"[bounty] {key} 触发每日定时悬赏流程（含随机抖动）")
                await send_cb(f"@{self.official_qq} 悬赏令查看")
            return


        if st.phase == "WORKING" and st.settle_at_ts and now >= st.settle_at_ts:
            st.phase = "SETTLING"
            st.settle_at_ts = 0.0
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 悬赏令结算")
            return


        if st.phase in ("PROBING", "QUERYING", "REFRESHING", "CHOOSING") \
                and st.last_action_ts and (now - st.last_action_ts) > 300:
            st.last_action_ts = now
            await self._set(key, st)
            await send_cb(f"@{self.official_qq} 悬赏令查看")
