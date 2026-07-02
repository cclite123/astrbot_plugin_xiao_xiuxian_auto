# market_price_server/uploader_example.py
# -*- coding: utf-8 -*-
"""其他坊市采集插件可参考的上传代码。"""
from __future__ import annotations

import json
import time
import urllib.request


def upload_prices(api_url: str, api_key: str, prices: dict[str, int], source: str = "坊市采集插件") -> dict:
    payload = {
        "source": source,
        "items": {
            name: {"price": int(price), "source": source, "updated_at": time.time()}
            for name, price in prices.items()
            if name and int(price) > 0
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url.rstrip("/") + "/api/prices/bulk",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": "xiao-xiuxian-market-uploader",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    result = upload_prices(
        api_url="http://127.0.0.1:8808",
        api_key="改成你的上传密钥",
        prices={"五指拳心剑": 888888, "真龙九变": 1200000},
    )
    print(result)
