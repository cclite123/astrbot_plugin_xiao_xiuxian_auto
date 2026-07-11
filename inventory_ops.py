# 模块：一键上架与一键炼金
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
from urllib.parse import unquote
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from .market_price import MarketPriceProvider
except Exception:
    MarketPriceProvider = Any

CATEGORY_HERB = "药材"
CATEGORY_PILL = "丹药"
CATEGORY_EQUIP = "装备"
CATEGORY_ARTIFACT = "神物"
CATEGORY_PROP = "道具"
SUPPORTED_CATEGORIES = {CATEGORY_HERB, CATEGORY_PILL, CATEGORY_EQUIP, CATEGORY_ARTIFACT}


@dataclass
class InventoryItem:


    name: str
    count: int
    category: str
    equipped: bool = False


@dataclass
class InventoryJob:


    op: str
    category: str
    phase: str = "COLLECTING"
    current_page: int = 1
    total_pages: int = 1
    items: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    current: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_command_ts: float = 0.0
    stats: Dict[str, Any] = field(default_factory=dict)


class InventoryOpsController:


    RE_PAGE = re.compile(r"第\s*(?P<cur>\d+)\s*页\s*/\s*共\s*(?P<total>\d+)\s*页")
    RE_NAME = re.compile(r"(?:名字|名称)\s*[:：]\s*(?P<name>[^\n\r\[\]（）()]+)")
    RE_COUNT = re.compile(r"(?:拥有)?数量\s*[:：]?\s*(?P<count>\d+)")

    SUCCESS_MARKET = "物品成功上架坊市"
    SUCCESS_ALCHEMY = "炼金成功"
    FAIL_KEYWORDS = (
        "失败", "无法", "不能", "不可", "不存在", "数量不足", "没有该物品", "未拥有",
        "已装备", "背包中没有", "参数错误", "格式错误", "冷却", "稍后再试",
    )

    def __init__(self, *, official_qq: str, market_price: Optional[MarketPriceProvider] = None,
                 config: Optional[dict] = None, runtime_path: str = "", logger=None):
        self.official_qq = str(official_qq)
        self.market_price = market_price
        self.log = logger
        self.runtime_path = str(runtime_path or "").strip()
        cfg = dict(config or {})

        self.enabled = bool(cfg.get("enabled", True))

        self.max_market_price = int(cfg.get("max_market_price", cfg.get("max_price_wan", 5000)))






        raw_price_format = str(
            cfg.get("market_command_price_format", "lingshi")
            or "lingshi"
        ).strip().lower()
        alias_map = {
            "灵石": "lingshi",
            "number": "lingshi",
            "数字": "lingshi",
            "pure_number": "lingshi",
            "pure-number": "lingshi",
            "纯数字": "lingshi",
            "wan": "wan_suffix",
            "万": "wan_suffix",
            "wan_suffix": "wan_suffix",
            "raw": "raw",
        }
        self.market_command_price_format = alias_map.get(raw_price_format, raw_price_format)
        if self.market_command_price_format not in {"wan_suffix", "lingshi", "raw"}:
            self.market_command_price_format = "lingshi"
        self.send_interval_sec = max(0.0, float(cfg.get("send_interval_sec", 1.0)))
        self.page_timeout_sec = max(10.0, float(cfg.get("page_timeout_sec", 30.0)))
        self.action_timeout_sec = max(10.0, float(cfg.get("action_timeout_sec", 30.0)))
        self.max_pages = max(1, int(cfg.get("max_pages", 30)))

        wl = cfg.get("alchemy_whitelist", {}) or {}
        bl = cfg.get("alchemy_blacklist", {}) or {}

        self.alchemy_whitelist = {
            CATEGORY_PILL: self._normalize_name_set(wl.get(CATEGORY_PILL) or wl.get("danyao") or wl.get("丹药") or []),
        }
        self.alchemy_blacklist = {
            CATEGORY_EQUIP: self._normalize_name_set(bl.get(CATEGORY_EQUIP) or bl.get("zhuangbei") or bl.get("装备") or []),
            CATEGORY_ARTIFACT: self._normalize_name_set(bl.get(CATEGORY_ARTIFACT) or bl.get("shenwu") or bl.get("神物") or []),
        }
        self._load_runtime_rules()

        self.jobs: Dict[str, InventoryJob] = {}
        self._locks: Dict[str, asyncio.Lock] = {}


    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    def _warn(self, msg: str) -> None:
        if self.log:
            self.log.warning(msg)

    def _load_runtime_rules(self) -> None:

        if not self.runtime_path or not os.path.exists(self.runtime_path):
            return
        try:
            with open(self.runtime_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            wl = data.get("alchemy_whitelist", {}) if isinstance(data, dict) else {}
            bl = data.get("alchemy_blacklist", {}) if isinstance(data, dict) else {}
            if isinstance(wl, dict):
                self.alchemy_whitelist[CATEGORY_PILL] = self._normalize_name_set(wl.get(CATEGORY_PILL) or wl.get("丹药") or [])
            if isinstance(bl, dict):
                self.alchemy_blacklist[CATEGORY_EQUIP] = self._normalize_name_set(bl.get(CATEGORY_EQUIP) or bl.get("装备") or [])
                self.alchemy_blacklist[CATEGORY_ARTIFACT] = self._normalize_name_set(bl.get(CATEGORY_ARTIFACT) or bl.get("神物") or [])
        except Exception as e:
            self._warn(f"[inventory_ops] 读取运行时名单失败：{e}")

    def _save_runtime_rules(self) -> None:

        if not self.runtime_path:
            return
        data = {
            "alchemy_whitelist": {CATEGORY_PILL: sorted(self.alchemy_whitelist.get(CATEGORY_PILL, set()))},
            "alchemy_blacklist": {
                CATEGORY_EQUIP: sorted(self.alchemy_blacklist.get(CATEGORY_EQUIP, set())),
                CATEGORY_ARTIFACT: sorted(self.alchemy_blacklist.get(CATEGORY_ARTIFACT, set())),
            },
        }
        try:
            os.makedirs(os.path.dirname(self.runtime_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp_inventory_ops_", dir=os.path.dirname(self.runtime_path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.runtime_path)
        except Exception as e:
            self._warn(f"[inventory_ops] 保存运行时名单失败：{e}")

    def set_alchemy_rules(self, whitelist_pill, blacklist_equip, blacklist_artifact) -> None:
        """整体设置炼金白/黑名单并持久化（供 WebUI Page 调用）。"""
        self.alchemy_whitelist[CATEGORY_PILL] = self._normalize_name_set(whitelist_pill)
        self.alchemy_blacklist[CATEGORY_EQUIP] = self._normalize_name_set(blacklist_equip)
        self.alchemy_blacklist[CATEGORY_ARTIFACT] = self._normalize_name_set(blacklist_artifact)
        self._save_runtime_rules()


    @staticmethod
    def normalize_name(name: str) -> str:
        name = str(name or "").strip()
        name = re.sub(r"\s+", "", name)
        name = name.replace("：", ":").replace("，", ",")
        return name

    @classmethod
    def _normalize_name_set(cls, names) -> set[str]:
        if isinstance(names, str):
            raw = re.split(r"[,，、\s]+", names)
        elif isinstance(names, (list, tuple, set)):
            raw = list(names)
        else:
            raw = []
        return {cls.normalize_name(x) for x in raw if cls.normalize_name(x)}

    def add_whitelist(self, category: str, names: List[str]) -> str:
        category = self._normalize_category(category)
        if category != CATEGORY_PILL:
            return "❌ 当前仅丹药品类使用炼金白名单。"
        bucket = self.alchemy_whitelist.setdefault(CATEGORY_PILL, set())
        added = []
        for name in names:
            n = self.normalize_name(name)
            if n and n not in bucket:
                bucket.add(n)
                added.append(n)
        self._save_runtime_rules()
        return f"✅ 已添加丹药炼金白名单：{'、'.join(added) if added else '无新增'}"

    def remove_whitelist(self, category: str, names: List[str]) -> str:
        category = self._normalize_category(category)
        if category != CATEGORY_PILL:
            return "❌ 当前仅丹药品类使用炼金白名单。"
        bucket = self.alchemy_whitelist.setdefault(CATEGORY_PILL, set())
        removed = []
        for name in names:
            n = self.normalize_name(name)
            if n in bucket:
                bucket.remove(n)
                removed.append(n)
        self._save_runtime_rules()
        return f"✅ 已删除丹药炼金白名单：{'、'.join(removed) if removed else '无匹配'}"

    def add_blacklist(self, category: str, names: List[str]) -> str:
        category = self._normalize_category(category)
        if category not in {CATEGORY_EQUIP, CATEGORY_ARTIFACT}:
            return "❌ 当前仅装备、神物品类使用炼金黑名单。"
        bucket = self.alchemy_blacklist.setdefault(category, set())
        added = []
        for name in names:
            n = self.normalize_name(name)
            if n and n not in bucket:
                bucket.add(n)
                added.append(n)
        self._save_runtime_rules()
        return f"✅ 已添加{category}炼金黑名单：{'、'.join(added) if added else '无新增'}"

    def remove_blacklist(self, category: str, names: List[str]) -> str:
        category = self._normalize_category(category)
        if category not in {CATEGORY_EQUIP, CATEGORY_ARTIFACT}:
            return "❌ 当前仅装备、神物品类使用炼金黑名单。"
        bucket = self.alchemy_blacklist.setdefault(category, set())
        removed = []
        for name in names:
            n = self.normalize_name(name)
            if n in bucket:
                bucket.remove(n)
                removed.append(n)
        self._save_runtime_rules()
        return f"✅ 已删除{category}炼金黑名单：{'、'.join(removed) if removed else '无匹配'}"

    def list_rules(self) -> str:
        pill_wl = sorted(self.alchemy_whitelist.get(CATEGORY_PILL, set()))
        equip_bl = sorted(self.alchemy_blacklist.get(CATEGORY_EQUIP, set()))
        art_bl = sorted(self.alchemy_blacklist.get(CATEGORY_ARTIFACT, set()))
        return (
            "📋 【一键炼金名单】\n"
            f"丹药白名单：{'、'.join(pill_wl) if pill_wl else '空'}\n"
            f"装备黑名单：{'、'.join(equip_bl) if equip_bl else '空'}\n"
            f"神物黑名单：{'、'.join(art_bl) if art_bl else '空'}\n"
            f"上架价格格式：{self.market_command_price_format}\n"
            "说明：药材无白名单限制；装备/神物命中黑名单会跳过。"
        )


    @staticmethod
    def _normalize_category(category: str) -> str:
        category = str(category or "").strip()
        alias = {
            "药材": CATEGORY_HERB,
            "丹药": CATEGORY_PILL,
            "装备": CATEGORY_EQUIP,
            "神物": CATEGORY_ARTIFACT,
            "道具": CATEGORY_PROP,
            "danyao": CATEGORY_PILL,
            "zhuangbei": CATEGORY_EQUIP,
            "shenwu": CATEGORY_ARTIFACT,
        }
        return alias.get(category, category)

    def _init_stats(self, op: str, category: str) -> Dict[str, Any]:
        base = {
            "category": category,
            "success": 0,
            "failed": 0,
            "failed_items": [],
            "success_items": [],
            "skip_prop": 0,
            "skip_equipped": 0,
        }
        if op == "market":
            base.update({"skip_high_price": 0, "skip_no_price": 0})
        else:
            base.update({"skip_not_whitelist": 0, "skip_blacklist": 0})
        return base

    async def cmd_start_market(self, key: str, category: str, send_cb) -> str:
        return await self._start_job(key, "market", category, send_cb)

    async def cmd_start_alchemy(self, key: str, category: str, send_cb) -> str:
        return await self._start_job(key, "alchemy", category, send_cb)

    async def _start_job(self, key: str, op: str, category: str, send_cb) -> str:
        category = self._normalize_category(category)
        if not self.enabled:
            return "🛑 一键上架/炼金模块已关闭。"
        if category not in SUPPORTED_CATEGORIES:
            return "❌ 品类错误，仅支持：药材、装备、神物、丹药。"

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            old = self.jobs.get(key)
            if old and old.phase in {"COLLECTING", "EXECUTING"}:
                return f"⚠️ 当前已有一键{'上架' if old.op == 'market' else '炼金'}任务正在执行：{old.category}，请等待完成。"

            job = InventoryJob(op=op, category=category, stats=self._init_stats(op, category))
            self.jobs[key] = job

        first_cmd = self._bag_command(category, 1)
        await send_cb(f"@{self.official_qq} {first_cmd}")
        job.last_command_ts = job.updated_at = time.time()
        op_name = "上架" if op == "market" else "炼金"
        return (
            f"✅ 已启动一键{op_name}{category}流程\n"
            f"📦 正在拉取背包分页：{first_cmd}\n"
            "流程执行期间请不要同时启动其他一键上架/炼金任务。"
        )

    def _bag_command(self, category: str, page: int) -> str:
        suffix = "" if page <= 1 else str(page)
        if category == CATEGORY_HERB:
            return f"药材背包{suffix}"
        if category == CATEGORY_PILL:
            return f"丹药背包{suffix}"
        return f"我的背包{suffix}"


    async def on_official_text(self, key: str, text: str, send_cb) -> bool:
        job = self.jobs.get(key)
        if not job:
            return False
        text = self._cleanup_text(text)
        if not text:
            return False

        if job.phase == "COLLECTING":
            return await self._handle_collecting(key, job, text, send_cb)
        if job.phase == "EXECUTING":
            return await self._handle_executing(key, job, text, send_cb)
        return False

    async def _handle_collecting(self, key: str, job: InventoryJob, text: str, send_cb) -> bool:

        if not ("拥有数量" in text or "数量" in text or "名字" in text or "☆" in text or "第" in text and "共" in text and "页" in text):
            return False

        cur, total = self._parse_page_info(text, job.current_page)
        job.current_page = max(1, cur)
        job.total_pages = min(max(1, total), self.max_pages)

        for item in self._parse_items(text, job.category):
            self._merge_item(job, item)

        job.updated_at = time.time()

        if job.current_page < job.total_pages:
            next_page = job.current_page + 1
            job.current_page = next_page
            cmd = self._bag_command(job.category, next_page)
            await send_cb(f"@{self.official_qq} {cmd}")
            job.last_command_ts = time.time()
            return True


        await self._prepare_and_execute(key, job, send_cb)
        return True

    async def _handle_executing(self, key: str, job: InventoryJob, text: str, send_cb) -> bool:
        cur = job.current
        if not cur:
            return False
        name = cur.get("name", "")

        success_kw = self.SUCCESS_MARKET if job.op == "market" else self.SUCCESS_ALCHEMY
        if success_kw in text:
            await self._mark_current_done(key, job, True, "", send_cb)
            return True
        if any(k in text for k in self.FAIL_KEYWORDS):

            action_hint = "上架" if job.op == "market" else "炼金"
            if name and (name in text or action_hint in text):
                await self._mark_current_done(key, job, False, self._short_reason(text), send_cb)
                return True
        return False

    async def tick(self, key: str, send_cb) -> None:
        job = self.jobs.get(key)
        if not job:
            return
        now = time.time()
        if job.phase == "COLLECTING" and job.last_command_ts and now - job.last_command_ts > self.page_timeout_sec:
            await send_cb(f"⚠️ 一键{'上架' if job.op == 'market' else '炼金'}{job.category}拉取背包超时，流程已终止。")
            self.jobs.pop(key, None)
            return
        if job.phase == "EXECUTING" and job.current and job.last_command_ts and now - job.last_command_ts > self.action_timeout_sec:
            await self._mark_current_done(key, job, False, "等待小小回执超时", send_cb)


    async def _prepare_and_execute(self, key: str, job: InventoryJob, send_cb) -> None:
        items = [InventoryItem(**v) for v in job.items.values()]
        if job.op == "market":
            job.plan = await self._build_market_plan(job, items)
        else:
            job.plan = self._build_alchemy_plan(job, items)

        job.phase = "EXECUTING"
        job.current = None
        job.updated_at = time.time()

        if not job.plan:
            await self._finish_job(key, job, send_cb)
            return
        await self._send_next_action(key, job, send_cb)

    async def _build_market_plan(self, job: InventoryJob, items: List[InventoryItem]) -> List[Dict[str, Any]]:
        plan: List[Dict[str, Any]] = []
        for item in items:
            if item.category == CATEGORY_PROP:
                job.stats["skip_prop"] += 1
                continue
            if item.category != job.category:
                continue
            if item.category == CATEGORY_EQUIP and item.equipped:
                job.stats["skip_equipped"] += 1
                continue
            price_info = await self._get_market_price_info(item.name)
            if not price_info:

                job.stats["skip_no_price"] += 1
                continue

            price_wan = int(price_info.get("price_wan", 0) or 0)
            command_price = str(price_info.get("command_price", "") or "").strip()
            if price_wan <= 0 or not command_price:
                job.stats["skip_no_price"] += 1
                continue
            if price_wan > self.max_market_price:
                job.stats["skip_high_price"] += 1
                continue
            plan.append({
                "name": item.name,
                "count": item.count,
                "category": item.category,
                "price_wan": price_wan,
                "command_price": command_price,
                "cmd": f"确认坊市上架 {item.name} {command_price} {item.count}",
            })
        return plan

    def _build_alchemy_plan(self, job: InventoryJob, items: List[InventoryItem]) -> List[Dict[str, Any]]:
        plan: List[Dict[str, Any]] = []
        pill_wl = self.alchemy_whitelist.get(CATEGORY_PILL, set())
        equip_bl = self.alchemy_blacklist.get(CATEGORY_EQUIP, set())
        art_bl = self.alchemy_blacklist.get(CATEGORY_ARTIFACT, set())
        for item in items:
            if item.category == CATEGORY_PROP:
                job.stats["skip_prop"] += 1
                continue
            if item.category != job.category:
                continue
            norm = self.normalize_name(item.name)
            if item.category == CATEGORY_EQUIP and item.equipped:
                job.stats["skip_equipped"] += 1
                continue
            if item.category == CATEGORY_PILL and norm not in pill_wl:
                job.stats["skip_not_whitelist"] += 1
                continue
            if item.category == CATEGORY_EQUIP and norm in equip_bl:
                job.stats["skip_blacklist"] += 1
                continue
            if item.category == CATEGORY_ARTIFACT and norm in art_bl:
                job.stats["skip_blacklist"] += 1
                continue
            plan.append({
                "name": item.name,
                "count": item.count,
                "category": item.category,
                "cmd": f"炼金 {item.name} {item.count}",
            })
        return plan

    @staticmethod
    def _normalize_price_unit(unit: str) -> str:
        unit = str(unit or "").strip().lower()
        if unit in {"万", "w", "wan", "万元", "万灵石"}:
            return "万"
        if unit in {"灵石", "lingshi", "raw", "number", "数字"}:
            return "灵石"

        return "万"

    @staticmethod
    def _format_wan_number(value: float) -> str:
        if abs(value - int(value)) < 1e-9:
            return str(int(value))

        return f"{value:.2f}".rstrip("0").rstrip(".")

    async def _get_market_price_info(self, name: str) -> Optional[Dict[str, Any]]:





        if not self.market_price:
            return None
        try:
            raw_info = None
            if hasattr(self.market_price, "get_price_info"):
                raw_info = await self.market_price.get_price_info(name)
            if raw_info is None:
                price = await self.market_price.get_price(name)
                raw_info = {"price": price, "unit": "万"} if price is not None else None
            if not raw_info:
                return None

            raw_price = float(raw_info.get("price", 0) or 0)
            if raw_price <= 0:
                return None
            unit = self._normalize_price_unit(str(raw_info.get("unit", "万") or "万"))

            if unit == "万":
                price_wan = raw_price
                price_lingshi = int(round(raw_price * 10000))
            else:
                price_lingshi = int(round(raw_price))
                price_wan = raw_price / 10000.0

            if self.market_command_price_format == "raw":

                command_price = self._format_wan_number(raw_price)
            elif self.market_command_price_format == "lingshi":

                command_price = str(price_lingshi)
            else:

                command_price = f"{self._format_wan_number(price_wan)}万"

            return {
                "price_wan": int(math.ceil(price_wan)),
                "price_lingshi": price_lingshi,
                "command_price": command_price,
                "raw_price": raw_price,
                "unit": unit,
            }
        except Exception as e:
            self._warn(f"[inventory_ops] 获取坊市价格失败 name={name}: {e}")
            return None


    async def _send_next_action(self, key: str, job: InventoryJob, send_cb) -> None:
        if not job.plan:
            await self._finish_job(key, job, send_cb)
            return
        job.current = job.plan.pop(0)
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} {job.current['cmd']}")

    async def _mark_current_done(self, key: str, job: InventoryJob, ok: bool, reason: str, send_cb) -> None:
        cur = job.current or {}
        name = cur.get("name", "未知物品")
        count = int(cur.get("count", 0) or 0)
        if ok:
            job.stats["success"] += 1
            job.stats["success_items"].append({"name": name, "count": count})
        else:
            job.stats["failed"] += 1
            job.stats["failed_items"].append({"name": name, "count": count, "reason": reason or "未知原因"})
        job.current = None
        job.updated_at = time.time()
        if self.send_interval_sec > 0:
            await asyncio.sleep(self.send_interval_sec)
        await self._send_next_action(key, job, send_cb)

    async def _finish_job(self, key: str, job: InventoryJob, send_cb) -> None:
        self.jobs.pop(key, None)
        await send_cb(self._summary(job))

    def _summary(self, job: InventoryJob) -> str:
        op_name = "上架" if job.op == "market" else "炼金"
        s = job.stats
        lines = [f"📊 【一键{op_name}汇总】", f"本次{op_name}品类：{job.category}", f"成功{op_name}数量：{s.get('success', 0)}"]
        if job.op == "market":
            lines.append(f"因单价过高跳过数量：{s.get('skip_high_price', 0)}")
            if s.get("skip_no_price", 0):
                lines.append(f"因未匹配坊市价格跳过数量：{s.get('skip_no_price', 0)}")
            if job.category == CATEGORY_EQUIP:
                lines.append(f"因已装备跳过数量：{s.get('skip_equipped', 0)}")
            lines.append(f"上架失败数量：{s.get('failed', 0)}")
        else:
            success_items = s.get("success_items", []) or []
            if success_items:
                detail = "、".join(f"{x['name']}×{x['count']}" for x in success_items[:30])
                if len(success_items) > 30:
                    detail += f" 等{len(success_items)}项"
            else:
                detail = "无"
            lines.append(f"累计炼金物品明细：{detail}")
            if job.category == CATEGORY_PILL:
                lines.append(f"因不在白名单跳过数量：{s.get('skip_not_whitelist', 0)}")
            lines.append(f"因已黑名单跳过数量：{s.get('skip_blacklist', 0)}")
            if job.category == CATEGORY_EQUIP:
                lines.append(f"因已装备跳过数量：{s.get('skip_equipped', 0)}")
            lines.append(f"因道具品类跳过数量：{s.get('skip_prop', 0)}")
            lines.append(f"炼金失败数量：{s.get('failed', 0)}")
        failed_items = s.get("failed_items", []) or []
        if failed_items:
            detail = "、".join(f"{x['name']}({x.get('reason','失败')})" for x in failed_items[:10])
            if len(failed_items) > 10:
                detail += f" 等{len(failed_items)}项"
            lines.append(f"失败明细：{detail}")
        return "\n".join(lines)


    @staticmethod
    def _cleanup_text(text: str) -> str:
        text = str(text or "")
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"mqqapi://\S+", "", text)
        text = text.replace("\r", "\n")
        return text

    def _parse_page_info(self, text: str, fallback_page: int) -> Tuple[int, int]:
        m = self.RE_PAGE.search(text)
        if not m:
            return fallback_page, max(1, fallback_page)
        try:
            return int(m.group("cur")), int(m.group("total"))
        except Exception:
            return fallback_page, max(1, fallback_page)

    def _merge_item(self, job: InventoryJob, item: InventoryItem) -> None:
        if not item.name or item.count <= 0:
            return

        k = f"{self.normalize_name(item.name)}::{item.category}"
        old = job.items.get(k)
        if not old or int(item.count) > int(old.get("count", 0)):
            job.items[k] = {
                "name": item.name,
                "count": int(item.count),
                "category": item.category,
                "equipped": bool(item.equipped),
            }
        elif old and item.equipped:
            old["equipped"] = True

    def _parse_items(self, text: str, target_category: str) -> List[InventoryItem]:














        raw_text = str(text or "").replace("\r", "\n")
        raw_lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
        clean_lines = [self._cleanup_text(ln).strip() for ln in raw_lines]

        items: List[InventoryItem] = []
        section = target_category if target_category in {CATEGORY_HERB, CATEGORY_PILL} else ""
        cur: Optional[Dict[str, Any]] = None
        pending_line = ""
        pending_raw = ""

        def add_item(name: str, count: int, block: str, section_hint: str) -> None:
            name = self._clean_item_name(name)
            try:
                count = int(count or 0)
            except Exception:
                count = 0
            if not name or count <= 0:
                return
            category = self._classify_item(block, section_hint=section_hint, target_category=target_category)
            equipped = bool(re.search(r"已\s*装备|已穿戴|装备中|当前装备", block))
            items.append(InventoryItem(name=name, count=count, category=category, equipped=equipped))

        def flush_current() -> None:
            nonlocal cur
            if not cur:
                return
            name = str(cur.get("name", ""))
            count = int(cur.get("count", 0) or 0)
            block = "\n".join(cur.get("lines", []))
            add_item(name, count, block, str(cur.get("section", "")))
            cur = None

        for raw, line in zip(raw_lines, clean_lines):
            if not line:
                continue


            sec = self._detect_section(line)
            if sec and "拥有数量" not in line and "名字" not in line:
                flush_current()
                section = sec
                pending_line = ""
                pending_raw = ""
                continue


            m_name = self.RE_NAME.search(line)
            if m_name:
                flush_current()
                cur = {
                    "name": m_name.group("name"),
                    "count": 0,
                    "lines": [line],
                    "section": section,
                }
                m_count_same = self.RE_COUNT.search(line)
                if m_count_same:
                    cur["count"] = int(m_count_same.group("count"))
                pending_line = ""
                pending_raw = ""
                continue


            if cur:
                cur.setdefault("lines", []).append(line)
                if not cur.get("count"):
                    m_count = self.RE_COUNT.search(line)
                    if m_count:
                        cur["count"] = int(m_count.group("count"))

                if cur.get("count") and section == CATEGORY_PROP:
                    flush_current()
                continue


            m_count = self.RE_COUNT.search(line)
            if m_count:
                count = int(m_count.group("count"))
                block = "\n".join(x for x in [pending_line, line] if x)


                cmd_name, cmd_count = self._extract_inline_item_from_raw(raw)
                if cmd_count:
                    count = cmd_count
                name = cmd_name


                if not name:
                    name = self._extract_effect_name_from_raw(raw)



                if not name:
                    name = self._extract_name_from_display_line(pending_line, section_hint=section, target_category=target_category)
                if not name:
                    name = self._extract_name_from_display_line(line, section_hint=section, target_category=target_category)

                add_item(name, count, block, section)
                pending_line = ""
                pending_raw = ""
                continue


            if self._is_possible_display_name_line(line):
                pending_line = line
                pending_raw = raw

        flush_current()
        return items

    @staticmethod
    def _decode_inline_commands(raw: str) -> List[str]:

        raw = str(raw or "")
        cmds: List[str] = []
        for m in re.finditer(r"command=([^&\)]+)", raw):
            cmd = unquote(m.group(1))
            cmd = cmd.replace("+", " ").strip()
            if cmd:
                cmds.append(cmd)
        return cmds

    @classmethod
    def _extract_inline_item_from_raw(cls, raw: str) -> Tuple[str, int]:

        cmds = cls._decode_inline_commands(raw)

        for cmd in cmds:
            m = re.match(r"炼金\s*(?P<name>.+?)\s+(?P<count>\d+)\s*$", cmd)
            if m:
                return cls._clean_item_name(m.group("name")), int(m.group("count"))
        for cmd in cmds:
            m = re.match(r"坊市数据\s*(?P<name>.+?)\s*$", cmd)
            if m:
                return cls._clean_item_name(m.group("name")), 0
        for cmd in cmds:
            m = re.match(r"使用\s*(?P<name>.+?)\s*$", cmd)
            if m:
                return cls._clean_item_name(m.group("name")), 0
        return "", 0

    @classmethod
    def _extract_effect_name_from_raw(cls, raw: str) -> str:

        for cmd in cls._decode_inline_commands(raw):
            m = re.match(r"查看效果\s*(?P<name>.+?)\s*$", cmd)
            if m:
                return cls._clean_item_name(m.group("name"))
        return ""

    @classmethod
    def _extract_name_from_display_line(cls, line: str, *, section_hint: str, target_category: str) -> str:

        s = str(line or "").strip()
        if not s:
            return ""
        s = re.sub(r"^@\S+", "", s).strip()
        s = re.sub(r"第\d+页/共\d+页.*$", "", s).strip()
        s = cls._clean_item_name(s)
        if not s:
            return ""


        if section_hint == CATEGORY_ARTIFACT or target_category == CATEGORY_ARTIFACT:
            s = re.sub(
                r"^(?:人阶|黄阶|玄阶|地阶|天阶|仙阶)(?:下品|中品|上品|极品)?",
                "",
                s,
            )
            s = re.sub(
                r"(?:人阶|黄阶|玄阶|地阶|天阶|仙阶)(?:下品|中品|上品|极品)?(?:辅修功法|功法|神通|心法|术法|秘术|法门)$",
                "",
                s,
            )
            return cls._clean_item_name(s)


        if section_hint == CATEGORY_EQUIP or target_category == CATEGORY_EQUIP:
            s = re.sub(
                r"^(?:下品|中品|上品|极品|无上)?(?:普通|黄阶|玄阶|地阶|天阶|仙阶|通天)?(?:法器|仙器|灵器|宝器|魔器|神器|道器)",
                "",
                s,
            )
            return cls._clean_item_name(s)
        return s

    @staticmethod
    def _is_possible_display_name_line(line: str) -> bool:

        s = re.sub(r"\s+", "", str(line or ""))
        if not s:
            return False
        banned = (
            "背包", "持有灵石", "物品功效", "炼金", "坊市数据", "拥有数量", "查看效果", "上一页", "下一页",
            "第", "名字", "名称", "品级", "core.event_bus", "小小/", "锻造背包",
        )
        if any(x in s for x in banned):
            return False
        if s.startswith("@"):
            return False

        if "☆" in s or "------" in s:
            return False
        return len(s) <= 60

    @staticmethod
    def _clean_item_name(name: str) -> str:
        name = str(name or "").strip()
        name = re.sub(r"\s+", "", name)
        name = re.sub(r"---.*$", "", name)
        name = re.sub(r"炼金.*$", "", name)
        name = re.sub(r"坊市数据.*$", "", name)
        name = re.sub(r"物品功效.*$", "", name)
        name = re.sub(r"^名字[:：]", "", name)
        name = re.sub(r"[（(]\s*已\s*[装穿]备\s*[)）].*$", "", name)
        name = re.sub(r"[-－]*\s*数量\s*[:：]?\s*\d+.*$", "", name)
        name = name.strip("[]【】()（）:：| ")
        return name

    @staticmethod
    def _detect_section(line: str) -> str:
        s = re.sub(r"\s+", "", str(line or ""))
        if not s:
            return ""

        if "已装备" in s or "未装备" in s or "当前装备" in s:
            return ""
        if len(s) <= 30:
            if any(x in s for x in ("装备背包", "装备列表", "【装备】", "装备：", "装备")):
                return CATEGORY_EQUIP
            if any(x in s for x in ("神物背包", "神物列表", "【神物】", "神物：", "神物")):
                return CATEGORY_ARTIFACT
            if any(x in s for x in ("道具背包", "道具列表", "【道具】", "道具：", "道具")):
                return CATEGORY_PROP
            if any(x in s for x in ("功法背包", "功法列表", "【功法】", "功法：", "功法")):
                return CATEGORY_ARTIFACT
        return ""

    def _classify_item(self, block: str, *, section_hint: str, target_category: str) -> str:
        if target_category in {CATEGORY_HERB, CATEGORY_PILL}:
            return target_category
        text = re.sub(r"\s+", "", block or "")
        artifact_like = any(k in text for k in ("神通", "功法", "辅修功法", "心法", "秘术", "法门"))


        if "神物" in text or section_hint == CATEGORY_ARTIFACT or artifact_like:
            return CATEGORY_ARTIFACT
        if "道具" in text or section_hint == CATEGORY_PROP:
            return CATEGORY_PROP

        if (
            section_hint == CATEGORY_EQUIP
            or any(k in text for k in ("已装备", "未装备", "当前装备", "装备中", "法器", "仙器", "灵器", "宝器", "魔器", "神器", "道器"))
        ):
            return CATEGORY_EQUIP

        if target_category in {CATEGORY_EQUIP, CATEGORY_ARTIFACT}:
            return target_category if section_hint in {CATEGORY_EQUIP, CATEGORY_ARTIFACT} else "未知"
        return "未知"

    @staticmethod
    def _short_reason(text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return text[:80] if text else "失败"
