# market_price_server/server.py
# -*- coding: utf-8 -*-
"""
坊市价格中心服务示例。

运行：
    pip install fastapi uvicorn
    export MARKET_WRITE_TOKEN="改成你的上传密钥"
    uvicorn server:app --host 0.0.0.0 --port 8808

接口：
    GET  /api/prices/latest            公开读取最新价格
    POST /api/prices/bulk              上传/合并价格，需 X-API-Key
    GET  /health                       健康检查
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DATA_PATH = Path(os.getenv("MARKET_PRICE_FILE", "./market_prices.json")).resolve()
WRITE_TOKEN = os.getenv("MARKET_WRITE_TOKEN", "change-me")

app = FastAPI(title="XiaoXiuxian Market Price Service", version="1.0.0")


class PriceItem(BaseModel):
    price: int = Field(gt=0)
    source: str = "unknown"
    updated_at: float | None = None


class BulkPricePayload(BaseModel):
    source: str = "unknown"
    items: Dict[str, PriceItem]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_market_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def load_prices() -> dict:
    if not DATA_PATH.exists():
        return {"updated_at": 0, "items": {}}
    with DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"updated_at": 0, "items": {}}
    data.setdefault("updated_at", 0)
    data.setdefault("items", {})
    return data


@app.get("/health")
def health() -> dict:
    return {"ok": True, "items": len(load_prices().get("items", {}))}


@app.get("/api/prices/latest")
def latest_prices() -> dict:
    """公开读取接口。其他用户只需要配置这个 URL。"""
    return load_prices()


@app.post("/api/prices/bulk")
def upload_prices(payload: BulkPricePayload, x_api_key: str = Header(default="")) -> dict:
    """上传/合并价格。只允许可信采集插件调用。"""
    if not WRITE_TOKEN or WRITE_TOKEN == "change-me":
        raise HTTPException(status_code=500, detail="服务端未配置 MARKET_WRITE_TOKEN")
    if x_api_key != WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="无效上传密钥")

    now = time.time()
    data = load_prices()
    items: Dict[str, Any] = data.setdefault("items", {})

    changed = 0
    for name, item in payload.items.items():
        clean_name = str(name or "").strip()
        if not clean_name:
            continue
        items[clean_name] = {
            "price": int(item.price),
            "source": item.source or payload.source or "unknown",
            "updated_at": float(item.updated_at or now),
        }
        changed += 1

    data["updated_at"] = now
    atomic_write_json(DATA_PATH, data)
    return {"ok": True, "changed": changed, "total": len(items), "updated_at": now}
