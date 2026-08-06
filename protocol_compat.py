from __future__ import annotations

import hashlib
from typing import Any


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
