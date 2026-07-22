# 模块：宗门任务
from __future__ import annotations
import asyncio
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

TASK_MAP = {
    "密令": "宗门密令",
    "除魔": "除魔令",
    "仙丹": "九转仙丹",
    "疏财": "仗义疏财",
    "红尘": "坊市通告",
}


def _phase_label(phase: str) -> str:

    mapping = {
        "IDLE": "未启动",
        "SLEEPING": "等待中",
        "PROBING": "查询中",
        "WAITING_REFRESH": "等待刷新",
        "REFRESHING": "刷新中",
        "WORKING": "任务进行中",
        "WAITING_HP": "等待气血恢复",
    }
    return mapping.get(str(phase or "").strip(), "运行中")

@dataclass
class SectState:
    enabled: bool = False
    daily_hour: int = 6
    daily_minute: int = 30
    tasks: Dict[str, bool] = field(default_factory=lambda: {k: False for k in TASK_MAP})

    phase: str = "IDLE"
    wake_at_ts: float = 0.0
    next_action_ts: float = 0.0
    pending_action: str = ""
    retry_count: int = 0
    paused_reason: str = ""

    hp_rest_start_ts: float = 0.0
    hp_cultivate_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SectState":
        if not d: return cls()
        inst = cls()
        for k in cls.__annotations__:
            if k in d:
                setattr(inst, k, d[k])
        for k in TASK_MAP:
            if k not in inst.tasks:
                inst.tasks[k] = False
        return inst

RE_SECT_FINISH = re.compile(r"恭喜道友完成宗门任务")
RE_SECT_LIMIT = re.compile(r"已完成4次，今日无法再获取宗门任务了|今日无法再获取宗门任务了")

RE_SECT_LOW_HP = re.compile(r"状态欠佳|没过两招就力不从心|浪费了一次出门机会|不扣你任务次数了")

class SectController:
    def __init__(self, store: JsonStore, official_qq: str, config: Optional[Dict[str, Any]] = None, logger=None):
        self.store = store
        self.official_qq = official_qq
        self.log = logger
        self.config = dict(config or {})

        self.default_daily_hour, self.default_daily_minute = self._parse_daily_time(
            self.config.get("daily_start_time", "06:30")
        )
        configured_tasks = self.config.get("default_enabled_tasks", [])
        enabled_tasks = {
            str(task).strip()
            for task in configured_tasks
            if str(task).strip() in TASK_MAP
        } if isinstance(configured_tasks, list) else set()
        self.default_tasks = {task: task in enabled_tasks for task in TASK_MAP}
        self.response_timeout_sec = max(10, int(self.config.get("response_timeout_sec", 120)))
        self.max_response_retries = max(1, int(self.config.get("max_response_retries", 3)))
        self.task_refresh_delay_sec = max(1, int(self.config.get("task_refresh_delay_sec", 70)))
        self.hp_recovery_min_sec = max(1, int(self.config.get("hp_recovery_min_sec", 180)))
        self.hp_recovery_cultivate_count = max(
            1, int(self.config.get("hp_recovery_cultivate_count", 3))
        )

        self.cultivate_ref = None

    @staticmethod
    def _parse_daily_time(value: Any) -> tuple[int, int]:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{1,2})\s*", str(value or ""))
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        return 6, 30

    def _new_state(self) -> SectState:
        return SectState(
            daily_hour=self.default_daily_hour,
            daily_minute=self.default_daily_minute,
            tasks=dict(self.default_tasks),
        )

    def bind_cultivate(self, cultivate):

        self.cultivate_ref = cultivate

    def _info(self, msg: str):
        if self.log: self.log.info(msg)

    async def _get(self, key: str) -> SectState:
        data = await self.store.get(f"sect:{key}")
        return SectState.from_dict(data) if data else self._new_state()

    async def _set(self, key: str, st: SectState) -> None:
        await self.store.set(f"sect:{key}", st.to_dict())

    def _now_beijing(self) -> datetime:
        return datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()

    def _next_daily_ts(self, hour: int, minute: int) -> float:
        now = self._now_beijing()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()


    async def cmd_enable(self, key: str, send_cb) -> str:
        st = await self._get(key)
        st.enabled = True
        st.phase = "WAITING_RECEIVE"
        st.pending_action = "receive"
        st.retry_count = 0
        st.paused_reason = ""
        st.next_action_ts = time.time() + self.response_timeout_sec
        await self._set(key, st)
        await send_cb(f"@{self.official_qq} 宗门任务接取")
        return ("✅ 已开启宗门任务。\n"
                "📌 会在悬赏/秘境空闲后执行今日任务。\n"
                f"⏰ 若小小暂无有效回执，将于 {fmt_ts(st.next_action_ts)} 兜底重试。")

    async def cmd_disable(self, key: str) -> str:
        st = await self._get(key)
        st.enabled = False
        st.phase = "IDLE"
        await self._set(key, st)
        return "🛑 已关闭宗门任务。"

    async def cmd_set_time(self, key: str, time_str: str) -> str:
        m = re.match(r"(\d{1,2})[.:：](\d{1,2})", time_str)
        if m:
            h, m_min = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= m_min <= 59:
                st = await self._get(key)
                st.daily_hour = h
                st.daily_minute = m_min
                await self._set(key, st)
                next_ts = self._next_daily_ts(h, m_min)
                return f"✅ 宗门任务执行时间已设置为每日 {h:02d}:{m_min:02d} (北京时间)\n⏰ 下次宗门任务时间约：{fmt_ts(next_ts)}"
        return "❌ 格式错误，请使用如 18.00 或 07.30 的格式"

    async def cmd_status(self, key: str) -> str:
        st = await self._get(key)
        enabled_str = "✅开启" if st.enabled else "🛑关闭"
        tasks_on = [k for k, v in st.tasks.items() if v]
        tasks_str = "、".join(tasks_on) if tasks_on else "无 (⚠️请至少开启一项目标)"
        next_ts = st.wake_at_ts or st.next_action_ts
        return (f"📊 【宗门任务状态】\n"
                f"状态：{enabled_str}\n"
                f"任务进度：{_phase_label(st.phase)}\n"
                f"执行时间：每日 {st.daily_hour:02d}:{st.daily_minute:02d}\n"
                f"下一次动作：{fmt_ts(next_ts)}\n"
                f"已开启目标：{tasks_str}")

    async def cmd_toggle_task(self, key: str, task_args: str, enable: bool) -> str:
        st = await self._get(key)
        changed = []
        for word in task_args.split():
            if word in TASK_MAP:
                st.tasks[word] = enable
                changed.append(word)
        if not changed:
            return "❌ 未识别到宗门任务关键字（除魔/密令/仙丹/疏财/红尘）"
        await self._set(key, st)
        action = "开启" if enable else "关闭"
        return f"✅ 已{action}接取目标：{'、'.join(changed)}"


    async def _enter_waiting_hp(self, key: str, st: SectState, send_cb):

        st.phase = "WAITING_HP"
        st.hp_rest_start_ts = time.time()
        st.hp_cultivate_count = 0
        st.next_action_ts = time.time() + 10
        await self._set(key, st)
        self._info(f"[sect] {key} 气血不足，暂停宗门任务，进入休养")
        recover_hint_ts = time.time() + self.hp_recovery_min_sec
        await send_cb(f"⚠️ 卡比提醒：气血不足，宗门任务已暂停，正在回血；预计最早 {fmt_ts(recover_hint_ts)} 后继续。")


        if self.cultivate_ref is not None:
            try:
                await self.cultivate_ref.request_rest(key, send_cb)
            except Exception as e:
                self._info(f"[sect] 通知 cultivate 回血失败: {e}")

    async def _is_hp_recovered(self, key: str, st: SectState) -> bool:

        if self.cultivate_ref is None:

            return time.time() - st.hp_rest_start_ts >= self.hp_recovery_min_sec

        cult_st = await self.cultivate_ref._get(key)
        mode = cult_st.mode

        if mode in ("闭关", "宗门闭关"):
            return time.time() - st.hp_rest_start_ts >= self.hp_recovery_min_sec
        elif mode == "修炼":
            return st.hp_cultivate_count >= self.hp_recovery_cultivate_count
        else:

            return time.time() - st.hp_rest_start_ts >= self.hp_recovery_min_sec


    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled or st.phase == "SLEEPING":
            return


        if RE_SECT_LOW_HP.search(text) and st.phase in {"WAITING_RECEIVE", "WAITING_COMPLETE", "WAITING_REFRESH"}:
            if st.phase != "WAITING_HP":
                await self._enter_waiting_hp(key, st, send_cb)
            return


        if st.phase == "WAITING_HP":
            if "本次修炼增加" in text:
                st.hp_cultivate_count += 1
                await self._set(key, st)
                self._info(f"[sect] {key} 回血中修炼计数 {st.hp_cultivate_count}/{self.hp_recovery_cultivate_count}")
            return


        if RE_SECT_LIMIT.search(text):
            st.phase = "SLEEPING"
            st.wake_at_ts = self._next_daily_ts(st.daily_hour, st.daily_minute)
            st.next_action_ts = 0.0
            await self._set(key, st)


            if self.cultivate_ref is not None:
                try:
                    cult_st = await self.cultivate_ref._get(key)
                    if cult_st.mode and not cult_st.is_resting and cult_st.suspended_for_activity:
                        self._info(f"[sect] {key} 检测到宗门任务已达上限，恢复闭关/休息态")
                        await self.cultivate_ref.request_rest(key, send_cb)
                except Exception as e:
                    self._info(f"[sect] 通知 cultivate 归位失败: {e}")
            return


        if RE_SECT_FINISH.search(text) and st.phase == "WAITING_COMPLETE":


            st.phase = "WAITING_RECEIVE"
            st.pending_action = "receive"
            st.retry_count = 0
            st.wake_at_ts = 0.0
            st.next_action_ts = time.time() + self.response_timeout_sec
            await self._set(key, st)
            self._info(f"[sect] {key} 宗门任务完成，继续接取下一轮")
            await send_cb(f"@{self.official_qq} 宗门任务接取")
            return


        found_task = None
        for short_name, full_name in TASK_MAP.items():
            if full_name in text:
                found_task = short_name
                break

        if found_task and st.phase in {"WAITING_RECEIVE", "WAITING_REFRESH"}:
            if st.tasks.get(found_task, False):

                st.phase = "WAITING_COMPLETE"
                st.pending_action = "complete"
                st.retry_count = 0
                st.next_action_ts = time.time() + self.response_timeout_sec
                await self._set(key, st)
                self._info(f"[sect] {key} 命中目标「{found_task}」，正在完成")
                await send_cb(f"@{self.official_qq} 宗门任务完成")
            else:

                st.phase = "WAITING_REFRESH_DELAY"
                st.pending_action = ""
                st.next_action_ts = time.time() + self.task_refresh_delay_sec
                await self._set(key, st)
                self._info(f"[sect] {key} 任务「{found_task}」未开启，70s后刷新")
            return


    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        now = time.time()
        updated = False


        if st.phase == "WAITING_HP":
            if st.next_action_ts and now >= st.next_action_ts:
                if await self._is_hp_recovered(key, st):

                    st.phase = "WAITING_RECEIVE"
                    st.pending_action = "receive"
                    st.retry_count = 0
                    st.hp_rest_start_ts = 0.0
                    st.hp_cultivate_count = 0
                    st.next_action_ts = now + 120
                    await self._set(key, st)
                    self._info(f"[sect] {key} 气血已恢复，继续宗门任务")

                    if self.cultivate_ref is not None:
                        try:
                            await self.cultivate_ref.request_idle(key, send_cb)
                        except Exception:
                            pass
                    await asyncio.sleep(2.0)
                    await send_cb(f"@{self.official_qq} 宗门任务接取")
                else:

                    st.next_action_ts = now + 10
                    await self._set(key, st)
            return

        if st.phase == "SLEEPING":
            if st.wake_at_ts and now >= st.wake_at_ts:
                st.phase = "WAITING_RECEIVE"
                st.pending_action = "receive"
                st.retry_count = 0
                st.wake_at_ts = 0.0
                st.next_action_ts = now + self.response_timeout_sec
                updated = True
                self._info(f"[sect] {key} 到达预定时间，开启今日宗门任务")
                await send_cb("⏰ 已到宗门任务时间，正在接取宗门任务。")
                await send_cb(f"@{self.official_qq} 宗门任务接取")

        elif st.phase == "WAITING_REFRESH_DELAY":
            if st.next_action_ts and now >= st.next_action_ts:
                st.phase = "WAITING_REFRESH"
                st.pending_action = "refresh"
                st.retry_count = 0
                st.next_action_ts = now + self.response_timeout_sec
                updated = True
                await send_cb(f"@{self.official_qq} 宗门任务刷新")


        elif st.phase in {"WAITING_RECEIVE", "WAITING_REFRESH", "WAITING_COMPLETE"}:
            if st.next_action_ts and now >= st.next_action_ts:
                commands = {
                    "receive": "宗门任务接取",
                    "refresh": "宗门任务刷新",
                    "complete": "宗门任务完成",
                }
                if st.retry_count >= self.max_response_retries:
                    st.phase = "PAUSED"
                    st.paused_reason = f"{st.pending_action} 连续 {st.retry_count} 次未收到回执"
                else:
                    st.retry_count += 1
                    st.next_action_ts = now + self.response_timeout_sec
                    await send_cb(f"@{self.official_qq} {commands[st.pending_action]}")
                updated = True

        if updated:
            await self._set(key, st)
