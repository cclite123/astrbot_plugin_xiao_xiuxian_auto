from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib import request as urllib_request

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
    MAX_RAW_PB_RECORDS = 4
    MAX_RAW_PB_CHARS = 16 * 1024 * 1024
    IMAGE_DOWNLOAD_TIMEOUT_SEC = 20
    MAX_IMAGE_BYTES = 8 * 1024 * 1024
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
        *,
        fetch_message: Optional[Callable[[], Awaitable[Any]]] = None,
        capture_raw_pb: Optional[
            Callable[[List[Dict[str, str]]], Awaitable[Optional[str]]]
        ] = None,
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
                fetch_message,
                capture_raw_pb,
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
        fetch_message: Optional[Callable[[], Awaitable[Any]]],
        capture_raw_pb: Optional[
            Callable[[List[Dict[str, str]]], Awaitable[Optional[str]]]
        ],
    ) -> bool:
        image = self.IMAGE_RE.search(raw_text)
        if not image:
            self.pause(key, "验证码缺少图片链接", phase="invalid_challenge")
            await notify("⚠️ 验证码缺少图片链接，任务保持暂停；完成验证后发送「继续任务」。")
            self._log("warning", "[captcha] 验证码缺少图片链接 key=%s", key)
            return True

        challenge, parse_error = self._parse_challenge(event)
        parse_source = event
        if challenge is None and fetch_message is not None:
            self._log("info", "[captcha][EVENT] 实时事件缺少完整键盘，尝试通过 get_msg 补取")
            try:
                detail = await fetch_message()
            except Exception as exc:
                detail = None
                self._log("warning", "[captcha][EVENT] get_msg 补取失败 error=%s", self._redact_text(exc))
            if detail is not None:
                group_id = str(key).split(":", 1)[1] if ":" in str(key) else ""
                parse_source = {
                    "group_id": group_id,
                    "message_detail": detail,
                }
                challenge, parse_error = self._parse_challenge(parse_source)
        if challenge is None:
            if self.debug and capture_raw_pb is not None:
                records = self._raw_pb_records(parse_source)
                if records:
                    try:
                        diagnostic_path = await capture_raw_pb(records)
                    except Exception as exc:
                        self._log(
                            "warning",
                            "[captcha][EVENT] raw_pb 诊断文件写入失败 error=%s",
                            self._redact_text(exc),
                        )
                    else:
                        if diagnostic_path:
                            self._log(
                                "warning",
                                "[captcha][EVENT] raw_pb 诊断样本已保存 path=%s（文件可能包含敏感回调数据，请勿公开）",
                                diagnostic_path,
                            )
            self.pause(key, parse_error, phase="invalid_challenge")
            await notify(f"⚠️ 验证码键盘字段不完整：{parse_error}；任务保持暂停，完成验证后发送「继续任务」。")
            self._log("warning", "[captcha] 验证码键盘字段不完整 key=%s detail=%s", key, parse_error)
            self._log("warning", "[captcha][EVENT] 脱敏事件结构=%r", self._event_shape(parse_source))
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
        image_data_uri = await self._image_data_uri(image_url)
        prompt = (
            "这是一张横向排列的 QQ 机器人干扰验证码图片，请从左到右识别有效物品。"
            "图片中的浅色、低饱和或半透明图案全部是背景干扰，即使轮廓清晰也不能计数。"
            "只统计颜色最深、最饱和、最不透明且与背景对比最强的前景表情；"
            "背景曲线、噪点和浅色物品都不能进入计数序列。"
            f"题目要求找出第 {target_index} 个物品。"
            f"候选按钮作为唯一识别类别，禁止自由命名；请只从以下候选按钮中选择一个最匹配的答案：{labels}。"
            "候选表情与图片中的画法可能不同，请按物体语义匹配。"
            "输出前在内部独立执行两次从左到右扫描，确认前景列表及目标序号一致。"
            "最终只能原样返回一个候选按钮，"
            "不要标点，不要解释。"
        )
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_uri}},
            ]}],
            max_tokens=16,
        )
        return str(response.choices[0].message.content or "").strip()

    @classmethod
    def _download_image(cls, image_url: str) -> Tuple[bytes, str]:
        request = urllib_request.Request(
            image_url,
            headers={
                "Accept": "image/*",
                "User-Agent": "astrbot-plugin-xiao-xiuxian-auto/1.0",
            },
        )
        with urllib_request.urlopen(request, timeout=cls.IMAGE_DOWNLOAD_TIMEOUT_SEC) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > cls.MAX_IMAGE_BYTES:
                raise ValueError("验证码图片超过 8MB 限制")

            chunks = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > cls.MAX_IMAGE_BYTES:
                    raise ValueError("验证码图片超过 8MB 限制")
                chunks.append(chunk)

            if not total:
                raise ValueError("验证码图片为空")

            content_type = str(response.headers.get_content_type() or "").lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}:
                content_type = "image/jpeg"
            return b"".join(chunks), content_type

    async def _image_data_uri(self, image_url: str) -> str:
        image_bytes, content_type = await asyncio.to_thread(self._download_image, image_url)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        self._log(
            "info",
            "[captcha][VISION] 验证码图片已转 Base64 bytes=%d content_type=%s",
            len(image_bytes),
            content_type,
        )
        return f"data:{content_type};base64,{encoded}"

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

    def _event_shape(self, event) -> Dict[str, Any]:
        message_obj = getattr(event, "message_obj", None)
        roots = (
            ("message_obj.raw_message", getattr(message_obj, "raw_message", None)),
            ("message_obj.message", getattr(message_obj, "message", None)),
            ("event.raw_message", getattr(event, "raw_message", None)),
            ("event.message", getattr(event, "message", None)),
            ("event", event if isinstance(event, dict) else None),
        )
        root_shapes = []
        segment_types = set()
        node_keys = set()
        for name, root in roots:
            if root is None:
                continue
            shape: Dict[str, Any] = {"name": name, "type": type(root).__name__}
            if isinstance(root, dict):
                shape["keys"] = sorted(str(key) for key in root.keys())
            elif isinstance(root, (list, tuple)):
                shape["length"] = len(root)
            root_shapes.append(shape)
            for node in self._walk_nodes(root)[:64]:
                keys = tuple(sorted(str(key) for key in node.keys()))
                if keys:
                    node_keys.add(keys)
                segment_type = node.get("type")
                if isinstance(segment_type, str):
                    segment_types.add(segment_type)
        return {
            "event_type": type(event).__name__,
            "message_obj_type": type(message_obj).__name__ if message_obj is not None else "none",
            "roots": root_shapes,
            "segment_types": sorted(segment_types),
            "node_keys": sorted(node_keys)[:32],
        }

    @classmethod
    def _raw_pb_records(cls, event) -> List[Dict[str, str]]:
        records: List[Dict[str, str]] = []
        seen_objects = set()
        seen_payloads = set()

        def normalize(value) -> Optional[Tuple[str, str]]:
            if isinstance(value, (bytes, bytearray)):
                return "hex", bytes(value).hex()
            if isinstance(value, str):
                data = value.strip()
                if not data:
                    return None
                cleaned = data[2:] if data.lower().startswith("0x") else data
                encoding = (
                    "hex"
                    if len(cleaned) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", cleaned)
                    else "text"
                )
                return encoding, cleaned if encoding == "hex" else data
            if isinstance(value, dict) and value.get("type") == "Buffer":
                data = value.get("data")
                if isinstance(data, list):
                    try:
                        return "hex", bytes(data).hex()
                    except (TypeError, ValueError):
                        return None
            return None

        def walk(value, path: str, depth: int = 0) -> None:
            if depth > 8 or len(records) >= cls.MAX_RAW_PB_RECORDS or value is None:
                return
            if isinstance(value, (dict, list, tuple)) or hasattr(value, "__dict__"):
                if id(value) in seen_objects:
                    return
                seen_objects.add(id(value))
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}"
                    if str(key).replace("_", "").lower() == "rawpb":
                        normalized = normalize(child)
                        if normalized is not None:
                            encoding, data = normalized
                            if data not in seen_payloads and len(data) <= cls.MAX_RAW_PB_CHARS:
                                seen_payloads.add(data)
                                records.append({
                                    "source": child_path,
                                    "encoding": encoding,
                                    "data": data,
                                })
                    walk(child, child_path, depth + 1)
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]", depth + 1)
            elif hasattr(value, "__dict__"):
                walk(vars(value), path, depth + 1)

        walk(event, "event")
        return records

    @staticmethod
    def _raw_pb_bytes(value: Any) -> Optional[bytes]:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            if not cleaned or len(cleaned) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", cleaned):
                return None
            try:
                return bytes.fromhex(cleaned)
            except ValueError:
                return None
        if isinstance(value, dict) and value.get("type") == "Buffer":
            data = value.get("data")
            if isinstance(data, list):
                try:
                    return bytes(data)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _read_pb_varint(data: bytes, position: int) -> Tuple[int, int]:
        value = 0
        shift = 0
        while position < len(data) and shift < 70:
            byte = data[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value, position
            shift += 7
        raise ValueError("invalid protobuf varint")

    @classmethod
    def _protobuf_fields(cls, data: bytes) -> List[Tuple[int, int, Any]]:
        fields: List[Tuple[int, int, Any]] = []
        position = 0
        while position < len(data):
            if len(fields) >= 4096:
                raise ValueError("too many protobuf fields")
            tag, position = cls._read_pb_varint(data, position)
            field_number, wire_type = tag >> 3, tag & 7
            if field_number <= 0:
                raise ValueError("invalid protobuf field")
            if wire_type == 0:
                value, position = cls._read_pb_varint(data, position)
            elif wire_type == 1:
                end = position + 8
                if end > len(data):
                    raise ValueError("truncated protobuf fixed64")
                value, position = data[position:end], end
            elif wire_type == 2:
                size, position = cls._read_pb_varint(data, position)
                end = position + size
                if end > len(data):
                    raise ValueError("truncated protobuf bytes")
                value, position = data[position:end], end
            elif wire_type == 5:
                end = position + 4
                if end > len(data):
                    raise ValueError("truncated protobuf fixed32")
                value, position = data[position:end], end
            else:
                raise ValueError(f"unsupported protobuf wire type {wire_type}")
            fields.append((field_number, wire_type, value))
        return fields

    @staticmethod
    def _pb_values(fields, field_number: int, wire_type: int) -> List[Any]:
        return [
            value
            for number, wire, value in fields
            if number == field_number and wire == wire_type
        ]

    @classmethod
    def _decode_button_extra(cls, payload: bytes) -> List[Dict[str, Any]]:
        """Decode the QQ commonElem(46) ButtonExtra fields required for captcha clicks."""
        outer = cls._protobuf_fields(payload)
        keyboard_data = cls._pb_values(outer, 1, 2)
        if not keyboard_data:
            return []
        rows: List[Dict[str, Any]] = []
        for row_payload in cls._pb_values(cls._protobuf_fields(keyboard_data[0]), 1, 2):
            buttons = []
            for button_payload in cls._pb_values(cls._protobuf_fields(row_payload), 1, 2):
                button_fields = cls._protobuf_fields(button_payload)
                button_ids = cls._pb_values(button_fields, 1, 2)
                renders = cls._pb_values(button_fields, 2, 2)
                actions = cls._pb_values(button_fields, 3, 2)
                if not button_ids or not renders or not actions:
                    continue
                render_fields = cls._protobuf_fields(renders[0])
                action_fields = cls._protobuf_fields(actions[0])
                labels = cls._pb_values(render_fields, 1, 2)
                callbacks = cls._pb_values(action_fields, 5, 2)
                if not labels or not callbacks:
                    continue
                try:
                    button_id = button_ids[0].decode("utf-8")
                    label = labels[0].decode("utf-8")
                    callback = callbacks[0].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if not button_id or not label or not callback:
                    continue
                buttons.append({
                    "id": button_id,
                    "render_data": {"label": label},
                    "action": {"data": callback},
                })
            if buttons:
                rows.append({"buttons": buttons})
        return rows

    @classmethod
    def _decode_message_keyboard(
        cls,
        message: bytes,
        bot_appid: str,
    ) -> Optional[Dict[str, Any]]:
        message_fields = cls._protobuf_fields(message)
        content_heads = cls._pb_values(message_fields, 2, 2)
        bodies = cls._pb_values(message_fields, 3, 2)
        if not content_heads or not bodies:
            return None
        content_fields = cls._protobuf_fields(content_heads[0])
        msg_seqs = cls._pb_values(content_fields, 5, 0)
        body_fields = cls._protobuf_fields(bodies[0])
        rich_texts = cls._pb_values(body_fields, 1, 2)
        if not msg_seqs or not rich_texts:
            return None
        rich_fields = cls._protobuf_fields(rich_texts[0])
        for element in cls._pb_values(rich_fields, 2, 2):
            element_fields = cls._protobuf_fields(element)
            for common in cls._pb_values(element_fields, 53, 2):
                common_fields = cls._protobuf_fields(common)
                service_types = cls._pb_values(common_fields, 1, 0)
                payloads = cls._pb_values(common_fields, 2, 2)
                if 46 not in service_types or not payloads:
                    continue
                rows = cls._decode_button_extra(payloads[0])
                if rows:
                    return {
                        "botAppid": bot_appid,
                        "msgSeq": str(msg_seqs[0]),
                        "rows": rows,
                    }
        return None

    @classmethod
    def _keyboard_from_raw_pb(cls, value: Any) -> Optional[Dict[str, Any]]:
        """Recover the inline keyboard that LLBot currently drops during message conversion."""
        raw = cls._raw_pb_bytes(value)
        if raw is None or len(raw) > cls.MAX_RAW_PB_CHARS // 2:
            return None
        appid_match = re.search(rb"https://qqbot\.ugcimg\.cn/(\d+)/", raw)
        bot_appid = appid_match.group(1).decode("ascii") if appid_match else ""
        try:
            root_fields = cls._protobuf_fields(raw)
        except ValueError:
            return None
        messages = [raw, *cls._pb_values(root_fields, 1, 2)]
        for message in messages:
            try:
                keyboard = cls._decode_message_keyboard(message, bot_appid)
            except (ValueError, IndexError):
                continue
            if keyboard is not None:
                return keyboard
        return None

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
                for raw_pb_name in ("raw_pb", "rawPb"):
                    if node.get(raw_pb_name) is None:
                        continue
                    decoded_keyboard = self._keyboard_from_raw_pb(node.get(raw_pb_name))
                    if decoded_keyboard is not None:
                        candidates.append(decoded_keyboard)
                        self._log(
                            "info",
                            "[captcha][EVENT] 已从 LLBot raw_pb 恢复 serviceType=46 键盘",
                        )

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
