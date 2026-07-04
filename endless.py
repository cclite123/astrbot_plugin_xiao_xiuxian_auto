# 模块：自动无尽妖塔
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    from .storage import JsonStore
    from .time_utils import fmt_ts
except ImportError:
    from storage import JsonStore
    from time_utils import fmt_ts


RE_MP = re.compile(r"(?:\[?真元\]?(?:\([^)]+\))?)\s*[:：]\s*(\d+(?:\.\d+)?)\s*%")
RE_CHALLENGE_OK = re.compile(r"踏破星河.*?成就无上", re.S)
RE_CHALLENGE_FAIL = re.compile(r"道友回家再练练")


def _phase_label(phase: str) -> str:
    mapping = {
        "IDLE": "未启动",
        "READY": "准备中",
        "CHECKING_MP": "检测真元中",
        "WAITING_CHALLENGE": "等待挑战回执",
        "RESTING": "宗门闭关恢复真元",
    }
    return mapping.get(str(phase or "").strip(), "运行中")


def _format_limit(done_count: int, target_count: int) -> str:
    if target_count > 0:
        return f"{done_count}/{target_count}"
    return f"{done_count}/无限"


@dataclass
class EndlessState:
    enabled: bool = False
    phase: str = "IDLE"
    target_count: int = 0
    done_count: int = 0
    next_action_ts: float = 0.0
    wake_at_ts: float = 0.0
    pending_action: str = ""
    failure_count: int = 0
    check_mp_enabled: bool = True
    mp_threshold: int = 600
    last_mp: float = -1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "EndlessState":
        if not d:
            return cls()
        inst = cls()
        for key in cls.__annotations__:
            if key in d:
                setattr(inst, key, d[key])
        return inst


class EndlessTowerController:
    def __init__(self, store: JsonStore, official_qq: str, config: Optional[Dict[str, Any]] = None, logger=None):
        cfg = dict(config or {})
        self.store = store
        self.official_qq = official_qq
        self.log = logger
        self.module_enabled = bool(cfg.get("enabled", True))
        self.default_mp_check_enabled = bool(cfg.get("mp_check_enabled", True))
        self.default_mp_threshold = self._clamp_threshold(cfg.get("mp_threshold", 600))
        self.rest_duration_sec = max(1.0, float(cfg.get("rest_duration_sec", 430.0)))
        self.action_delay_sec = max(0.0, float(cfg.get("action_delay_sec", 1.0)))
        self.status_timeout_sec = max(5.0, float(cfg.get("status_timeout_sec", 20.0)))
        self.challenge_timeout_sec = max(5.0, float(cfg.get("challenge_timeout_sec", 60.0)))
        self.max_failures = max(1, int(cfg.get("max_failures", 3)))

    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    @staticmethod
    def _skey(key: str) -> str:
        return f"endless:{key}"

    @staticmethod
    def _clamp_threshold(value: Any) -> int:
        try:
            num = int(float(value))
        except Exception:
            num = 600
        return max(0, min(9999, num))

    async def _get(self, key: str) -> EndlessState:
        raw = await self.store.get(self._skey(key))
        st = EndlessState.from_dict(raw)
        if not isinstance(raw, dict):
            st.check_mp_enabled = self.default_mp_check_enabled
            st.mp_threshold = self.default_mp_threshold
        else:
            if "check_mp_enabled" not in raw:
                st.check_mp_enabled = self.default_mp_check_enabled
            if "mp_threshold" not in raw:
                st.mp_threshold = self.default_mp_threshold
        st.mp_threshold = self._clamp_threshold(st.mp_threshold)
        return st

    async def _set(self, key: str, st: EndlessState) -> None:
        await self.store.set(self._skey(key), st.to_dict())

    def _parse_mp(self, text: str) -> Optional[float]:
        matches = RE_MP.findall(str(text or ""))
        if not matches:
            return None
        try:
            return float(matches[-1])
        except Exception:
            return None

    async def cmd_enable(self, key: str, arg: str, send_cb) -> str:
        if not self.module_enabled:
            return "🛑 自动无尽妖塔模块已在配置中关闭。"
        target_count = 0
        arg = str(arg or "").strip()
        if arg:
            try:
                target_count = int(arg)
            except Exception:
                return "❌ 次数格式错误，请使用：开启自动无尽 或 开启自动无尽 100"
            if target_count <= 0:
                return "❌ 挑战次数需为正整数；不填次数表示无限挑战。"

        st = await self._get(key)
        st.enabled = True
        st.phase = "READY"
        st.target_count = target_count
        st.done_count = 0
        st.next_action_ts = time.time() + self.action_delay_sec
        st.wake_at_ts = 0.0
        st.pending_action = ""
        st.failure_count = 0
        await self._set(key, st)
        limit = f"{target_count} 次" if target_count > 0 else "无限挑战"
        mp_line = f"真元检测：{'开启' if st.check_mp_enabled else '关闭'}"
        if st.check_mp_enabled:
            mp_line += f"（阈值 {st.mp_threshold}%）"
        return (
            f"✅ 已开启自动无尽妖塔：{limit}\n"
            f"{mp_line}\n"
            f"⏰ 首次动作：约 {fmt_ts(st.next_action_ts)}"
        )

    async def cmd_disable(self, key: str) -> str:
        st = await self._get(key)
        st.enabled = False
        st.phase = "IDLE"
        st.next_action_ts = 0.0
        st.wake_at_ts = 0.0
        st.pending_action = ""
        st.failure_count = 0
        await self._set(key, st)
        return "🛑 已关闭自动无尽妖塔。"

    async def cmd_enable_mp_check(self, key: str) -> str:
        st = await self._get(key)
        st.check_mp_enabled = True
        await self._set(key, st)
        return f"✅ 已开启无尽真元检测，当前阈值：{st.mp_threshold}%"

    async def cmd_disable_mp_check(self, key: str) -> str:
        st = await self._get(key)
        st.check_mp_enabled = False
        await self._set(key, st)
        return "🛑 已关闭无尽真元检测。"

    async def cmd_set_mp_threshold(self, key: str, arg: str) -> str:
        arg = str(arg or "").strip()
        if not re.fullmatch(r"\d{1,4}", arg):
            return "❌ 真元检测数值需为 0-9999 内数字，例如：设置无尽真元检测 600"
        threshold = int(arg)
        if threshold < 0 or threshold > 9999:
            return "❌ 真元检测数值需在 0-9999 范围内。"
        st = await self._get(key)
        st.mp_threshold = threshold
        await self._set(key, st)
        return f"✅ 无尽真元检测阈值已设置为：{threshold}%"

    async def cmd_status(self, key: str) -> str:
        st = await self._get(key)
        next_ts = st.wake_at_ts if st.phase == "RESTING" else st.next_action_ts
        last_mp = "未知" if st.last_mp < 0 else f"{st.last_mp:g}%"
        return (
            "📊【自动无尽妖塔状态】\n"
            f"状态：{'✅开启' if st.enabled else '🛑关闭'}\n"
            f"阶段：{_phase_label(st.phase)}\n"
            f"进度：{_format_limit(st.done_count, st.target_count)}\n"
            f"真元检测：{'✅开启' if st.check_mp_enabled else '🛑关闭'}，阈值：{st.mp_threshold}%\n"
            f"最近真元：{last_mp}\n"
            f"下一动作：{fmt_ts(next_ts)}"
        )

    async def _send_challenge(self, key: str, st: EndlessState, send_cb) -> None:
        st.phase = "WAITING_CHALLENGE"
        st.pending_action = "challenge"
        st.next_action_ts = time.time() + self.challenge_timeout_sec
        await self._set(key, st)
        if send_cb:
            await send_cb(f"@{self.official_qq} 挑战无尽妖塔")

    async def _handle_failure(self, key: str, st: EndlessState, send_cb, reason: str) -> None:
        st.failure_count += 1
        if st.failure_count >= self.max_failures:
            st.enabled = False
            st.phase = "IDLE"
            st.next_action_ts = 0.0
            st.wake_at_ts = 0.0
            st.pending_action = ""
            await self._set(key, st)
            if send_cb:
                await send_cb(f"🛑 自动无尽妖塔已停止：{reason}，连续异常 {st.failure_count}/{self.max_failures}。")
            return
        st.phase = "READY"
        st.pending_action = ""
        st.next_action_ts = time.time() + self.action_delay_sec
        await self._set(key, st)
        if send_cb:
            await send_cb(f"⚠️ 自动无尽妖塔：{reason}，稍后重试（{st.failure_count}/{self.max_failures}）。")

    async def tick(self, key: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        now = time.time()

        if st.target_count > 0 and st.done_count >= st.target_count:
            st.enabled = False
            st.phase = "IDLE"
            st.next_action_ts = 0.0
            await self._set(key, st)
            if send_cb:
                await send_cb(f"✅ 自动无尽妖塔已完成 {st.done_count}/{st.target_count} 次，已停止。")
            return

        if st.phase == "RESTING":
            if st.wake_at_ts and now >= st.wake_at_ts:
                st.phase = "READY"
                st.wake_at_ts = 0.0
                st.next_action_ts = now + self.action_delay_sec
                await self._set(key, st)
                if send_cb:
                    await send_cb(f"@{self.official_qq} 宗门出关")
            return

        if st.phase == "CHECKING_MP" and st.next_action_ts and now >= st.next_action_ts:
            await self._handle_failure(key, st, send_cb, "等待真元状态回执超时")
            return

        if st.phase == "WAITING_CHALLENGE" and st.next_action_ts and now >= st.next_action_ts:
            await self._handle_failure(key, st, send_cb, "等待无尽妖塔挑战回执超时")
            return

        if st.phase in ("IDLE", "READY") and (not st.next_action_ts or now >= st.next_action_ts):
            if st.check_mp_enabled:
                st.phase = "CHECKING_MP"
                st.pending_action = "status"
                st.next_action_ts = now + self.status_timeout_sec
                await self._set(key, st)
                if send_cb:
                    await send_cb(f"@{self.official_qq} 我的状态")
            else:
                await self._send_challenge(key, st, send_cb)

    async def on_official_text(self, key: str, text: str, send_cb) -> None:
        st = await self._get(key)
        if not st.enabled:
            return
        text = str(text or "")

        if RE_CHALLENGE_FAIL.search(text):
            st.enabled = False
            st.phase = "IDLE"
            st.next_action_ts = 0.0
            st.wake_at_ts = 0.0
            st.pending_action = ""
            await self._set(key, st)
            if send_cb:
                await send_cb(f"🛑 自动无尽妖塔：挑战失败（{_format_limit(st.done_count, st.target_count)}），已关闭。")
            return

        if RE_CHALLENGE_OK.search(text):
            st.done_count += 1
            st.failure_count = 0
            st.pending_action = ""
            if st.target_count > 0 and st.done_count >= st.target_count:
                st.enabled = False
                st.phase = "IDLE"
                st.next_action_ts = 0.0
                await self._set(key, st)
                if send_cb:
                    await send_cb(f"✅ 自动无尽妖塔已完成 {st.done_count}/{st.target_count} 次，已停止。")
                return
            st.phase = "READY"
            st.next_action_ts = time.time() + self.action_delay_sec
            await self._set(key, st)
            if send_cb:
                await send_cb(f"✅ 无尽妖塔挑战成功：{_format_limit(st.done_count, st.target_count)}，继续挑战。")
            return

        if st.phase == "CHECKING_MP":
            mp = self._parse_mp(text)
            if mp is None:
                if "真元" in text or "道号" in text or "气血" in text:
                    await self._handle_failure(key, st, send_cb, "真元状态解析失败")
                return
            st.last_mp = mp
            st.failure_count = 0
            if mp < st.mp_threshold:
                st.phase = "RESTING"
                st.pending_action = "rest"
                st.wake_at_ts = time.time() + self.rest_duration_sec
                st.next_action_ts = 0.0
                await self._set(key, st)
                if send_cb:
                    await send_cb(
                        f"⚠️ 真元不足：{mp:g}% < {st.mp_threshold}%，进入宗门闭关恢复。\n"
                        f"⏰ 预计出关：{fmt_ts(st.wake_at_ts)}"
                    )
                    await send_cb(f"@{self.official_qq} 宗门闭关")
                return
            await self._send_challenge(key, st, send_cb)
