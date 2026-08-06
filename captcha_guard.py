from __future__ import annotations

from collections import OrderedDict
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    from openai import AsyncOpenAI
except ImportError:  # Keep the plugin loadable until the optional dependency is installed.
    AsyncOpenAI = None


def is_click_action_accepted(result: Any) -> bool:
    """Return true only when OneBot explicitly reports that the click was accepted."""
    def is_zero_code(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value == 0
        return isinstance(value, str) and value.strip() == "0"

    if result is True:
        return True
    if not isinstance(result, dict) or not result:
        return False
    numeric_codes = [result[name] for name in ("result", "retcode") if name in result]
    status = result.get("status")
    if not numeric_codes and status is None:
        return False
    if numeric_codes and not all(is_zero_code(code) for code in numeric_codes):
        return False
    if status is None:
        return True
    if isinstance(status, str) and status.lower() == "ok":
        return True
    return is_zero_code(status)


@dataclass
class CaptchaPause:
    active: bool = False
    reason: str = ""
    paused_at: float = 0.0
    phase: str = "idle"
    msg_seq: str = ""


@dataclass(frozen=True)
class CaptchaButton:
    label: str
    button_id: str
    callback_data: str


@dataclass(frozen=True)
class CaptchaChallenge:
    group_id: str
    bot_appid: str
    msg_seq: str
    buttons: Tuple[CaptchaButton, ...]


class CaptchaGuard:
    """Detect, solve and gate official-bot captcha messages per bound group."""

    CAPTCHA_RE = re.compile(r"请点击图中第(\d+)个表情")
    IMAGE_RE = re.compile(r"!\[.*?\]\((https://qqbot\.ugcimg\.cn/.*?)\)")
    AT_RE = re.compile(r"at_(?:tinyid|qq)=(\d+)")
    REWARD_SUCCESS_RE = re.compile(r"奖励[0-9]{2,5}灵石")
    SUCCESS_TEXTS = ("不需要验证", "验证码正确", "验证成功", "验证码通过")
    FAILURE_TEXTS = ("验证码不正确", "验证码错误", "验证失败")
    RECEIPT_PHASES = ("submitting", "awaiting_confirmation")
    MAX_SEEN_MSG_SEQS = 64
    CALLBACK_DATA_RE = re.compile(
        r"(?i)((?:['\"]?callback[_ ]?data['\"]?|callbackdata)\s*[:=]\s*)(?:'[^']*'|\"[^\"]*\"|[^,\s)]+)"
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None, logger=None):
        self.log = logger
        self._pauses: Dict[str, CaptchaPause] = {}
        self._generations: Dict[str, int] = {}
        self._seen_msg_seqs: Dict[str, OrderedDict[str, None]] = {}
        self._config_signature = None
        self._client = None
        self.configure(config)

    def configure(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Apply the latest account config, including changes made outside the custom Page."""
        cfg = dict(config or {})
        signature = tuple(
            (key, str(cfg.get(key, "")))
            for key in ("enabled", "vision_api_key", "vision_base_url", "vision_model", "debug_print", "auto_resume")
        )
        if signature == self._config_signature:
            return
        self._config_signature = signature
        self.enabled = bool(cfg.get("enabled", True))
        self.api_key = str(cfg.get("vision_api_key", "")).strip()
        self.base_url = str(cfg.get("vision_base_url", "https://ark.cn-beijing.volces.com/api/v3")).strip()
        self.model = str(cfg.get("vision_model", "")).strip()
        self.debug = bool(cfg.get("debug_print", False))
        # Compatibility: auto_resume now means resume after an explicit success receipt.
        self.auto_resume = bool(cfg.get("auto_resume", True))
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url) if AsyncOpenAI and self.api_key else None
        self._log(
            "info",
            "[captcha][CONFIG] 配置已加载 enabled=%s model=%r api_key=%s debug=%s auto_resume=%s",
            self.enabled,
            self.model or "未配置",
            "已配置" if self.api_key else "未配置",
            self.debug,
            self.auto_resume,
        )
        if self.enabled and not self.api_key:
            self._log("warning", "[captcha][CONFIG] 视觉模型 API Key 未配置")
        if self.enabled and not self.model:
            self._log("warning", "[captcha][CONFIG] 视觉模型 ID 未配置")

    def _log(self, level: str, message: str, *args) -> None:
        if self.log is None or (level == "info" and not self.debug):
            return
        try:
            getattr(self.log, level)(message, *args)
        except Exception:
            pass

    def is_paused(self, key: str) -> bool:
        return self._pauses.get(str(key), CaptchaPause()).active

    def status(self, key: str) -> CaptchaPause:
        return self._pauses.get(str(key), CaptchaPause())

    def pause(
        self,
        key: str,
        reason: str,
        *,
        phase: str = "paused",
        msg_seq: str = "",
    ) -> None:
        self._pauses[str(key)] = CaptchaPause(
            active=True,
            reason=str(reason),
            paused_at=time.time(),
            phase=str(phase),
            msg_seq=str(msg_seq),
        )

    def resume(self, key: str) -> None:
        key = str(key)
        self._invalidate_generation(key)
        self._pauses.pop(key, None)

    def _invalidate_generation(self, key: str) -> int:
        key = str(key)
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        return generation

    def _is_seen_msg_seq(self, key: str, msg_seq: str) -> bool:
        return str(msg_seq) in self._seen_msg_seqs.get(str(key), {})

    def _remember_msg_seq(self, key: str, msg_seq: str) -> None:
        key, msg_seq = str(key), str(msg_seq)
        seen = self._seen_msg_seqs.setdefault(key, OrderedDict())
        seen[msg_seq] = None
        seen.move_to_end(msg_seq)
        while len(seen) > self.MAX_SEEN_MSG_SEQS:
            seen.popitem(last=False)

    @classmethod
    def _redact_text(cls, value: Any) -> str:
        return cls.CALLBACK_DATA_RE.sub(r"\1<redacted>", str(value))

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "<redacted>" if str(key).replace("_", "").lower() == "callbackdata" else cls._redact_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return type(value)(cls._redact_value(item) for item in value)
        if isinstance(value, (str, int, float, bool, type(None))):
            return cls._redact_text(value) if isinstance(value, str) else value
        return cls._redact_text(value)

    def _is_targeted(self, event, raw_text: str, self_id: str) -> bool:
        if getattr(event, "is_at_or_wake_command", False):
            return True
        return str(self_id) in self.AT_RE.findall(str(raw_text or ""))

    def _is_current(self, key: str, generation: int, msg_seq: str) -> bool:
        pause = self.status(key)
        return (
            self._generations.get(str(key), 0) == generation
            and pause.active
            and pause.msg_seq == str(msg_seq)
        )

    async def handle(
        self,
        key: str,
        event,
        raw_text: str,
        self_id: str,
        notify: Callable[[str], Awaitable[None]],
        click: Callable[[Dict[str, str]], Awaitable[Any]],
    ) -> bool:
        if not self.enabled:
            return False

        key = str(key)
        raw_text = str(raw_text or "")
        receipt_handled = await self._handle_receipt(key, raw_text, notify)
        if receipt_handled:
            return True

        match = self.CAPTCHA_RE.search(raw_text)
        if not match:
            return False
        if not self._is_targeted(event, raw_text, self_id):
            self._log("info", "[captcha][SAFE] 验证码未 @ 当前账号，已跳过 self_id=%s", self_id)
            return False

        started = time.monotonic()
        self._log("info", "[captcha][CAPTCHA] 验证码处理开始 key=%s", key)
        try:
            return await self._handle_challenge(
                key,
                event,
                raw_text,
                match,
                notify,
                click,
            )
        finally:
            self._log(
                "info",
                "[captcha][CAPTCHA] 验证码处理结束 key=%s 耗时=%.2fs",
                key,
                time.monotonic() - started,
            )

    async def _handle_challenge(
        self,
        key: str,
        event,
        raw_text: str,
        match,
        notify: Callable[[str], Awaitable[None]],
        click: Callable[[Dict[str, str]], Awaitable[Any]],
    ) -> bool:
        image = self.IMAGE_RE.search(raw_text)
        if not image:
            self.pause(key, "验证码缺少图片链接", phase="invalid_challenge")
            await notify("⚠️ 验证码缺少图片链接，任务保持暂停；完成验证后发送「继续任务」。")
            self._log("warning", "[captcha] 验证码缺少图片链接 key=%s", key)
            return True

        challenge, parse_error = self._parse_challenge(event)
        if challenge is None:
            self.pause(key, parse_error, phase="invalid_challenge")
            await notify(f"⚠️ 验证码键盘字段不完整：{parse_error}；任务保持暂停，完成验证后发送「继续任务」。")
            self._log("warning", "[captcha] 验证码键盘字段不完整 key=%s detail=%s", key, parse_error)
            return True

        labels = [button.label for button in challenge.buttons]
        self._log(
            "info",
            "[captcha][CAPTCHA] 目标编号=%s 图片URL=%s msg_seq=%s bot_appid=%s 按钮数=%d labels=%r",
            match.group(1),
            image.group(1),
            challenge.msg_seq,
            challenge.bot_appid,
            len(challenge.buttons),
            labels,
        )

        if self._is_seen_msg_seq(key, challenge.msg_seq):
            self._log("info", "[captcha] 忽略重复验证码 key=%s msg_seq=%s", key, challenge.msg_seq)
            return True

        generation = self._invalidate_generation(key)
        self._remember_msg_seq(key, challenge.msg_seq)
        self.pause(
            key,
            "检测到验证码，等待识别与点击",
            phase="recognizing",
            msg_seq=challenge.msg_seq,
        )
        await notify("⚠️ 检测到验证码，已暂停本群全部自动任务。")
        if not self._is_current(key, generation, challenge.msg_seq):
            return True

        if AsyncOpenAI is None:
            self.pause(key, "缺少 openai 依赖", phase="configuration_error", msg_seq=challenge.msg_seq)
            await notify("⚠️ 缺少 openai 依赖，无法调用视觉模型。请在 AstrBot 的 Python 环境执行 pip install -r requirements.txt；任务保持暂停。")
            return True
        if not self.api_key or not self.model:
            self.pause(
                key,
                "openai 兼容视觉模型 API Key 或模型 ID 未配置",
                phase="configuration_error",
                msg_seq=challenge.msg_seq,
            )
            await notify("⚠️ 验证码视觉模型 API Key 或模型 ID 未配置，任务保持暂停；完成验证后发送「继续任务」。")
            return True

        try:
            self._log(
                "info",
                "[captcha][VISION] 提交图片给视觉模型 msg_seq=%s model=%r",
                challenge.msg_seq,
                self.model,
            )
            answer = await self._recognize(image.group(1), int(match.group(1)), labels)
            if not answer:
                raise RuntimeError("视觉模型未返回答案")
            self._log(
                "info",
                "[captcha][VISION] 模型回答=%r msg_seq=%s",
                answer,
                challenge.msg_seq,
            )
            if not self._is_current(key, generation, challenge.msg_seq):
                self._log(
                    "warning",
                    "[captcha] 丢弃陈旧视觉结果 key=%s msg_seq=%s answer=%r",
                    key,
                    challenge.msg_seq,
                    answer,
                )
                return True

            selected, used_fallback = self._select_button(answer, challenge.buttons)
            if used_fallback:
                self._log(
                    "warning",
                    "[captcha] 视觉答案未匹配候选，兜底首项 answer=%r labels=%r selected=%r msg_seq=%s",
                    answer,
                    labels,
                    selected.label,
                    challenge.msg_seq,
                )
            payload = self._payload_for(challenge, selected)
            self._log(
                "info",
                "[captcha][CLICK] 准备点击按钮 label=%r button_id=%r msg_seq=%s",
                selected.label,
                selected.button_id,
                challenge.msg_seq,
            )
            safe_payload = dict(payload)
            safe_payload["callback_data"] = "<redacted>"
            self._log("info", "[captcha][CLICK] payload=%r", safe_payload)
            self.pause(
                key,
                "验证码点击已提交，等待 OneBot 响应",
                phase="submitting",
                msg_seq=challenge.msg_seq,
            )
            click_result = await click(payload)
            self._log("info", "[captcha][CLICK] OneBot 返回=%r", self._redact_value(click_result))
        except Exception as exc:
            if self._is_current(key, generation, challenge.msg_seq):
                safe_error = self._redact_text(exc)
                self.pause(
                    key,
                    f"验证码处理失败：{safe_error}",
                    phase="processing_error",
                    msg_seq=challenge.msg_seq,
                )
                await notify(f"⚠️ 验证码处理失败，任务保持暂停：{safe_error}\n完成验证后发送「继续任务」。")
                self._log("exception", "[captcha][CAPTCHA] 验证码处理失败 key=%s msg_seq=%s error=%s", key, challenge.msg_seq, safe_error)
            return True

        if not self._is_current(key, generation, challenge.msg_seq):
            return True
        self.pause(
            key,
            "验证码已提交，等待官方确认",
            phase="awaiting_confirmation",
            msg_seq=challenge.msg_seq,
        )
        await notify("✅ 验证码已提交，正在等待官方确认，自动任务保持暂停。")
        self._log("info", "[captcha] 点击已提交，等待官方确认 key=%s msg_seq=%s", key, challenge.msg_seq)
        return True

    async def _handle_receipt(
        self,
        key: str,
        raw_text: str,
        notify: Callable[[str], Awaitable[None]],
    ) -> bool:
        pause = self.status(key)
        if not pause.active:
            return False
        if pause.phase not in self.RECEIPT_PHASES:
            return False
        if any(text in raw_text for text in self.FAILURE_TEXTS):
            self._invalidate_generation(key)
            self.pause(
                key,
                "官方返回验证码错误，等待新的验证码或人工恢复",
                phase="failed",
                msg_seq=pause.msg_seq,
            )
            self._log("warning", "[captcha] 官方确认验证码错误 key=%s msg_seq=%s", key, pause.msg_seq)
            return True
        success_text = next((text for text in self.SUCCESS_TEXTS if text in raw_text), "")
        reward_match = self.REWARD_SUCCESS_RE.search(raw_text)
        if not success_text and reward_match is None:
            return False
        matched_receipt = success_text or reward_match.group(0)
        if self.auto_resume:
            self.resume(key)
            await notify("✅ 官方已确认验证通过，已恢复本群自动任务。")
        else:
            self._invalidate_generation(key)
            self.pause(
                key,
                "官方已确认验证通过，等待人工恢复",
                phase="verified",
                msg_seq=pause.msg_seq,
            )
            await notify("✅ 官方已确认验证通过；发送「继续任务」恢复自动任务。")
        self._log(
            "info",
            "[captcha][RECEIPT] 官方确认验证通过 key=%s msg_seq=%s receipt=%r",
            key,
            pause.msg_seq,
            matched_receipt,
        )
        return True

    async def _recognize(self, image_url: str, target_index: int, labels: List[str]) -> str:
        prompt = (
            "这是一张 QQ 机器人的干扰验证码图片，请从左到右识别图中的物品。"
            "忽略浅淡、半透明的背景干扰，只统计深色高对比的前景表情。"
            f"题目要求找出第 {target_index} 个物品。"
            f"候选按钮作为唯一识别类别，禁止自由命名；请只从以下候选按钮中选择一个最匹配的答案：{labels}。"
            "对变形、遮挡图案逐一与候选比较。最终只能原样返回一个候选按钮，"
            "不要标点，不要解释。"
        )
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            max_tokens=16,
        )
        return str(response.choices[0].message.content or "").strip()

    def _walk_nodes(self, root) -> List[Dict[str, Any]]:
        seen = set()
        nodes: List[Dict[str, Any]] = []

        def walk(value):
            if value is None or id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, dict):
                nodes.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    walk(child)
            elif hasattr(value, "__dict__"):
                walk(vars(value))

        walk(root)
        return nodes

    def _parse_challenge(self, event) -> Tuple[Optional[CaptchaChallenge], str]:
        message_obj = getattr(event, "message_obj", None)
        group_id = str(
            getattr(message_obj, "group_id", "") or getattr(event, "group_id", "")
            or (event.get("group_id", "") if isinstance(event, dict) else "") or ""
        )
        roots = [
            getattr(message_obj, "raw_message", None),
            getattr(message_obj, "message", None),
            getattr(message_obj, "data", None),
            getattr(event, "raw_message", None),
            getattr(event, "message", None),
            getattr(event, "data", None),
            event if isinstance(event, dict) else None,
        ]
        base_missing = {"group_id"} if not group_id else set()
        best_missing: Optional[set[str]] = None
        for root in roots:
            for node in self._walk_nodes(root):
                candidates = []
                if node.get("keyboard") is not None:
                    candidates.append(node.get("keyboard"))
                if node.get("type") == "keyboard" and isinstance(node.get("data"), dict):
                    candidates.append(node.get("data"))
                if node.get("inlineKeyboardElement") is not None:
                    candidates.append(node.get("inlineKeyboardElement"))

                for keyboard in candidates:
                    msg_seq = str(self._first_field(
                        (keyboard, node, root),
                        ("msgSeq", "msg_seq", "messageSeq", "message_seq", "msgId", "msg_id"),
                    ) or "")
                    bot_appid = str(self._first_field(
                        (keyboard, node, root),
                        ("botAppid", "bot_appid", "botAppId", "appid", "app_id"),
                    ) or "")
                    buttons = self._buttons_from_keyboard(keyboard)
                    candidate_missing = set(base_missing)
                    if not bot_appid:
                        candidate_missing.add("bot_appid")
                    if not msg_seq:
                        candidate_missing.add("msg_seq")
                    if not buttons:
                        candidate_missing.add("buttons")
                    if not candidate_missing:
                        return CaptchaChallenge(group_id, bot_appid, msg_seq, tuple(buttons)), ""
                    if best_missing is None or len(candidate_missing) < len(best_missing):
                        best_missing = candidate_missing
        return None, "缺少 " + ", ".join(sorted(best_missing or {"keyboard"}))

    @staticmethod
    def _first_field(containers, names):
        for container in containers:
            if not isinstance(container, dict):
                continue
            for name in names:
                value = container.get(name)
                if value is not None and str(value).strip():
                    return value
        return None

    def _buttons_from_keyboard(self, keyboard) -> List[CaptchaButton]:
        buttons: List[CaptchaButton] = []
        seen_buttons = set()
        for node in self._walk_nodes(keyboard):
            render_data = node.get("render_data") or node.get("renderData")
            if not isinstance(render_data, dict):
                render_data = {}
            action = node.get("action")
            if not isinstance(action, dict):
                action = {}
            label = str(
                node.get("label") or node.get("text") or node.get("name")
                or node.get("title") or render_data.get("label") or ""
            )
            button_id = str(
                node.get("id") or node.get("buttonId") or node.get("button_id")
                or node.get("buttonID") or node.get("index") or ""
            )
            callback = str(
                node.get("data") or node.get("callbackData") or node.get("callback_data")
                or node.get("value") or node.get("actionData") or action.get("data") or ""
            )
            if not label or not button_id or not callback:
                continue
            identity = (label, button_id, callback)
            if identity not in seen_buttons:
                seen_buttons.add(identity)
                buttons.append(CaptchaButton(label, button_id, callback))
        return buttons

    @staticmethod
    def _select_button(
        answer: str, buttons: Tuple[CaptchaButton, ...]
    ) -> Tuple[CaptchaButton, bool]:
        selected = next((button for button in buttons if button.label == answer), None)
        if selected is None:
            selected = next(
                (button for button in buttons if button.label in answer or answer in button.label),
                None,
            )
        if selected is not None:
            return selected, False
        return buttons[0], True

    @staticmethod
    def _payload_for(challenge: CaptchaChallenge, button: CaptchaButton) -> Dict[str, str]:
        return {
            "group_id": challenge.group_id,
            "bot_appid": challenge.bot_appid,
            "msg_seq": challenge.msg_seq,
            "button_id": button.button_id,
            "callback_data": button.callback_data,
        }

    def _button_payload(self, event, answer: str) -> Optional[Dict[str, str]]:
        """Compatibility helper used by existing callers and tests."""
        challenge, _error = self._parse_challenge(event)
        if challenge is None:
            return None
        selected, _used_fallback = self._select_button(answer, challenge.buttons)
        return self._payload_for(challenge, selected)
