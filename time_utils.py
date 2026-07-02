# 模块：时间工具
from __future__ import annotations
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING_TZ = None


def fmt_ts(ts: float | int | None, fallback: str = "未设置") -> str:

    try:
        ts = float(ts or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return fallback
    dt = datetime.fromtimestamp(ts, BEIJING_TZ) if BEIJING_TZ else datetime.fromtimestamp(ts)
    return f"{dt.year}年{dt.month}月{dt.day}日 {dt.hour:02d}时{dt.minute:02d}分"


def fmt_ts_compact(ts: float | int | None, fallback: str = "未设置") -> str:

    try:
        ts = float(ts or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return fallback
    dt = datetime.fromtimestamp(ts, BEIJING_TZ) if BEIJING_TZ else datetime.fromtimestamp(ts)
    return f"{dt:%Y-%m-%d %H:%M}"
