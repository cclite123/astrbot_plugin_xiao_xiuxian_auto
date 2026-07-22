from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    from openai import AsyncOpenAI
except ImportError:  # Keep the plugin loadable until the optional dependency is installed.
    AsyncOpenAI = None


@dataclass
class CaptchaPause:
    active: bool = False
    reason: str = ""
    paused_at: float = 0.0


class CaptchaGuard:
    """Detect, solve and gate official-bot captcha messages per bound group."""

    CAPTCHA_RE = re.compile(r"请点击图中第(\d+)个表情")
    IMAGE_RE = re.compile(r"!\[.*?\]\((https://qqbot\.ugcimg\.cn/.*?)\)")
    AT_RE = re.compile(r"at_(?:tinyid|qq)=(\d+)")

    def __init__(self, config: Optional[Dict[str, Any]] = None, logger=None):
        self.log = logger
        self._pauses: Dict[str, CaptchaPause] = {}
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
        self.auto_resume = bool(cfg.get("auto_resume", True))
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url) if AsyncOpenAI and self.api_key else None

    def is_paused(self, key: str) -> bool:
        return self._pauses.get(str(key), CaptchaPause()).active

    def status(self, key: str) -> CaptchaPause:
        return self._pauses.get(str(key), CaptchaPause())

    def pause(self, key: str, reason: str) -> None:
        self._pauses[str(key)] = CaptchaPause(True, str(reason), time.time())

    def resume(self, key: str) -> None:
        self._pauses.pop(str(key), None)

    def _is_targeted(self, event, raw_text: str, self_id: str) -> bool:
        if getattr(event, "is_at_or_wake_command", False):
            return True
        return str(self_id) in self.AT_RE.findall(str(raw_text or ""))

    async def handle(
        self,
        key: str,
        event,
        raw_text: str,
        self_id: str,
        notify: Callable[[str], Awaitable[None]],
        click: Callable[[Dict[str, str]], Awaitable[None]],
    ) -> bool:
        if not self.enabled:
            return False
        match = self.CAPTCHA_RE.search(str(raw_text or ""))
        if not match or not self._is_targeted(event, raw_text, self_id):
            return False
        self.pause(key, "检测到验证码，等待识别与点击")
        await notify("⚠️ 检测到验证码，已暂停本群全部自动任务。")
        image = self.IMAGE_RE.search(str(raw_text or ""))
        if not image:
            self.pause(key, "验证码缺少图片链接")
            await notify("⚠️ 验证码缺少图片链接，任务保持暂停；完成验证后发送「继续任务」。")
            return True
        if AsyncOpenAI is None:
            self.pause(key, "缺少 openai 依赖")
            await notify("⚠️ 缺少 openai 依赖，无法调用视觉模型。请在 AstrBot 的 Python 环境执行 pip install -r requirements.txt；任务保持暂停。")
            return True
        if not self.api_key or not self.model:
            self.pause(key, "视觉模型 API Key 或模型 ID 未配置")
            await notify("⚠️ 验证码视觉模型 API Key 或模型 ID 未配置，任务保持暂停；完成验证后发送「继续任务」。")
            return True
        try:
            answer = await self._recognize(image.group(1), int(match.group(1)))
            if not answer:
                raise RuntimeError("视觉模型未返回答案")
            payload = self._button_payload(event, answer)
            if not payload:
                raise RuntimeError("验证码内联键盘参数不完整")
            await click(payload)
        except Exception as exc:
            self.pause(key, f"验证码处理失败：{exc}")
            await notify(f"⚠️ 验证码处理失败，任务保持暂停：{exc}\n完成验证后发送「继续任务」。")
            return True
        if self.auto_resume:
            await asyncio.sleep(2.0)
            self.resume(key)
            await notify("✅ 验证码已提交，已恢复本群自动任务。")
        else:
            self.pause(key, "验证码已提交，等待人工恢复")
            await notify("✅ 验证码已提交，任务保持暂停；发送「继续任务」恢复。")
        return True

    async def _recognize(self, image_url: str, target_index: int) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"识别图片从左到右第 {target_index} 个表情，只回复该表情或物品名称。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            max_tokens=16,
        )
        return str(response.choices[0].message.content or "").strip()

    def _button_payload(self, event, answer: str) -> Optional[Dict[str, str]]:
        # 不同 AstrBot/OneBot 版本会把键盘放在 raw_message、message、data 或事件对象本身。
        # 从事件根对象递归查找，避免只读 message_obj.raw_message 导致参数丢失。
        root = event
        seen = set()
        nodes = []

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
        msg_seq = bot_appid = ""
        buttons = []
        for node in nodes:
            msg_seq = msg_seq or str(
                node.get("msgSeq") or node.get("msg_seq") or node.get("messageSeq")
                or node.get("message_seq") or node.get("msgId") or node.get("msg_id") or ""
            )
            bot_appid = bot_appid or str(
                node.get("botAppid") or node.get("bot_appid") or node.get("botAppId")
                or node.get("appid") or node.get("app_id") or ""
            )
            label = str(node.get("label") or node.get("text") or node.get("name") or node.get("title") or "")
            button_id = str(
                node.get("id") or node.get("buttonId") or node.get("button_id")
                or node.get("buttonID") or node.get("index") or ""
            )
            callback = str(
                node.get("data") or node.get("callbackData") or node.get("callback_data")
                or node.get("value") or node.get("actionData") or ""
            )
            if label and (button_id or callback):
                buttons.append((label, button_id, callback))
        selected = next((item for item in buttons if item[0] == answer), None)
        if selected is None:
            selected = next((item for item in buttons if item[0] in answer or answer in item[0]), None)
        if selected is None or not msg_seq or not bot_appid:
            if self.debug and self.log:
                try:
                    key_names = sorted({str(key) for node in nodes for key in node.keys()})
                    self.log.warning(
                        "[captcha] 未找到完整内联键盘参数 answer=%r msg_seq=%r bot_appid=%r buttons=%d keys=%s",
                        answer, bool(msg_seq), bool(bot_appid), len(buttons), key_names[:80],
                    )
                except Exception:
                    pass
            return None
        message_obj = getattr(event, "message_obj", None)
        group_id = str(
            getattr(message_obj, "group_id", "") or getattr(event, "group_id", "")
            or (event.get("group_id", "") if isinstance(event, dict) else "") or ""
        )
        if not group_id:
            return None
        return {"group_id": group_id, "bot_appid": bot_appid, "msg_seq": msg_seq, "button_id": selected[1], "callback_data": selected[2]}
