from __future__ import annotations

import hashlib
from typing import Any, Dict, Tuple


LLBOT_INLINE_KEYBOARD_CLICK_COMMAND = "OidbSvcTrpcTcp.0x112e_1"


def normalize_qq_id(value: Any) -> str:
    """Return a usable numeric QQ id, rejecting CQHttp dynamic actions."""
    if value is None or isinstance(value, bool) or callable(value):
        return ""
    try:
        text = str(value).strip()
    except Exception:
        return ""
    if not text.isdigit() or int(text) <= 0:
        return ""
    return text


def event_field(event: Any, name: str, default: Any = None) -> Any:
    """Read a OneBot field from raw events or AstrBot wrappers."""
    containers = [event]
    try:
        message_obj = getattr(event, "message_obj", None)
    except Exception:
        message_obj = None
    if message_obj is not None:
        containers.append(message_obj)

    for container in tuple(containers):
        try:
            raw_message = (
                container.get("raw_message")
                if isinstance(container, dict)
                else getattr(container, "raw_message", None)
            )
        except Exception:
            raw_message = None
        if raw_message is not None and raw_message is not container:
            containers.append(raw_message)

    for container in containers:
        try:
            if isinstance(container, dict):
                if name in container and container[name] is not None:
                    return container[name]
            else:
                value = getattr(container, name, None)
                if value is not None:
                    return value
        except Exception:
            continue

    getter_name = {
        "user_id": "get_sender_id",
        "group_id": "get_group_id",
    }.get(name)
    if getter_name:
        try:
            getter = getattr(event, getter_name, None)
            if callable(getter):
                value = getter()
                if value is not None:
                    return value
        except Exception:
            pass
    return default


def event_fingerprint(event: Any, self_id: str, group_id: Any, text: str) -> str:
    """Build the same fingerprint for raw OneBot and AstrBot-wrapped events."""
    prefix = f"{self_id}:{group_id}"
    for field_name in ("message_id", "message_seq", "msg_seq"):
        value = event_field(event, field_name)
        if value is not None and str(value).strip():
            return f"{prefix}:{field_name}:{str(value).strip()}"

    user_id = str(event_field(event, "user_id", "") or "")
    digest = hashlib.sha256(f"{user_id}\n{text}".encode("utf-8")).hexdigest()
    return f"{prefix}:content:{digest}"


def is_onebot_action_missing(result: Any, action: str) -> bool:
    """Return whether LLBot explicitly rejected an unregistered OneBot action."""
    values = []
    retcode = None
    if isinstance(result, dict):
        retcode = result.get("retcode")
        values.extend(result.get(name) for name in ("message", "wording", "error"))
    else:
        retcode = getattr(result, "retcode", None)
        values.extend(getattr(result, name, None) for name in ("message", "wording"))
    values.append(str(result))
    detail = " ".join(str(value) for value in values if value is not None).lower()
    return (
        str(retcode) == "1404"
        and action.lower() in detail
        and ("api 不存在" in detail or "api not found" in detail)
    )


def _encode_varint(value: int) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"protobuf varint 必须是非负整数：{value!r}")
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _encode_pb_varint(field: int, value: int) -> bytes:
    return _encode_varint(field << 3) + _encode_varint(value)


def _encode_pb_bytes(field: int, value: bytes) -> bytes:
    return _encode_varint((field << 3) | 2) + _encode_varint(len(value)) + value


def _positive_decimal(payload: Dict[str, Any], name: str) -> int:
    value = payload.get(name)
    text = str(value).strip() if value is not None else ""
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"LLBot 验证码点击参数 {name} 不是正整数")
    return int(text)


def build_llbot_inline_keyboard_click(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Build the OIDB 0x112e_1 request accepted by LLBot's send_pb action."""
    group_id = _positive_decimal(payload, "group_id")
    bot_appid = _positive_decimal(payload, "bot_appid")
    msg_seq = _positive_decimal(payload, "msg_seq")
    button_id = str(payload.get("button_id") or "")
    callback_data = str(payload.get("callback_data") or "")
    if not button_id:
        raise ValueError("LLBot 验证码点击参数 button_id 为空")

    body = b"".join((
        _encode_pb_varint(3, bot_appid),
        _encode_pb_varint(4, msg_seq),
        _encode_pb_bytes(5, button_id.encode("utf-8")),
        _encode_pb_bytes(6, callback_data.encode("utf-8")),
        _encode_pb_varint(8, group_id),
        _encode_pb_varint(9, 1),
    ))
    envelope = b"".join((
        _encode_pb_varint(1, 0x112E),
        _encode_pb_varint(2, 1),
        _encode_pb_bytes(4, body),
    ))
    return LLBOT_INLINE_KEYBOARD_CLICK_COMMAND, envelope.hex()


def _decode_pb_fields(data: bytes) -> Dict[int, Tuple[int, Any]]:
    fields: Dict[int, Tuple[int, Any]] = {}
    position = 0

    def read_varint() -> int:
        nonlocal position
        value = 0
        shift = 0
        while position < len(data) and shift < 70:
            byte = data[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7
        raise ValueError("protobuf varint 截断或溢出")

    while position < len(data):
        tag = read_varint()
        field, wire = tag >> 3, tag & 7
        if field <= 0:
            raise ValueError("protobuf 字段号无效")
        if wire == 0:
            value = read_varint()
        elif wire == 2:
            size = read_varint()
            end = position + size
            if end > len(data):
                raise ValueError("protobuf 长度字段截断")
            value, position = data[position:end], end
        elif wire == 1:
            end = position + 8
            if end > len(data):
                raise ValueError("protobuf fixed64 字段截断")
            value, position = data[position:end], end
        elif wire == 5:
            end = position + 4
            if end > len(data):
                raise ValueError("protobuf fixed32 字段截断")
            value, position = data[position:end], end
        else:
            raise ValueError(f"protobuf wire type {wire} 不受支持")
        fields[field] = (wire, value)
    return fields


def _pb_text(fields: Dict[int, Tuple[int, Any]], field: int) -> str:
    item = fields.get(field)
    if item is None or item[0] != 2:
        return ""
    return bytes(item[1]).decode("utf-8", errors="replace")


def _pb_uint(fields: Dict[int, Tuple[int, Any]], field: int, default: int = 0) -> int:
    item = fields.get(field)
    if item is None:
        return default
    if item[0] != 0:
        raise ValueError(f"protobuf 字段 {field} 不是 varint")
    return int(item[1])


def validate_llbot_inline_keyboard_click_response(result: Any) -> Dict[str, Any]:
    """Validate both the OneBot send_pb result and QQ's OIDB click response."""
    if isinstance(result, Exception):
        raise RuntimeError(f"LLBot send_pb 调用失败：{result}")
    if not isinstance(result, dict):
        raise RuntimeError(f"LLBot send_pb 返回格式无效：{result!r}")
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    response_command = data.get("cmd")
    if response_command and response_command != LLBOT_INLINE_KEYBOARD_CLICK_COMMAND:
        raise RuntimeError(f"LLBot send_pb 返回了意外的 command：{response_command!r}")
    response_hex = data.get("hex") or data.get("pb")
    if not isinstance(response_hex, str) or not response_hex.strip():
        raise RuntimeError("LLBot send_pb 未返回 protobuf 响应")
    try:
        response_bytes = bytes.fromhex(response_hex.strip())
        envelope = _decode_pb_fields(response_bytes)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"LLBot send_pb 返回的 protobuf 无效：{exc}") from exc

    try:
        error_code = _pb_uint(envelope, 3)
    except ValueError as exc:
        raise RuntimeError(f"LLBot 按钮点击 OIDB 响应无效：{exc}") from exc
    if error_code:
        raise RuntimeError(
            f"LLBot 按钮点击 OIDB 失败：error_code={error_code} "
            f"error={_pb_text(envelope, 5)!r}"
        )
    body_item = envelope.get(4)
    if body_item is None or body_item[0] != 2:
        raise RuntimeError("LLBot 按钮点击 OIDB 响应缺少 body")
    try:
        body = _decode_pb_fields(bytes(body_item[1]))
    except ValueError as exc:
        raise RuntimeError(f"LLBot 按钮点击响应 body 无效：{exc}") from exc
    try:
        click_result = _pb_uint(body, 3)
    except ValueError as exc:
        raise RuntimeError(f"LLBot 按钮点击响应 body 无效：{exc}") from exc
    if click_result:
        raise RuntimeError(
            f"LLBot 按钮点击被 QQ 拒绝：result={click_result} "
            f"error={_pb_text(body, 5)!r} prompt={_pb_text(body, 4)!r}"
        )
    return {
        "status": "ok",
        "retcode": 0,
        "provider": "llbot_send_pb",
        "result": int(click_result),
    }
