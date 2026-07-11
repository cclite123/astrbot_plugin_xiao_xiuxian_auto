from __future__ import annotations

import asyncio
import html
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

try:
    import yaml
except ImportError:
    yaml = None

try:
    from .inventory_ops import InventoryOpsController, CATEGORY_HERB
except Exception:
    try:
        from inventory_ops import InventoryOpsController, CATEGORY_HERB
    except Exception:
        InventoryOpsController = None
        CATEGORY_HERB = "药材"


FIXED_PILL_SALE_PRICE: Dict[str, float] = {
    "摄魂鬼丸": 130,
    "化煞魔丸": 160,
    "素心真丸": 190,
    "灭神古丸": 220,
    "静禅魔丸": 250,
    "地仙玄丸": 280,
    "消冰宝丸": 310,
    "无涯鬼丸": 340,
    "太一仙丸": 370,
}


@dataclass
class MaterialReq:
    role: str
    name: str = ""
    qty: int = 1
    wildcard_prop: str = ""


@dataclass
class Recipe:
    pill: str
    grade: str
    furnace: str
    materials: List[MaterialReq]
    raw: str = ""


@dataclass
class AutoAlchemyJob:
    phase: str = "IDLE"
    report_mode: str = "calculate"
    mode: str = "batch"
    current_page: int = 1
    max_page: int = 8
    scan_pages: List[int] = field(default_factory=list)
    scan_index: int = 0

    prices: Dict[str, float] = field(default_factory=dict)
    buy_commands: Dict[str, str] = field(default_factory=dict)
    pages_by_name: Dict[str, int] = field(default_factory=dict)
    pages_seen: List[int] = field(default_factory=list)
    page_counts: Dict[int, int] = field(default_factory=dict)

    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_command_ts: float = 0.0


    target_pill: str = ""
    target_rounds: int = 1
    yield_count: int = 6
    min_profit: float = 100.0

    batch_selected: List[Dict[str, Any]] = field(default_factory=list)
    batch_purchase_plan: List[Dict[str, Any]] = field(default_factory=list)
    batch_formula_texts: List[str] = field(default_factory=list)
    batch_report: str = ""

    batch_buy_queue: List[Dict[str, Any]] = field(default_factory=list)
    batch_buy_index: int = 0
    batch_current_item: Dict[str, Any] = field(default_factory=dict)
    batch_buy_expected: int = 0
    batch_buy_sent: int = 0
    batch_success_count: int = 0
    batch_fail_count: int = 0
    batch_buy_results: List[str] = field(default_factory=list)
    batch_buy_started_at: float = 0.0
    batch_busy_retry_done: bool = False

    purchased_counts: Dict[str, int] = field(default_factory=dict)
    failed_counts: Dict[str, int] = field(default_factory=dict)
    deferred_retry_items: List[Dict[str, Any]] = field(default_factory=list)
    deferred_retry_names: List[str] = field(default_factory=list)
    retry_after_batch_active: bool = False
    retry_after_batch_started: bool = False
    retry_refresh_pages: List[int] = field(default_factory=list)
    round_refreshed_rounds: List[int] = field(default_factory=list)
    round_refreshing_round: int = 0
    round_refreshed_page_keys: List[str] = field(default_factory=list)
    round_refreshing_page: int = 0
    abandoned_pills: Dict[str, str] = field(default_factory=dict)
    skipped_alchemy: List[str] = field(default_factory=list)
    overbuy_counts: Dict[str, int] = field(default_factory=dict)
    overbuy_value: float = 0.0

    batch_reserve_candidates: List[Dict[str, Any]] = field(default_factory=list)
    formula_price_baselines: Dict[str, Dict[str, float]] = field(default_factory=dict)

    refresh_item_name: str = ""
    refresh_item_page: int = 0
    refresh_old_price: float = 0.0

    alchemy_queue: List[Dict[str, Any]] = field(default_factory=list)
    alchemy_index: int = 0
    alchemy_sent: int = 0
    alchemy_success: int = 0
    alchemy_results: List[str] = field(default_factory=list)
    alchemy_started_at: float = 0.0

    phase_before_pause: str = ""
    paused_reason: str = ""
    paused_at: float = 0.0
    fast_start_note: str = ""

    backpack_counts: Dict[str, int] = field(default_factory=dict)
    backpack_pages_seen: List[int] = field(default_factory=list)
    backpack_total_pages: int = 1

    herb_buy_rounds: int = 1
    herb_buy_current_round: int = 0
    herb_buy_scan_index: int = 0
    herb_buy_buy_queue: List[Dict[str, Any]] = field(default_factory=list)
    herb_buy_buy_index: int = 0
    herb_buy_current_item: Dict[str, Any] = field(default_factory=dict)
    herb_buy_bought: List[str] = field(default_factory=list)
    herb_buy_failed: List[str] = field(default_factory=list)
    herb_buy_total_success: int = 0
    herb_buy_total_fail: int = 0
    herb_buy_page_items: List[Dict[str, Any]] = field(default_factory=list)
    herb_buy_page_item_index: int = 0

    dynamic_buy_queue: List[Dict[str, Any]] = field(default_factory=list)
    dynamic_buy_index: int = 0
    dynamic_buy_current_item: Dict[str, Any] = field(default_factory=dict)
    dynamic_buy_success: int = 0
    dynamic_buy_fail: int = 0
    dynamic_purchased: Dict[str, int] = field(default_factory=dict)
    dynamic_busy_retry_done: bool = False


class AutoAlchemyOptimizer:

    RE_RECIPE = re.compile(
        r"配方主药(?P<main>.+?)(?P<main_qty>\d+)"
        r"药引(?P<guide>.+?)"
        r"辅药(?P<assist>.+?)(?P<assist_qty>\d+)"
        r"丹炉(?P<furnace>.+?)~(?P<pill>.+?)="
    )
    PRICE_LABEL_RE = re.compile(
        r"(?:坊市售价|当前售价|市场售价|出售价格|售卖价格|售价|单价|坊市价|市场价|价格|价钱)\s*[:：=]?\s*"
        r"(?P<price>\d+(?:\.\d+)?)\s*(?P<unit>万|w|W|灵石)?"
    )
    ANY_PRICE_RE = re.compile(r"(?P<price>\d+(?:\.\d+)?)\s*(?P<unit>万|w|W|灵石)")
    ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff\u2060-\u206f]")
    MARKET_INLINE_RE = re.compile(
        r"价格\s*[:：]\s*(?P<price>\d+(?:\.\d+)?)\s*(?P<unit>万|w|W|灵石)?\s*"
        r"\[(?P<name>[^\]]+)\]\((?P<url>mqqapi://aio/inlinecmd\?[^)]*)\)",
        re.S,
    )
    MARKET_LINK_RE = re.compile(r"\[(?P<name>[^\]]+)\]\((?P<url>mqqapi://aio/inlinecmd\?[^)]*)\)", re.S)
    FORMULA_REUSE_PRICE_TOLERANCE = 100.0

    def __init__(
        self,
        *,
        official_qq: str,
        recipe_path: str,
        snapshot_path: str = "",
        page_index_path: str = "",
        config: Optional[dict] = None,
        logger=None,
    ):
        cfg = dict(config or {})
        self.official_qq = str(official_qq)
        self.recipe_path = str(recipe_path or "").strip()
        self.snapshot_path = str(snapshot_path or "").strip()
        if page_index_path:
            self.page_index_path = str(page_index_path)
        elif self.snapshot_path:
            self.page_index_path = os.path.join(os.path.dirname(self.snapshot_path), "alchemy_page_index.json")
        else:
            self.page_index_path = ""
        self.log = logger

        self.enabled = bool(cfg.get("enabled", True))
        self.max_page = max(1, int(cfg.get("market_pages", 8)))
        self.page_timeout_sec = max(8.0, float(cfg.get("page_timeout_sec", 30.0)))
        self.send_interval_sec = max(0.0, float(cfg.get("send_interval_sec", 1.2)))
        self.max_page_retries = max(0, int(cfg.get("max_page_retries", 1)))
        self.default_yield_count = min(7, max(1, int(cfg.get("default_yield_count", 6))))
        self.min_profit_6pill = float(cfg.get("min_profit_6pill", cfg.get("min_profit", 100)))
        self.batch_buy_enabled = bool(cfg.get("batch_buy_enabled", True))
        self.batch_buy_send_interval_sec = max(0.0, float(cfg.get("batch_buy_send_interval_sec", self.send_interval_sec)))
        self.max_profitable_report_count = max(1, int(cfg.get("max_profitable_report_count", 30)))
        self.purchase_response_timeout_sec = max(5.0, float(cfg.get("purchase_response_timeout_sec", cfg.get("captcha_wait_sec", 30.0))))
        self.busy_retry_delay_sec = max(1.0, float(cfg.get("busy_retry_delay_sec", 15.0)))
        self.target_unknown_price_execute = bool(cfg.get("target_unknown_price_execute", True))
        self.alchemy_confirm_timeout_sec = max(5.0, float(cfg.get("alchemy_confirm_timeout_sec", 20.0)))
        self.alchemy_send_interval_sec = max(0.0, float(cfg.get("alchemy_send_interval_sec", self.send_interval_sec)))
        self.page_index_cache_enabled = bool(cfg.get("page_index_cache_enabled", True))
        self.target_mode_plan_lock = bool(cfg.get("target_mode_plan_lock", False))
        self.batch_mode_plan_lock = bool(cfg.get("batch_mode_plan_lock", True))
        self.batch_repeat_until_threshold = bool(cfg.get("batch_repeat_until_threshold", True))
        self.max_batch_formula_count = max(0, int(cfg.get("max_batch_formula_count", cfg.get("max_batch_pill_count", 6))))
        self.max_formula_per_pill = max(1, int(cfg.get("max_formula_per_pill", 6)))
        self.batch_fast_start_from_snapshot = bool(cfg.get("batch_fast_start_from_snapshot", True))
        self.batch_snapshot_max_age_sec = max(0, int(cfg.get("batch_snapshot_max_age_sec", 21600)))
        self.batch_refresh_selected_pages = bool(cfg.get("batch_refresh_selected_pages", True))
        self.retry_failed_after_batch = bool(cfg.get("retry_failed_after_batch", True))
        self.multi_round_buy_enabled = bool(cfg.get("multi_round_buy_enabled", True))
        self.refresh_pages_each_buy_round = bool(cfg.get("refresh_pages_each_buy_round", True))
        self.backpack_max_formula_count = max(1, int(cfg.get("backpack_max_formula_count", 6)))
        self.backpack_use_existing_as_free = bool(cfg.get("backpack_use_existing_as_free", True))
        self.backpack_min_profit_6pill = float(cfg.get("backpack_min_profit_6pill", 0))
        self.backpack_require_existing_material = bool(cfg.get("backpack_require_existing_material", True))
        self.dynamic_herb_buy_during_scan = bool(cfg.get("dynamic_herb_buy_during_market_scan", False))
        self.batch_mode_profit_threshold = float(cfg.get("batch_mode_profit_threshold", 50.0))
        self.use_backpack_for_batch_mode = bool(cfg.get("use_backpack_for_batch_mode", True))

        self.inventory_parser = None
        if InventoryOpsController is not None:
            try:
                self.inventory_parser = InventoryOpsController(official_qq=official_qq, config={"enabled": True}, logger=logger)
            except Exception:
                self.inventory_parser = None

        self.herb_props = self._build_herb_properties()
        self.herb_names = sorted(self.herb_props.keys(), key=len, reverse=True)
        self._recipes: List[Recipe] = []
        self._recipe_mtime = 0.0
        self.jobs: Dict[str, AutoAlchemyJob] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

        self.herb_max_prices_path = str(cfg.get("herb_max_prices_path", "") or "")
        if not self.herb_max_prices_path and self.snapshot_path:
            self.herb_max_prices_path = os.path.join(os.path.dirname(self.snapshot_path), "herb_max_prices.yaml")
        self.herb_max_prices: Dict[str, float] = self._load_herb_max_prices()

    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    def _warn(self, msg: str) -> None:
        if self.log:
            self.log.warning(msg)

    def _load_herb_max_prices(self) -> Dict[str, float]:
        """加载药材最高价 YAML 配置。"""
        if not self.herb_max_prices_path or not os.path.exists(self.herb_max_prices_path):
            return {}
        if yaml is None:
            self._warn("PyYAML 未安装，无法加载药材最高价配置")
            return {}
        try:
            with open(self.herb_max_prices_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            self._warn(f"加载药材最高价配置失败：{e}")
            return {}
        if not isinstance(data, dict):
            return {}
        result: Dict[str, float] = {}
        for grade_or_name, value in data.items():
            if isinstance(value, dict):
                for name, price in value.items():
                    try:
                        p = float(price)
                    except Exception:
                        continue
                    if p > 0:
                        result[self.normalize_name(str(name))] = p
            elif isinstance(value, (int, float)):
                try:
                    p = float(value)
                except Exception:
                    continue
                if p > 0:
                    result[self.normalize_name(str(grade_or_name))] = p
        return result

    def _save_herb_max_prices(self, prices: Dict[str, float]) -> None:
        """保存药材最高价 YAML 配置。"""
        if not self.herb_max_prices_path or yaml is None:
            return
        try:
            os.makedirs(os.path.dirname(self.herb_max_prices_path), exist_ok=True)
            with open(self.herb_max_prices_path, "w", encoding="utf-8") as f:
                yaml.dump(prices, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            self._warn(f"保存药材最高价配置失败：{e}")

    def set_herb_max_prices(self, prices: Dict[str, float]) -> None:
        """整体设置药材上限价并持久化（供 WebUI Page 调用）。"""
        cleaned: Dict[str, float] = {}
        for name, price in (prices or {}).items():
            try:
                p = float(price)
            except Exception:
                continue
            if p > 0:
                cleaned[self.normalize_name(str(name))] = p
        self.herb_max_prices = cleaned
        self._save_herb_max_prices(cleaned)

    @classmethod
    def normalize_name(cls, name: str) -> str:
        name = str(name or "").strip()
        name = cls.ZERO_WIDTH_RE.sub("", name)
        name = re.sub(r"\s+", "", name)
        return name.replace("：", ":").replace("，", ",")

    @classmethod
    def _build_herb_properties(cls) -> Dict[str, Dict[str, Any]]:
        blocks = {
            "养气": [
                ["恒心草", "红绫草", "罗犀草", "天青花"],
                ["五柳根", "天元果", "何首乌", "夜交藤"],
                ["紫猴花", "九叶芝", "幻心草", "鬼臼草"],
                ["血莲精", "鸡冠草", "银精芝", "玉髓芝"],
                ["地心火芝", "天蝉灵叶", "雪玉骨参", "腐骨灵花"],
                ["三叶青芝", "七彩月兰", "三尾风叶", "冰灵焰草"],
                ["地心淬灵乳", "天麻翡石精", "八角玄冰草", "奇茸通天菊"],
                ["木灵三针花", "鎏鑫天晶草", "檀芒九叶花", "坎水玄冰果"],
                ["离火梧桐芝", "尘磊岩麟果", "剑魄竹笋", "明心问道果"],
            ],
            "炼气": [
                ["宁心草", "凝血草", "银月花", "宁神花"],
                ["流莹草", "蛇涎果", "夏枯草", "百草露"],
                ["轻灵草", "龙葵", "弗兰草", "玄参"],
                ["菩提花", "乌稠木", "雪凝花", "龙纹草"],
                ["天灵果", "灯心草", "穿心莲", "龙鳞果"],
                ["白沉脂", "苦蔓藤", "血菩提", "诱妖草"],
                ["天问花", "渊血冥花", "芒焰果", "问道花"],
                ["阴阳黄泉花", "厉魂血珀", "浩淼水藤", "道蕴花"],
                ["太乙碧莹花", "森檀木", "炼心芝", "重元换血草"],
            ],
            "凝神": [
                ["地黄参", "火精枣", "剑芦", "七星草"],
                ["风灵花", "伏龙参", "凌风花", "补天芝"],
                ["枫香脂", "炼魂珠", "玄冰花", "炼血珠"],
                ["石龙芮", "锦地罗", "冰灵果", "玉龙参"],
                ["伴妖草", "剑心竹", "绝魂草", "月灵花"],
                ["混元果", "皇龙花", "天剑笋", "黑天麻"],
                ["血玉竹", "肠蚀草", "凤血果", "冰精芝"],
                ["狼桃", "霸王花", "太清玄灵草", "冥胎骨"],
                ["地龙干", "龙须藤", "鬼面花", "梧桐木"],
            ],
        }
        main_pattern = ["性平", "性平", "性寒", "性热"]
        guide_pattern = ["性平", "性平", "性热", "性寒"]
        out: Dict[str, Dict[str, Any]] = {}
        for assist_type, grades in blocks.items():
            for grade, names in enumerate(grades, 1):
                main_value = 2 ** (grade - 1)
                assist_value = 2 ** grade
                for idx, name in enumerate(names):
                    out[name] = {
                        "grade": grade,
                        "main": f"{main_pattern[idx]}{main_value}",
                        "guide": f"{guide_pattern[idx]}{main_value}",
                        "assist": f"{assist_type}{assist_value}",
                    }
        return out

    async def cmd_start(self, key: str, send_cb) -> str:
        if not self.enabled:
            return "🛑 自动炼丹模块已关闭。"
        if not self.recipe_path or not os.path.exists(self.recipe_path):
            return f"❌ 未找到丹方文件：{self.recipe_path}\n请把配方查询.txt 放到 data/alchemy_recipes.txt。"
        if self.inventory_parser is None:
            return "❌ 背包解析器不可用，无法读取药材背包。"
        initial_prices: Dict[str, float] = {}
        initial_buy_commands: Dict[str, str] = {}
        initial_pages: Dict[str, int] = {}
        scan_pages = list(range(1, self.max_page + 1))
        fast_note = ""
        if self.batch_fast_start_from_snapshot:
            cached = self._read_snapshot()
            if cached and cached.get("prices") and cached.get("pages_by_name"):
                initial_prices = dict(cached.get("prices") or {})
                initial_buy_commands = dict(cached.get("buy_commands") or {})
                initial_pages = dict(cached.get("pages_by_name") or {})
                pages = self._batch_pages_from_cached_snapshot(initial_prices, initial_buy_commands, initial_pages)
                if pages and self.batch_refresh_selected_pages:
                    scan_pages = pages
                    age = int(time.time()) - int(cached.get("updated_at") or 0) if cached.get("updated_at") else 0
                    fast_note = (
                        f"\n⚡ 已启用页码缓存快速启动：复用上次坊市价格快照计算，"
                        f"本轮只刷新预计用到的药材页：{','.join(map(str, scan_pages))}。"
                    )
                    if age > 0:
                        fast_note += f"\n快照距今约 {age} 秒；刷新页会覆盖对应药材的最新价格和购买指令。"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            old = self.jobs.get(key)
            if old and old.phase not in {"DONE", "STOPPED"}:
                return f"⚠️ 自动炼丹已有任务运行中（阶段：{old.phase}），请先等待完成、暂停或关闭。"
            job = AutoAlchemyJob(
                phase="COLLECTING",
                report_mode="batch_buy",
                mode="batch",
                max_page=self.max_page,
                yield_count=self.default_yield_count,
                min_profit=self.min_profit_6pill,
                scan_pages=scan_pages,
                scan_index=0,
                current_page=scan_pages[0] if scan_pages else 1,
            )
            job.prices.update({self.normalize_name(k): float(v) for k, v in initial_prices.items() if float(v or 0) > 0})
            job.buy_commands.update({self.normalize_name(k): self._normalize_buy_command(str(v or "")) for k, v in initial_buy_commands.items() if self._normalize_buy_command(str(v or ""))})
            for k, v in initial_pages.items():
                try:
                    page = int(v)
                except Exception:
                    continue
                if 1 <= page <= self.max_page:
                    job.pages_by_name[self.normalize_name(k)] = page
            job.fast_start_note = str(fast_note or "")
            self.jobs[key] = job
        dyn_note = ""
        if self.dynamic_herb_buy_during_scan and self.herb_max_prices:
            dyn_note = f"\n🛒 已启用坊市动态购买：遍历坊市时自动购买符合最高价的药材（{len(self.herb_max_prices)} 种已配置）。"
        await self._send_page(job, send_cb)
        return (
            "✅ 已启动自动炼丹\n"
            "📊 正在遍历坊市1-8页采集药材价格；采集完成后将读取背包药材进行智能抵扣。"
            f"{dyn_note}"
            f"{fast_note}"
        )

    async def cmd_backpack(self, key: str, send_cb) -> str:
        """根据药材背包已有药材匹配最优丹方，只使用背包药材，不做坊市购买，盈利>10万即可炼制。"""
        if not self.enabled:
            return "🛑 自动炼丹模块已关闭。"
        if not self.recipe_path or not os.path.exists(self.recipe_path):
            return f"❌ 未找到丹方文件：{self.recipe_path}\n请把配方查询.txt 放到 data/alchemy_recipes.txt。"
        if self.inventory_parser is None:
            return "❌ 背包解析器不可用，无法启动自动背包炼丹。"

        cached = self._read_snapshot()
        initial_prices: Dict[str, float] = {}
        initial_buy_commands: Dict[str, str] = {}
        initial_pages: Dict[str, int] = {}
        fast_note = ""
        if cached and cached.get("prices") and cached.get("pages_by_name"):
            initial_prices = dict(cached.get("prices") or {})
            initial_buy_commands = dict(cached.get("buy_commands") or {})
            initial_pages = dict(cached.get("pages_by_name") or {})
            fast_note = "\n⚡ 已加载上次坊市价格快照用于利润计算；背包匹配后只使用背包药材，不做坊市购买。"
        else:
            fast_note = "\n⚠️ 当前没有可用坊市价格快照，请先发送“开启自动炼丹”拉取坊市价格后再使用背包炼丹。"

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            old = self.jobs.get(key)
            if old and old.phase not in {"DONE", "STOPPED"}:
                return f"⚠️ 自动炼丹已有任务运行中（阶段：{old.phase}），请先等待完成、暂停或关闭。"
            job = AutoAlchemyJob(
                phase="BAG_COLLECTING",
                report_mode="backpack_buy",
                mode="backpack",
                max_page=self.max_page,
                yield_count=self.default_yield_count,
                min_profit=self.min_profit_6pill,
                current_page=1,
                scan_pages=[],
                scan_index=0,
            )
            job.prices.update({self.normalize_name(k): float(v) for k, v in initial_prices.items() if float(v or 0) > 0})
            job.buy_commands.update({self.normalize_name(k): self._normalize_buy_command(str(v or "")) for k, v in initial_buy_commands.items() if self._normalize_buy_command(str(v or ""))})
            for k, v in initial_pages.items():
                try:
                    page = int(v)
                except Exception:
                    continue
                if 1 <= page <= self.max_page:
                    job.pages_by_name[self.normalize_name(k)] = page
            job.fast_start_note = fast_note
            self.jobs[key] = job
        await self._send_backpack_page(job, send_cb)
        return (
            "✅ 已启动自动背包炼丹\n"
            "📦 正在读取药材背包；只使用背包已有药材，不做坊市购买。"
            f"{fast_note}"
        )

    async def cmd_target(self, key: str, args: str, send_cb) -> str:
        if not self.enabled:
            return "🛑 自动炼丹模块已关闭。"
        pill, qty = self._parse_target_args(args)
        if not pill or qty <= 0:
            return "❌ 用法：自动炼丹 丹药名称 数量\n示例：自动炼丹 化煞魔丸 3\n说明：数量代表炼制炉数。"
        if not self.recipe_path or not os.path.exists(self.recipe_path):
            return f"❌ 未找到丹方文件：{self.recipe_path}\n请把配方查询.txt 放到 data/alchemy_recipes.txt。"
        recipes_for_pill = [r for r in self._load_recipes() if r.pill == pill]
        if not recipes_for_pill:
            return f"❌ 指定丹药未在丹方文件中找到：{pill}。"
        unknown_price_note = ""
        if pill not in FIXED_PILL_SALE_PRICE:
            if not self.target_unknown_price_execute:
                return "❌ 指定丹药不在固定炼金售价表中，且 target_unknown_price_execute=false。当前支持固定利润计算的丹药：" + "、".join(FIXED_PILL_SALE_PRICE.keys())
            unknown_price_note = f"\n⚠️ {pill} 未配置固定炼金售价，本次将按药材总成本最低的可用丹方执行，不做6丹利润阈值判断。"
        pages = self._target_scan_pages(pill)
        page_note = "按页码索引只刷新相关页" if pages and len(pages) < self.max_page else "页码索引不完整，先遍历坊市1-8页"
        if not pages:
            pages = list(range(1, self.max_page + 1))
        msg = await self._start_collect_job(
            key,
            send_cb,
            report_mode="target_buy",
            mode="target",
            title="指定丹药自动炼丹",
            intro=f"✅ 已启动指定丹药自动炼丹：{pill} ×{qty}炉",
            scan_pages=pages,
            target_pill=pill,
            target_rounds=qty,
        )
        return msg + f"\n指定模式不使用方案锁；价格变化时只在 {pill} 内重新选方。\n{page_note}。"

    def _parse_target_args(self, args: str) -> Tuple[str, int]:
        raw = self.ZERO_WIDTH_RE.sub("", str(args or "")).strip()
        if not raw:
            return "", 0
        m = re.match(r"^(?P<pill>.+?)(?:\s+(?P<qty1>\d+)|[xX×*]\s*(?P<qty2>\d+))?$", raw)
        if not m:
            return self.normalize_name(raw), 1
        pill = self.normalize_name(m.group("pill") or "")
        qty = int(m.group("qty1") or m.group("qty2") or 1)
        return pill, max(1, qty)

    async def cmd_pause(self, key: str) -> str:
        job = self.jobs.get(key)
        if not job:
            return "自动炼丹：当前没有运行中的流程。"
        if job.phase == "PAUSED":
            return f"自动炼丹：当前已经暂停。\n原因：{job.paused_reason or '等待人工处理'}"
        job.phase_before_pause = job.phase
        job.phase = "PAUSED"
        job.paused_reason = "用户手动暂停"
        job.paused_at = job.updated_at = time.time()
        return "⏸️ 自动炼丹已暂停。\n当前队列和进度已保留；发送「继续自动炼丹」恢复，或发送「关闭自动炼丹」终止。"

    async def cmd_resume(self, key: str, send_cb) -> str:
        job = self.jobs.get(key)
        if not job:
            return "自动炼丹：当前没有可继续的流程。"
        if job.phase != "PAUSED":
            return f"自动炼丹：当前未暂停，阶段：{job.phase}。"
        prev = job.phase_before_pause or ""
        reason = job.paused_reason or ""
        job.paused_reason = ""
        job.phase_before_pause = ""
        job.updated_at = time.time()
        if prev in {"BATCH_BUY_WAIT", "BATCH_BUY_SENT", "BATCH_BUY_REFRESHING"}:
            item = job.batch_current_item or (job.batch_buy_queue[job.batch_buy_index] if 0 <= job.batch_buy_index < len(job.batch_buy_queue) else {})
            name = self.normalize_name(item.get("name", ""))
            page = int(item.get("page") or job.pages_by_name.get(name, 0) or 0)
            if name and page > 0:
                job.phase = "BATCH_BUY_REFRESHING"
                job.refresh_item_name = name
                job.refresh_item_page = page
                job.refresh_old_price = float(item.get("unit_price") or job.prices.get(name, 0) or 0)
                job.current_page = page
                job.scan_pages = [page]
                job.scan_index = 0
                job.retry_count = 0
                self.jobs[key] = job
                await self._send_page(job, send_cb)
                return f"▶️ 自动炼丹继续执行。\n暂停原因：{reason}\n正在重新查看 {name} 所在第 {page} 页，获取最新购买指令后继续。"
            job.phase = "BUYING"
            self.jobs[key] = job
            await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)
            return "▶️ 自动炼丹继续执行，正在处理下一个购买项。"
        if prev in {"ALCHEMY_WAIT", "ALCHEMY"}:
            job.phase = "ALCHEMY_WAIT"
            self.jobs[key] = job
            await self._send_next_alchemy_command(key, job, send_cb)
            return f"▶️ 自动炼丹继续执行。\n暂停原因：{reason}\n正在继续发送当前炼丹配方。"
        if prev == "COLLECTING_DYN_BUY_WAIT":
            job.dynamic_buy_fail += 1
            job.dynamic_buy_current_item = {}
            job.dynamic_buy_index += 1
            self.jobs[key] = job
            await self._send_next_dynamic_buy(key, job, send_cb)
            return f"▶️ 自动炼丹继续执行。\n暂停原因：{reason}\n已跳过当前动态购买，继续采集流程。"
        job.phase = prev or "BUYING"
        self.jobs[key] = job
        return f"▶️ 自动炼丹已恢复，当前阶段：{job.phase}。"

    async def cmd_stop(self, key: str) -> str:
        job = self.jobs.pop(key, None)
        if not job:
            return "自动炼丹：当前没有运行中的流程。"
        return "🛑 自动炼丹已关闭，本轮购买队列、炼丹队列和等待状态已清空。"

    async def cmd_auto_buy_herbs_start(self, key: str, rounds: int, send_cb) -> str:
        """开启自动购买药材。"""
        if not self.enabled:
            return "🛑 自动炼丹模块已关闭。"
        if not self.herb_max_prices:
            return "❌ 未找到药材最高价配置文件或配置为空。\n请将 herb_max_prices.yaml 放入 data/ 目录。"
        rounds = max(1, min(99, int(rounds or 1)))
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            old = self.jobs.get(key)
            if old and old.phase not in {"DONE", "STOPPED"}:
                return f"⚠️ 已有任务运行中（阶段：{old.phase}），请先等待完成或关闭。"
            job = AutoAlchemyJob(
                phase="HERB_BUY_SCANNING",
                report_mode="herb_buy",
                mode="herb_buy",
                max_page=self.max_page,
                current_page=1,
                scan_pages=list(range(1, self.max_page + 1)),
                scan_index=0,
                herb_buy_rounds=rounds,
                herb_buy_current_round=1,
                herb_buy_scan_index=0,
            )
            self.jobs[key] = job
        await self._send_herb_buy_page(job, send_cb)
        return (
            f"✅ 已启动自动购买药材\n"
            f"📦 轮次：{rounds}，当前第 1 轮\n"
            f"🔍 正在查看坊市药材第 1 页\n"
            f"💰 最高价配置已加载：{len(self.herb_max_prices)} 种药材"
        )

    async def cmd_auto_buy_herbs_stop(self, key: str) -> str:
        """关闭自动购买药材。"""
        job = self.jobs.get(key)
        if not job:
            return "自动购买药材：当前没有运行中的流程。"
        if job.mode != "herb_buy":
            return f"当前运行的是「{job.mode}」模式，不是自动购买药材。"
        self.jobs.pop(key, None)
        return f"🛑 自动购买药材已关闭。\n累计购买成功：{job.herb_buy_total_success} 次，失败：{job.herb_buy_total_fail} 次。"

    async def _send_herb_buy_page(self, job: AutoAlchemyJob, send_cb) -> None:
        """发送坊市查看药材页指令。"""
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} 坊市查看药材{job.current_page}")

    async def _send_next_herb_buy_purchase(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        """发送下一个药材购买指令。"""
        while job.herb_buy_buy_index < len(job.herb_buy_buy_queue):
            item = dict(job.herb_buy_buy_queue[job.herb_buy_buy_index] or {})
            name = self.normalize_name(item.get("name", ""))
            cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
            if name and cmd:
                job.phase = "HERB_BUY_WAIT"
                job.herb_buy_current_item = dict(item)
                job.herb_buy_current_item["buy_command"] = cmd
                job.last_command_ts = job.updated_at = time.time()
                await send_cb(f"@{self.official_qq} {cmd}")
                return
            job.herb_buy_buy_index += 1
        await self._finish_herb_buy_page(key, job, send_cb)

    async def _finish_herb_buy_page(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        """当前页购买完成，进入下一页或下一轮。"""
        job.phase = "HERB_BUY_SCANNING"
        job.herb_buy_buy_queue = []
        job.herb_buy_buy_index = 0
        job.herb_buy_current_item = {}
        if job.scan_index + 1 < len(job.scan_pages):
            job.scan_index += 1
            job.current_page = int(job.scan_pages[job.scan_index])
            job.retry_count = 0
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_herb_buy_page(job, send_cb)
            return
        if job.herb_buy_current_round < job.herb_buy_rounds:
            job.herb_buy_current_round += 1
            job.scan_index = 0
            job.current_page = int(job.scan_pages[0])
            job.retry_count = 0
            await send_cb(
                f"✅ 第 {job.herb_buy_current_round - 1} 轮购买完成。\n"
                f"📦 开始第 {job.herb_buy_current_round}/{job.herb_buy_rounds} 轮，查看坊市药材第 1 页。"
            )
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_herb_buy_page(job, send_cb)
            return
        self.jobs.pop(key, None)
        await send_cb(
            f"✅【自动购买药材完成】\n"
            f"总轮次：{job.herb_buy_rounds}\n"
            f"购买成功：{job.herb_buy_total_success} 次\n"
            f"购买失败：{job.herb_buy_total_fail} 次"
        )

    async def _handle_herb_buy_scanning(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        """处理坊市页回复，筛选符合最高价的药材并购买。"""
        if not self._looks_like_market_text(clean_text):
            return False
        self._merge_market_page(job, raw_text, int(job.current_page))
        page_items = self.parse_market_items(raw_text)
        buy_queue: List[Dict[str, Any]] = []
        for name, item in page_items.items():
            norm_name = self.normalize_name(name)
            price = float(item.get("price") or 0)
            max_price = self.herb_max_prices.get(norm_name, 0)
            if price <= 0 or max_price <= 0 or price > max_price:
                continue
            buy_cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
            if buy_cmd:
                buy_queue.append({
                    "name": norm_name,
                    "price": price,
                    "max_price": max_price,
                    "buy_command": buy_cmd,
                    "page": int(job.current_page),
                })
        buy_queue.sort(key=lambda x: (x["name"]))
        if buy_queue:
            names_preview = "、".join(x["name"] for x in buy_queue[:6])
            if len(buy_queue) > 6:
                names_preview += f"等{len(buy_queue)}种"
            await send_cb(
                f"🔍 第 {job.current_page} 页筛选出 {len(buy_queue)} 种符合最高价的药材：{names_preview}\n"
                f"💰 开始购买..."
            )
            job.herb_buy_buy_queue = buy_queue
            job.herb_buy_buy_index = 0
            await self._send_next_herb_buy_purchase(key, job, send_cb)
            return True
        await send_cb(f"🔍 第 {job.current_page} 页没有符合最高价的药材。")
        await self._finish_herb_buy_page(key, job, send_cb)
        return True

    async def _handle_herb_buy_result(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        """处理药材购买结果。"""
        if self._is_official_busy(clean_text):
            await send_cb(f"⏳ 小小繁忙，{int(self.busy_retry_delay_sec)}秒后重试当前购买...")
            await asyncio.sleep(self.busy_retry_delay_sec)
            if self.jobs.get(key) is not job or job.phase != "HERB_BUY_WAIT":
                return True
            item = dict(job.herb_buy_current_item or {})
            cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
            if cmd:
                job.last_command_ts = job.updated_at = time.time()
                await send_cb(f"@{self.official_qq} {cmd}")
            else:
                job.herb_buy_buy_index += 1
                await self._send_next_herb_buy_purchase(key, job, send_cb)
            return True
        if self._is_purchase_success(clean_text):
            item = dict(job.herb_buy_current_item or {})
            name = self.normalize_name(item.get("name", ""))
            job.herb_buy_bought.append(name)
            job.herb_buy_total_success += 1
            job.herb_buy_current_item = {}
            job.herb_buy_buy_index += 1
            job.updated_at = time.time()
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_next_herb_buy_purchase(key, job, send_cb)
            return True
        if self._is_purchase_recheck_fail(clean_text):
            item = dict(job.herb_buy_current_item or {})
            name = self.normalize_name(item.get("name", ""))
            job.herb_buy_failed.append(name)
            job.herb_buy_total_fail += 1
            job.herb_buy_current_item = {}
            job.herb_buy_buy_index += 1
            job.updated_at = time.time()
            await send_cb(f"⚠️ 购买失败：{name or '未知药材'}，已跳过。")
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_next_herb_buy_purchase(key, job, send_cb)
            return True
        return True



    async def _start_collect_job(
        self,
        key: str,
        send_cb,
        *,
        report_mode: str,
        mode: str,
        title: str,
        intro: str,
        scan_pages: Optional[List[int]] = None,
        target_pill: str = "",
        target_rounds: int = 1,
        initial_prices: Optional[Dict[str, float]] = None,
        initial_buy_commands: Optional[Dict[str, str]] = None,
        initial_pages_by_name: Optional[Dict[str, int]] = None,
        fast_start_note: str = "",
    ) -> str:
        if not self.enabled:
            return "🛑 自动炼丹模块已关闭。"
        if not self.recipe_path or not os.path.exists(self.recipe_path):
            return f"❌ 未找到丹方文件：{self.recipe_path}\n请把配方查询.txt 放到 data/alchemy_recipes.txt。"
        pages = sorted({int(p) for p in (scan_pages or list(range(1, self.max_page + 1))) if 1 <= int(p) <= self.max_page})
        if not pages:
            pages = list(range(1, self.max_page + 1))
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            old = self.jobs.get(key)
            if old and old.phase not in {"DONE", "STOPPED"}:
                return f"⚠️ 自动炼丹已有任务运行中（阶段：{old.phase}），请先等待完成、暂停或关闭。"
            job = AutoAlchemyJob(
                phase="COLLECTING",
                report_mode=report_mode,
                mode=mode,
                max_page=self.max_page,
                yield_count=self.default_yield_count,
                min_profit=self.min_profit_6pill,
                scan_pages=pages,
                scan_index=0,
                current_page=pages[0],
                target_pill=target_pill,
                target_rounds=max(1, int(target_rounds or 1)),
            )
            if initial_prices:
                job.prices.update({self.normalize_name(k): float(v) for k, v in initial_prices.items() if float(v or 0) > 0})
            if initial_buy_commands:
                job.buy_commands.update({self.normalize_name(k): self._normalize_buy_command(str(v or "")) for k, v in initial_buy_commands.items() if self._normalize_buy_command(str(v or ""))})
            if initial_pages_by_name:
                for k, v in initial_pages_by_name.items():
                    try:
                        page = int(v)
                    except Exception:
                        continue
                    if 1 <= page <= self.max_page:
                        job.pages_by_name[self.normalize_name(k)] = page
            job.fast_start_note = str(fast_start_note or "")
            self.jobs[key] = job
        await self._send_page(job, send_cb)
        if report_mode == "target_buy":
            extra = f"\n指定丹药：{target_pill} ×{target_rounds}炉。"
        else:
            extra = f"\n按成丹 {self.default_yield_count} 颗筛选不亏本丹方。"
        page_label = "、".join(str(p) for p in pages)
        return (
            f"{intro}\n"
            f"📦 获取坊市页：{page_label}"
            f"{job.fast_start_note}"
            f"{extra}"
        )

    async def cmd_status(self, key: str) -> str:
        job = self.jobs.get(key)
        if not job:
            return (
                "自动炼丹：当前没有运行中的流程。\n"
                f"当前批量配置：最多 {self.max_batch_formula_count} 条主丹方 × 每条 {self.max_formula_per_pill} 炉\n"
                f"利润阈值：{self.batch_mode_profit_threshold}万 | 背包抵扣：{'是' if self.use_backpack_for_batch_mode else '否'}\n"
                f"动态购买：{'已开启' if self.dynamic_herb_buy_during_scan else '已关闭'}"
            )
        if job.phase in {"COLLECTING", "COLLECTING_DYN_BUY_WAIT"}:
            mode_label = "指定丹药" if job.report_mode == "target_buy" else "批量自动炼丹"
            dyn_info = ""
            if self.dynamic_herb_buy_during_scan:
                dyn_info = f"\n动态购买：已开启（成功 {job.dynamic_buy_success} / 失败 {job.dynamic_buy_fail}）"
            if job.phase == "COLLECTING_DYN_BUY_WAIT":
                dyn_item = (job.dynamic_buy_current_item or {}).get("name", "未知")
                dyn_info += f"\n动态购买中：{dyn_item}"
            return (
                f"自动炼丹：{mode_label}采集中\n"
                f"批量配置：最多 {self.max_batch_formula_count} 条主丹方 × 每条 {self.max_formula_per_pill} 炉\n"
                f"利润阈值：{self.batch_mode_profit_threshold}万 | 背包抵扣：{'是' if self.use_backpack_for_batch_mode else '否'}\n"
                f"当前页：{job.current_page}\n"
                f"待采集页：{'、'.join(map(str, job.scan_pages))}\n"
                f"已采集药材价格：{len(job.prices)} 条\n"
                f"已解析购买指令：{len(job.buy_commands)} 条\n"
                f"已完成页：{', '.join(map(str, job.pages_seen)) if job.pages_seen else '无'}{dyn_info}"
            )
        if job.phase in {"BATCH_BUY_WAIT", "BATCH_BUY_SENT", "BATCH_BUY_REFRESHING", "BUYING"}:
            item = job.batch_current_item or {}
            name = item.get("name") or "未知药材"
            return (
                "自动炼丹：逐个购买药材中\n"
                f"模式：{'指定丹药 ' + job.target_pill if job.mode == 'target' else '批量模式'}\n"
                f"批量配置：最多 {self.max_batch_formula_count} 条主丹方 × 每条 {self.max_formula_per_pill} 炉\n"
                f"默认成丹数：{job.yield_count}\n"
                "筛选规则：不亏本即可购买炼制\n"
                f"当前药材：{name}\n"
                f"购买进度：{job.batch_success_count}/{job.batch_buy_expected}\n"
                f"失败跳过：{sum(job.failed_counts.values())}"
            )
        if job.phase == "ALCHEMY_WAIT":
            current = job.alchemy_queue[job.alchemy_index] if 0 <= job.alchemy_index < len(job.alchemy_queue) else {}
            return (
                "自动炼丹：正在逐个炼丹\n"
                f"当前丹药：{current.get('pill', '未知')}\n"
                f"已发送炼丹指令：{job.alchemy_sent}\n"
                f"炼丹成功：{job.alchemy_success}/{len(job.alchemy_queue)}"
            )
        if job.phase == "PAUSED":
            return f"自动炼丹：已暂停\n原因：{job.paused_reason or '等待人工处理'}\n暂停前阶段：{job.phase_before_pause or '未知'}"
        return f"自动炼丹：运行中，阶段 {job.phase}"



    async def _send_page(self, job: AutoAlchemyJob, send_cb) -> None:
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} 坊市查看药材{job.current_page}")

    async def on_official_text(self, key: str, text: str, send_cb) -> bool:
        job = self.jobs.get(key)
        if not job:
            return False
        raw_text = str(text or "")
        clean_text = self._cleanup_text(raw_text)
        if not clean_text:
            return False
        if self._is_daily_limit_stop(clean_text):
            self.jobs.pop(key, None)
            await send_cb("🛑 检测到小小提示：道友今天已经很努力了。\n本次自动炼丹流程已停止，购买队列与炼丹队列已清空。")
            return True
        if job.phase == "PAUSED":
            return True
        if job.phase == "BAG_COLLECTING":
            return await self._handle_backpack_collecting(key, job, raw_text, clean_text, send_cb)
        if job.phase == "COLLECTING":
            return await self._handle_collecting_page(key, job, raw_text, clean_text, send_cb)
        if job.phase in {"BATCH_BUY_WAIT", "BATCH_BUY_SENT"}:
            return await self._handle_batch_buy_result(key, job, raw_text, clean_text, send_cb)
        if job.phase == "BATCH_BUY_REFRESHING":
            return await self._handle_batch_refresh_page(key, job, raw_text, clean_text, send_cb)
        if job.phase == "BATCH_RETRY_REFRESHING":
            return await self._handle_retry_refresh_page(key, job, raw_text, clean_text, send_cb)
        if job.phase == "BATCH_ROUND_REFRESHING":
            return await self._handle_round_refresh_page(key, job, raw_text, clean_text, send_cb)
        if job.phase == "ALCHEMY_WAIT":
            return await self._handle_alchemy_result(key, job, raw_text, clean_text, send_cb)
        if job.phase == "HERB_BUY_SCANNING":
            return await self._handle_herb_buy_scanning(key, job, raw_text, clean_text, send_cb)
        if job.phase == "HERB_BUY_WAIT":
            return await self._handle_herb_buy_result(key, job, raw_text, clean_text, send_cb)
        if job.phase == "COLLECTING_DYN_BUY_WAIT":
            return await self._handle_collecting_dyn_buy_result(key, job, raw_text, clean_text, send_cb)
        return False

    async def _send_backpack_page(self, job: AutoAlchemyJob, send_cb) -> None:
        job.last_command_ts = job.updated_at = time.time()
        suffix = "" if int(job.current_page or 1) <= 1 else str(int(job.current_page))
        await send_cb(f"@{self.official_qq} 药材背包{suffix}")

    async def _handle_backpack_collecting(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if self.inventory_parser is None:
            self.jobs.pop(key, None)
            await send_cb("❌ 自动背包炼丹失败：背包解析器不可用。")
            return True
        inv_text = self.inventory_parser._cleanup_text(raw_text)
        if not ("拥有数量" in inv_text or "数量" in inv_text or "名字" in inv_text or "☆" in inv_text or ("第" in inv_text and "共" in inv_text and "页" in inv_text)):
            return False
        cur, total = self.inventory_parser._parse_page_info(inv_text, int(job.current_page or 1))
        job.current_page = max(1, int(cur or job.current_page or 1))
        job.backpack_total_pages = min(max(1, int(total or 1)), 30)
        try:
            items = self.inventory_parser._parse_items(inv_text, CATEGORY_HERB)
        except Exception as e:
            self.jobs.pop(key, None)
            await send_cb(f"❌ 自动背包炼丹失败：解析药材背包异常：{e}")
            return True
        for item in items:
            name = self.normalize_name(getattr(item, "name", ""))
            count = int(getattr(item, "count", 0) or 0)
            if name and count > 0:
                # 背包分页同名药材以最大数量为准，避免重复回显造成数量翻倍。
                job.backpack_counts[name] = max(int(job.backpack_counts.get(name, 0)), count)
        if job.current_page not in job.backpack_pages_seen:
            job.backpack_pages_seen.append(job.current_page)
        job.updated_at = time.time()
        if job.current_page < job.backpack_total_pages:
            job.current_page += 1
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_backpack_page(job, send_cb)
            return True
        if not job.prices:
            if job.mode == "backpack":
                self.jobs.pop(key, None)
                await send_cb(
                    f"📦 药材背包读取完成：共识别 {len(job.backpack_counts)} 种药材。\n"
                    "❌ 当前没有可用坊市价格快照，无法计算利润。\n"
                    "请先发送“开启自动炼丹”拉取坊市价格，然后再使用背包炼丹。"
                )
                return True
            job.phase = "COLLECTING"
            if job.mode == "backpack":
                job.report_mode = "backpack_buy"
            job.scan_pages = list(range(1, self.max_page + 1))
            job.scan_index = 0
            job.current_page = job.scan_pages[0]
            job.retry_count = 0
            if job.mode == "backpack":
                note = "当前没有可用坊市价格快照，正在拉取坊市药材1-8页用于计算药材实际成本和缺口采购成本。"
            else:
                note = "当前没有可用坊市价格快照，正在拉取坊市药材1-8页；采购时会自动抵扣背包已有药材。"
            await send_cb(f"📦 药材背包读取完成：共识别 {len(job.backpack_counts)} 种药材。\n{note}")
            await self._send_page(job, send_cb)
            return True
        if job.mode == "batch":
            # 批量模式：背包读取完成后直接进入丹方匹配（坊市扫描已完成）
            await self._finish_collecting_and_start_batch_buy(key, job, send_cb)
            return True
        if job.mode != "backpack":
            job.phase = "COLLECTING"
            if not job.scan_pages:
                job.scan_pages = list(range(1, self.max_page + 1))
            job.scan_index = 0
            job.current_page = int(job.scan_pages[0])
            job.retry_count = 0
            await send_cb(
                f"📦 药材背包读取完成：共识别 {len(job.backpack_counts)} 种药材。\n"
                "正在刷新本轮预计用到的坊市页；采购时会自动抵扣背包已有药材。"
            )
            await self._send_page(job, send_cb)
            return True
        await self._finish_backpack_collecting_and_start_buy(key, job, send_cb)
        return True

    async def _finish_backpack_collecting_and_start_buy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        try:
            selected, candidate_count, skipped_count = self._select_backpack_best_candidates(job)
        except Exception as e:
            self.jobs.pop(key, None)
            await send_cb(f"❌ 自动背包炼丹失败：计算丹方异常：{e}")
            return
        job.batch_selected = selected
        if not selected:
            self.jobs.pop(key, None)
            await send_cb(
                f"❌【自动背包炼丹】没有找到可执行丹方。\n"
                f"背包药材种类：{len(job.backpack_counts)}\n"
                f"可计算候选数：{candidate_count}\n"
                f"跳过配方数：{skipped_count}\n"
                f"筛选规则：只使用背包已有药材，成丹 {job.yield_count} 颗利润 > 10万，最多取 {self.backpack_max_formula_count} 个丹方。\n"
                f"提示：无可匹配盈利丹方则停止。"
            )
            return
        await self._start_backpack_alchemy_directly(key, job, send_cb, candidate_count, skipped_count)

    async def _start_backpack_alchemy_directly(self, key: str, job: AutoAlchemyJob, send_cb, candidate_count: int, skipped_count: int) -> None:
        """背包炼丹直接进入炼丹阶段，不做坊市购买。"""
        job.batch_formula_texts = [self._format_recipe_send_command(c["recipe"], c["materials"]) for c in job.batch_selected if not c.get("abandoned")]
        total_profit = sum(float(c.get("score_profit", 0)) for c in job.batch_selected if not c.get("abandoned"))
        report_lines = [
            f"💰【背包炼丹利润丹方】",
            f"成丹：{job.yield_count}颗｜丹方：{len([c for c in job.batch_selected if not c.get('abandoned')])}条｜预计利润：{self._fmt_num(total_profit)}万",
        ]
        for idx, cand in enumerate(job.batch_selected, 1):
            if cand.get("abandoned"):
                continue
            r: Recipe = cand["recipe"]
            report_lines.append(f"{idx}. {r.pill}｜利润 {self._fmt_num(cand.get('score_profit', 0))}万｜成本 {self._fmt_num(cand.get('cost', 0))}万")
            report_lines.append(f"   {self._format_recipe_send_command(r, cand.get('materials', []))}")
        report = "\n".join(report_lines)
        await send_cb(report + "\n\n🧪 只使用背包药材，开始炼丹。")
        await self._start_alchemy_sequence(key, job, send_cb)

    def _select_backpack_best_candidates(self, job: AutoAlchemyJob) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        背包模式：优先用背包已有药材匹配丹方，盈利 > 10万即可。

        规则：
        - 背包已有药材只抵扣采购数量；利润成本仍按所有药材的实时坊市价格计算。
        - 成丹 6 颗利润 > 10万即可纳入。
        - 默认要求丹方至少使用 1 个背包已有药材，避免退化成普通坊市采购模式。
        - 允许同一条丹方在背包材料仍可继续抵扣时被多次选择。
        """
        recipes = [r for r in self._load_recipes() if r.pill in FIXED_PILL_SALE_PRICE]
        selected: List[Dict[str, Any]] = []
        available = {self.normalize_name(k): int(v or 0) for k, v in (job.backpack_counts or {}).items()}
        total_candidate_seen = 0
        skipped_total = 0
        threshold = 10.0

        for _ in range(self.backpack_max_formula_count):
            round_candidates: List[Tuple[Tuple[float, float, float, str], Dict[str, Any]]] = []
            round_skipped = 0
            for recipe in recipes:
                resolved = self._resolve_recipe_with_backpack(recipe, available, job.prices, job.buy_commands, job.pages_by_name)
                if not resolved or resolved.get("wildcard_missing"):
                    round_skipped += 1
                    continue
                materials = resolved.get("materials", [])
                backpack_used = sum(int(m.get("backpack_used") or 0) for m in materials)
                if self.backpack_require_existing_material and backpack_used <= 0:
                    continue
                sale = float(FIXED_PILL_SALE_PRICE.get(recipe.pill, 0))
                cost = float(resolved.get("recipe_cost", resolved.get("missing_cost", 0)) or 0)
                purchase_cost = float(resolved.get("missing_cost", 0) or 0)
                profit = sale * int(job.yield_count or self.default_yield_count) - cost
                if profit < threshold:
                    continue
                cand = {
                    "recipe": recipe,
                    "materials": materials,
                    "cost": cost,
                    "sale": sale,
                    "profits": [{"count": n, "revenue": sale * n, "profit": sale * n - cost} for n in range(1, 8)],
                    "profit7": sale * 7 - cost,
                    "yield_count": int(job.yield_count or self.default_yield_count),
                    "score_profit": profit,
                    "unknown_sale": False,
                    "abandoned": False,
                    "backpack_used_total": backpack_used,
                    "purchase_cost": purchase_cost,
                }
                self._attach_command_efficiency(cand, use_purchase_qty=True)
                sort_key = (float(backpack_used), -float(purchase_cost), float(profit), recipe.pill)
                round_candidates.append((sort_key, cand))
            total_candidate_seen += len(round_candidates)
            skipped_total += round_skipped
            if not round_candidates:
                break
            round_candidates.sort(key=lambda pair: pair[0], reverse=True)
            best = round_candidates[0][1]
            selected.append(best)
            for m in best.get("materials", []):
                n = self.normalize_name(m.get("name", ""))
                use_bag = int(m.get("backpack_used") or 0)
                if n and use_bag > 0:
                    available[n] = max(0, int(available.get(n, 0) or 0) - use_bag)
        return selected, total_candidate_seen, skipped_total

    def _resolve_recipe_with_backpack(self, recipe: Recipe, backpack_counts: Dict[str, int], prices: Dict[str, float], buy_commands: Dict[str, str], pages_by_name: Dict[str, int]) -> Optional[Dict[str, Any]]:
        used: Dict[str, int] = {}
        materials: List[Dict[str, Any]] = []
        recipe_cost = 0.0
        missing_cost = 0.0

        def available_left(n: str) -> int:
            n = self.normalize_name(n)
            return max(0, int(backpack_counts.get(n, 0) or 0) - int(used.get(n, 0) or 0))

        def add_material(req: MaterialReq, name: str, source: str = "固定药材", prop_value: str = "") -> bool:
            nonlocal recipe_cost, missing_cost
            name = self.normalize_name(name)
            qty = int(req.qty or 1)
            have = available_left(name)
            use_bag = min(have, qty) if self.backpack_use_existing_as_free else 0
            miss = max(0, qty - use_bag)
            unit_price = float(prices.get(name, 0) or 0)
            if unit_price <= 0:
                return False
            used[name] = int(used.get(name, 0) or 0) + use_bag
            recipe_cost += qty * unit_price
            missing_cost += miss * unit_price
            role_key = "main" if req.role == "主药" else "assist" if req.role == "辅药" else "guide"
            prop = prop_value or self.herb_props.get(name, {}).get(role_key, "")
            materials.append({
                "role": req.role,
                "name": name,
                "qty": qty,
                "unit_price": unit_price,
                "source": source,
                "property": prop,
                "buy_command": buy_commands.get(name, ""),
                "page": int(pages_by_name.get(name, 0) or 0),
                "backpack_used": use_bag,
                "purchase_qty": miss,
            })
            return True

        for req in recipe.materials:
            if req.wildcard_prop:
                choices = []
                for name, prop in self.herb_props.items():
                    guide_prop = str(prop.get("guide", ""))
                    if not guide_prop.startswith(req.wildcard_prop):
                        continue
                    qty = int(req.qty or 1)
                    use_bag = min(available_left(name), qty) if self.backpack_use_existing_as_free else 0
                    miss = max(0, qty - use_bag)
                    unit_price = float(prices.get(name, 0) or 0)
                    if unit_price <= 0:
                        continue
                    choices.append((-use_bag, qty * unit_price, miss * unit_price, unit_price, name, guide_prop))
                if not choices:
                    return {"wildcard_missing": True, "materials": []}
                choices.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
                _, _, _, _, picked_name, prop_value = choices[0]
                if not add_material(req, picked_name, source=f"任意{req.wildcard_prop}", prop_value=prop_value):
                    return {"wildcard_missing": True, "materials": []}
            else:
                if not add_material(req, req.name):
                    return None
        return {"materials": materials, "recipe_cost": recipe_cost, "missing_cost": missing_cost}

    def _select_batch_with_backpack(self, job: AutoAlchemyJob, *, threshold: float) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        批量模式 + 背包抵扣：筛选利润 > threshold 万的所有丹方。

        规则：
        - 背包已有药材优先抵扣采购需求，只购买缺口部分。
        - 利润按药材实时坊市总成本计算（不扣减背包免费部分）。
        - 同一条丹方可被多次选择，直到背包材料耗尽或利润不达标。
        """
        recipes = [r for r in self._load_recipes() if r.pill in FIXED_PILL_SALE_PRICE]
        available = {self.normalize_name(k): int(v or 0) for k, v in (job.backpack_counts or {}).items()}
        selected: List[Dict[str, Any]] = []
        total_candidate_seen = 0
        skipped_total = 0

        for _ in range(self.max_batch_formula_count):
            round_candidates: List[Tuple[Tuple[float, float, float, str], Dict[str, Any]]] = []
            round_skipped = 0
            for recipe in recipes:
                resolved = self._resolve_recipe_with_backpack(
                    recipe, available, job.prices, job.buy_commands, job.pages_by_name,
                )
                if not resolved or resolved.get("wildcard_missing"):
                    round_skipped += 1
                    continue
                materials = resolved.get("materials", [])
                total_candidate_seen += 1
                backpack_used = sum(int(m.get("backpack_used") or 0) for m in materials)
                sale = float(FIXED_PILL_SALE_PRICE.get(recipe.pill, 0))
                cost = float(resolved.get("recipe_cost", resolved.get("missing_cost", 0)) or 0)
                purchase_cost = float(resolved.get("missing_cost", 0) or 0)
                profit = sale * int(job.yield_count or self.default_yield_count) - cost
                if profit < threshold:
                    continue
                cand = {
                    "recipe": recipe,
                    "materials": materials,
                    "cost": cost,
                    "sale": sale,
                    "profits": [{"count": n, "revenue": sale * n, "profit": sale * n - cost} for n in range(1, 8)],
                    "profit7": sale * 7 - cost,
                    "yield_count": int(job.yield_count or self.default_yield_count),
                    "score_profit": profit,
                    "unknown_sale": False,
                    "abandoned": False,
                    "backpack_used_total": backpack_used,
                    "purchase_cost": purchase_cost,
                }
                self._attach_command_efficiency(cand, use_purchase_qty=True)
                sort_key = (float(backpack_used), -float(purchase_cost), float(profit), recipe.pill)
                round_candidates.append((sort_key, cand))
            skipped_total += round_skipped
            if not round_candidates:
                break
            round_candidates.sort(key=lambda pair: pair[0], reverse=True)
            best = round_candidates[0][1]
            selected.append(best)
            for m in best.get("materials", []):
                n = self.normalize_name(m.get("name", ""))
                use_bag = int(m.get("backpack_used") or 0)
                if n and use_bag > 0:
                    available[n] = max(0, int(available.get(n, 0) or 0) - use_bag)
        return selected, total_candidate_seen, skipped_total

    def _build_batch_purchase_plan_with_backpack(self, selected: List[Dict[str, Any]], backpack_counts: Dict[str, int]) -> List[Dict[str, Any]]:
        aggregate: Dict[str, Dict[str, Any]] = {}
        available = {self.normalize_name(k): int(v or 0) for k, v in (backpack_counts or {}).items()}
        for cand in selected:
            if cand.get("abandoned") or cand.get("purchase_frozen"):
                continue
            recipe = cand.get("recipe")
            for item in cand.get("materials", []):
                name = self.normalize_name(item.get("name", ""))
                if not name:
                    continue
                qty = int(item.get("qty") or 1)
                use_bag = min(int(available.get(name, 0) or 0), qty) if self.backpack_use_existing_as_free else 0
                available[name] = int(available.get(name, 0) or 0) - use_bag
                miss = max(0, qty - use_bag)
                if miss <= 0:
                    continue
                if name not in aggregate:
                    aggregate[name] = {"name": name, "qty": 0, "unit_price": float(item.get("unit_price") or 0), "page": int(item.get("page") or 0), "buy_command": item.get("buy_command", ""), "roles": set(), "pills": set()}
                aggregate[name]["qty"] += miss
                aggregate[name]["roles"].add(str(item.get("role") or ""))
                if recipe is not None:
                    aggregate[name]["pills"].add(recipe.pill)
                if item.get("buy_command"):
                    aggregate[name]["buy_command"] = item.get("buy_command")
                if item.get("page"):
                    aggregate[name]["page"] = int(item.get("page") or 0)
                if item.get("unit_price"):
                    aggregate[name]["unit_price"] = float(item.get("unit_price") or 0)
        plan = list(aggregate.values())
        for item in plan:
            item["roles"] = sorted(x for x in item.get("roles", set()) if x)
            item["pills"] = sorted(x for x in item.get("pills", set()) if x)
        plan.sort(key=lambda x: (int(x.get("page") or 999), x["name"]))
        return plan

    async def _handle_collecting_page(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if not self._looks_like_market_text(clean_text):
            return False
        self._merge_market_page(job, raw_text, int(job.current_page))
        if self.dynamic_herb_buy_during_scan and self.herb_max_prices:
            page_items = self.parse_market_items(raw_text)
            dyn_queue = self._collect_dynamic_buy_items(page_items, int(job.current_page))
            if dyn_queue:
                job.dynamic_buy_queue = dyn_queue
                job.dynamic_buy_index = 0
                job.dynamic_buy_current_item = {}
                await send_cb(f"🛒 坊市第 {job.current_page} 页发现 {len(dyn_queue)} 种符合最高价的药材，开始动态购买。")
                if self.send_interval_sec > 0:
                    await asyncio.sleep(self.send_interval_sec)
                await self._send_next_dynamic_buy(key, job, send_cb)
                return True
        await self._advance_collecting_or_finish(key, job, send_cb)
        return True

    async def _advance_collecting_or_finish(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        """采集阶段：进入下一页或完成采集后转入背包读取。"""
        if job.scan_index + 1 < len(job.scan_pages):
            job.scan_index += 1
            job.current_page = int(job.scan_pages[job.scan_index])
            job.retry_count = 0
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_page(job, send_cb)
            return
        if job.report_mode == "backpack_buy":
            await self._finish_backpack_collecting_and_start_buy(key, job, send_cb)
            return
        if job.report_mode == "target_buy":
            await self._finish_collecting_and_start_target_buy(key, job, send_cb)
            return
        await send_cb(
            f"📊 坊市价格采集完成：已获取 {len(job.prices)} 种药材价格。\n"
            "📦 正在读取药材背包用于智能抵扣。"
        )
        job.phase = "BAG_COLLECTING"
        job.current_page = 1
        job.backpack_pages_seen = []
        job.backpack_counts = {}
        job.backpack_total_pages = 1
        await self._send_backpack_page(job, send_cb)

    def _collect_dynamic_buy_items(self, page_items: Dict[str, Dict[str, Any]], page: int) -> List[Dict[str, Any]]:
        """从当前坊市页中筛选符合最高价的药材，返回购买队列。"""
        queue: List[Dict[str, Any]] = []
        for name, item in page_items.items():
            max_price = self.herb_max_prices.get(name)
            if max_price is None:
                continue
            price = float(item.get("price") or 0)
            buy_cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
            if price <= 0 or price > max_price or not buy_cmd:
                continue
            queue.append({"name": name, "unit_price": price, "buy_command": buy_cmd, "page": page, "qty": 1})
        queue.sort(key=lambda x: x.get("name", ""))
        return queue

    async def _send_next_dynamic_buy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        """发送下一个动态购买指令。"""
        while job.dynamic_buy_index < len(job.dynamic_buy_queue):
            item = dict(job.dynamic_buy_queue[job.dynamic_buy_index] or {})
            name = self.normalize_name(item.get("name", ""))
            buy_cmd = item.get("buy_command", "")
            if not name or not buy_cmd:
                job.dynamic_buy_index += 1
                continue
            job.phase = "COLLECTING_DYN_BUY_WAIT"
            job.dynamic_buy_current_item = item
            job.last_command_ts = job.updated_at = time.time()
            await send_cb(buy_cmd)
            return
        await self._advance_collecting_or_finish(key, job, send_cb)

    async def _handle_collecting_dyn_buy_result(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        """处理动态购买结果（成功/失败/繁忙），然后继续下一个动态购买或采集。"""
        item = dict(job.dynamic_buy_current_item or {})
        name = self.normalize_name(item.get("name", ""))
        if self._is_official_busy(clean_text):
            if job.dynamic_busy_retry_done:
                await send_cb(f"⏳ {name} 繁忙重试后仍繁忙，已跳过。")
                job.dynamic_buy_fail += 1
                job.dynamic_buy_current_item = {}
                job.dynamic_buy_index += 1
                job.dynamic_busy_retry_done = False
                job.updated_at = time.time()
                if self.send_interval_sec > 0:
                    await asyncio.sleep(self.send_interval_sec)
                await self._send_next_dynamic_buy(key, job, send_cb)
                return True
            job.dynamic_busy_retry_done = True
            await send_cb(f"⏳ 小小繁忙，3秒后重试：{name}")
            await asyncio.sleep(3.0)
            buy_cmd = item.get("buy_command", "")
            if buy_cmd:
                job.last_command_ts = job.updated_at = time.time()
                await send_cb(buy_cmd)
                return True
            job.dynamic_buy_fail += 1
            job.dynamic_buy_current_item = {}
            job.dynamic_buy_index += 1
            job.dynamic_busy_retry_done = False
            job.updated_at = time.time()
            await self._send_next_dynamic_buy(key, job, send_cb)
            return True
        if self._is_purchase_success(clean_text):
            job.dynamic_buy_success += 1
            job.dynamic_purchased[name] = int(job.dynamic_purchased.get(name, 0)) + 1
            job.dynamic_buy_current_item = {}
            job.dynamic_buy_index += 1
            job.dynamic_busy_retry_done = False
            job.updated_at = time.time()
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_next_dynamic_buy(key, job, send_cb)
            return True
        # 购买失败或其他：跳过当前药材
        job.dynamic_buy_fail += 1
        job.dynamic_buy_current_item = {}
        job.dynamic_busy_retry_done = False
        job.dynamic_buy_index += 1
        job.updated_at = time.time()
        if self.send_interval_sec > 0:
            await asyncio.sleep(self.send_interval_sec)
        await self._send_next_dynamic_buy(key, job, send_cb)
        return True

    async def cmd_toggle_dynamic_buy(self, enable: bool) -> str:
        """开启/关闭坊市扫描动态购买功能。"""
        self.dynamic_herb_buy_during_scan = bool(enable)
        if not self.herb_max_prices:
            return (
                f"ℹ️ 动态购买已{'开启' if enable else '关闭'}。\n"
                "⚠️ 当前未加载 herb_max_prices.yaml，动态购买不会生效。\n"
                "请确保 data/herb_max_prices.yaml 文件存在。"
            )
        return (
            f"✅ 动态购买已{'开启' if enable else '关闭'}。\n"
            f"当前已配置 {len(self.herb_max_prices)} 种药材最高价。\n"
            "下次开启自动炼丹时，遍历坊市将" + ("自动购买符合最高价的药材。" if enable else "不会动态购买药材。")
        )

    def _merge_market_page(self, job: AutoAlchemyJob, raw_text: str, page: int) -> None:
        page_items = self.parse_market_items(raw_text)
        for name, item in page_items.items():
            price = float(item.get("price") or 0)
            if price > 0:
                job.prices[name] = price
                job.pages_by_name[name] = int(page)
                buy_command = self._normalize_buy_command(str(item.get("buy_command") or ""))
                if buy_command:
                    job.buy_commands[name] = buy_command
        if page not in job.pages_seen:
            job.pages_seen.append(page)
        job.page_counts[int(page)] = len(page_items)
        job.retry_count = 0
        job.updated_at = time.time()
        self._write_page_index(job.pages_by_name)



    async def _handle_batch_buy_result(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if self._is_official_busy(clean_text):
            if job.batch_busy_retry_done:
                # 已重试过仍然繁忙 → 按失败处理，加入重试队列
                item = dict(job.batch_current_item or {})
                name = self.normalize_name(item.get("name", ""))
                job.batch_busy_retry_done = False
                if self.retry_failed_after_batch and not job.retry_after_batch_active:
                    await self._defer_current_purchase_for_retry(
                        key, job, send_cb,
                        f"小小繁忙重试后仍繁忙，已暂时跳过{name or ''}，将在本轮购买完成后统一刷新重试"
                    )
                else:
                    await self._skip_current_purchase_and_continue(
                        key, job, send_cb,
                        f"小小繁忙重试后仍繁忙，已最终跳过{name or ''}"
                    )
            else:
                await self._retry_current_purchase_after_busy(key, job, send_cb)
            return True
        if self._is_purchase_success(clean_text):
            item = dict(job.batch_current_item or {})
            name = self.normalize_name(item.get("name", ""))
            if name:
                job.purchased_counts[name] = int(job.purchased_counts.get(name, 0)) + 1
            job.batch_success_count += 1
            job.batch_buy_results.append(self._short_preview(clean_text, 220))
            job.batch_current_item = {}
            job.batch_buy_index += 1
            job.batch_busy_retry_done = False
            job.updated_at = time.time()
            await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)
            return True
        if self._is_purchase_recheck_fail(clean_text):
            item = dict(job.batch_current_item or {})
            name = self.normalize_name(item.get("name", ""))
            job.batch_busy_retry_done = False
            if self.retry_failed_after_batch and not job.retry_after_batch_active:
                await self._defer_current_purchase_for_retry(
                    key,
                    job,
                    send_cb,
                    f"小小提示购买失败/购买指令可能变动，已暂时跳过{name or ''}，将在本轮购买完成后统一刷新所在页并重试"
                )
            else:
                await self._skip_current_purchase_and_continue(
                    key,
                    job,
                    send_cb,
                    f"小小提示购买失败/需重新查看坊市，重试阶段仍失败，已最终标记缺材料{name or ''}"
                )
            return True
        return True

    async def _handle_batch_refresh_page(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if not self._looks_like_market_text(clean_text):
            return True
        page_items = self.parse_market_items(raw_text)
        target = self.normalize_name(job.refresh_item_name)
        item = page_items.get(target)
        if not item:
            await self._skip_current_purchase_and_continue(key, job, send_cb, f"重新查看坊市第 {job.refresh_item_page} 页后没有找到 {target}")
            return True
        new_price = float(item.get("price") or 0)
        new_cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
        if new_price <= 0 or not new_cmd:
            await self._skip_current_purchase_and_continue(key, job, send_cb, f"重新查看 {target} 后仍未解析到有效价格或购买指令")
            return True
        old_price = float(job.refresh_old_price or job.prices.get(target, 0) or 0)
        price_changed = abs(new_price - old_price) > 1e-9
        job.prices[target] = new_price
        job.buy_commands[target] = new_cmd
        job.pages_by_name[target] = int(job.refresh_item_page or job.current_page or 0)
        self._write_page_index(job.pages_by_name)
        for q in job.batch_buy_queue:
            if self.normalize_name(q.get("name", "")) == target:
                q["unit_price"] = new_price
                q["buy_command"] = new_cmd
                q["page"] = int(job.pages_by_name.get(target, 0) or 0)
        if job.batch_current_item and self.normalize_name(job.batch_current_item.get("name", "")) == target:
            job.batch_current_item["unit_price"] = new_price
            job.batch_current_item["buy_command"] = new_cmd
            job.batch_current_item["page"] = int(job.pages_by_name.get(target, 0) or 0)
        if price_changed:
            keep_running, note = self._handle_price_change_for_job(job, target, old_price, new_price)
            if note:
                await send_cb(note)
            await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)
            return True
        else:
            await send_cb(f"✅ {target} 价格未变化，已获取新的购买指令，继续购买。")
        await self._send_fresh_purchase_command(job, send_cb, job.batch_current_item or item)
        return True

    async def _handle_alchemy_result(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if not self._is_alchemy_success(clean_text):
            return True
        current = job.alchemy_queue[job.alchemy_index] if 0 <= job.alchemy_index < len(job.alchemy_queue) else {}
        job.alchemy_success += 1
        job.alchemy_results.append(self._short_preview(clean_text, 220))
        job.alchemy_index += 1
        job.updated_at = time.time()
        if job.alchemy_index >= len(job.alchemy_queue):
            self.jobs.pop(key, None)
            await send_cb(self._format_full_done_report(job))
            return True
        if self.alchemy_send_interval_sec > 0:
            await asyncio.sleep(self.alchemy_send_interval_sec)
        await self._send_next_alchemy_command(key, job, send_cb)
        return True

    def _normalize_buy_command(self, command: str) -> str:
        cmd = unquote(str(command or "")).strip()
        if not cmd.startswith("坊市购买"):
            return ""
        while cmd.startswith("坊市购买坊市购买"):
            cmd = "坊市购买" + cmd[len("坊市购买坊市购买"):]
        return cmd

    def _normalize_keyword_text(self, text: str) -> str:
        t = self.ZERO_WIDTH_RE.sub("", str(text or ""))
        t = re.sub(r"\s+", "", t)
        return t

    def _is_purchase_success(self, text: str) -> bool:
        return "道友成功购买" in self._normalize_keyword_text(text)

    def _is_purchase_recheck_fail(self, text: str) -> bool:
        t = self._normalize_keyword_text(text)
        return (
            "道友请重新查看坊市" in t
            or "未查询到该物品" in t
            or "可能输入错误" in t
            or "已被购买" in t
        )

    def _is_official_busy(self, text: str) -> bool:
        t = self._normalize_keyword_text(text)
        return (
            "方式现在太繁忙" in t
            or "坊市现在太繁忙" in t
            or "现在太繁忙" in t
            or "太繁忙了" in t
        )

    def _is_daily_limit_stop(self, text: str) -> bool:
        return "道友今天已经很努力了" in self._normalize_keyword_text(text)

    def _is_alchemy_success(self, text: str) -> bool:
        return "恭喜道友成功炼成丹药" in self._normalize_keyword_text(text)

    @staticmethod
    def _short_preview(text: str, limit: int = 220) -> str:
        preview = re.sub(r"\n{3,}", "\n\n", str(text or "")).strip()
        return preview[:limit] + "..." if len(preview) > limit else preview

    async def tick(self, key: str, send_cb) -> None:
        job = self.jobs.get(key)
        if not job or not job.last_command_ts or job.phase == "PAUSED":
            return
        now = time.time()
        if job.phase in {"COLLECTING", "BAG_COLLECTING", "BATCH_BUY_REFRESHING", "BATCH_RETRY_REFRESHING", "BATCH_ROUND_REFRESHING"}:
            if now - job.last_command_ts <= self.page_timeout_sec:
                return
            if job.retry_count < self.max_page_retries:
                job.retry_count += 1
                await send_cb(f"⚠️ 自动炼丹拉取第 {job.current_page} 页超时，正在重试 {job.retry_count}/{self.max_page_retries}。")
                if job.phase == "BAG_COLLECTING":
                    await self._send_backpack_page(job, send_cb)
                else:
                    await self._send_page(job, send_cb)
                return
            if job.phase == "BATCH_BUY_REFRESHING":
                await self._skip_current_purchase_and_continue(key, job, send_cb, f"重新查看第 {job.current_page} 页超时")
                return
            if job.phase == "BATCH_RETRY_REFRESHING":
                await self._start_retry_queue_after_refresh(key, job, send_cb, reason=f"刷新重试页第 {job.current_page} 页超时，无法重试部分药材")
                return
            self.jobs.pop(key, None)
            await send_cb(f"❌ 自动炼丹实时价格采集失败：第 {job.current_page} 页超时。为避免使用不完整数据，本次流程已终止。")
            return
        if job.phase == "COLLECTING_DYN_BUY_WAIT":
            if now - job.last_command_ts <= self.purchase_response_timeout_sec:
                return
            item = dict(job.dynamic_buy_current_item or {})
            name = self.normalize_name(item.get("name", "")) or "未知药材"
            await send_cb(
                f"⚠️ 动态购买 {name} 后 {int(self.purchase_response_timeout_sec)} 秒内未收到回执；"
                f"已自动跳过当前动态购买并继续采集。"
            )
            job.dynamic_buy_fail += 1
            job.dynamic_buy_current_item = {}
            job.dynamic_buy_index += 1
            job.updated_at = time.time()
            await self._send_next_dynamic_buy(key, job, send_cb)
            return
        if job.phase in {"BATCH_BUY_WAIT", "BATCH_BUY_SENT"}:
            if now - job.last_command_ts <= self.purchase_response_timeout_sec:
                return
            item = dict(job.batch_current_item or {})
            name = self.normalize_name(item.get("name", "")) or "未知药材"
            reason = f"购买 {name} 后 {int(self.purchase_response_timeout_sec)} 秒内未收到有效购买回执；已自动结束验证码空窗期并继续后续购买"
            if self.retry_failed_after_batch and not job.retry_after_batch_active:
                await self._defer_current_purchase_for_retry(key, job, send_cb, reason)
            else:
                await self._skip_current_purchase_and_continue(key, job, send_cb, reason)
            return
        if job.phase == "ALCHEMY_WAIT":
            if now - job.last_command_ts <= self.alchemy_confirm_timeout_sec:
                return
            current = job.alchemy_queue[job.alchemy_index] if 0 <= job.alchemy_index < len(job.alchemy_queue) else {}
            await self._pause_job(key, job, send_cb, f"发送炼丹配方 {current.get('pill', '未知丹药')} 后未收到“恭喜道友成功炼成丹药”回执。")
            return
        if job.phase == "HERB_BUY_SCANNING":
            if now - job.last_command_ts <= self.page_timeout_sec:
                return
            if job.retry_count < self.max_page_retries:
                job.retry_count += 1
                await send_cb(f"⚠️ 自动购买药材拉取第 {job.current_page} 页超时，正在重试 {job.retry_count}/{self.max_page_retries}。")
                await self._send_herb_buy_page(job, send_cb)
                return
            await send_cb(f"⚠️ 自动购买药材第 {job.current_page} 页超时，跳过当前页。")
            await self._finish_herb_buy_page(key, job, send_cb)
            return
        if job.phase == "HERB_BUY_WAIT":
            if now - job.last_command_ts <= self.purchase_response_timeout_sec:
                return
            item = dict(job.herb_buy_current_item or {})
            name = self.normalize_name(item.get("name", "")) or "未知药材"
            await send_cb(
                f"⚠️ 购买 {name} 后 {int(self.purchase_response_timeout_sec)} 秒内未收到回执；"
                f"已自动结束验证码空窗期并跳过当前购买。"
            )
            job.herb_buy_failed.append(name)
            job.herb_buy_total_fail += 1
            job.herb_buy_current_item = {}
            job.herb_buy_buy_index += 1
            job.updated_at = time.time()
            await self._send_next_herb_buy_purchase(key, job, send_cb)
            return

    def _load_recipes(self) -> List[Recipe]:
        try:
            mtime = os.path.getmtime(self.recipe_path)
        except Exception:
            mtime = 0.0
        if self._recipes and mtime == self._recipe_mtime:
            return self._recipes
        raw = self._read_text_guess_encoding(self.recipe_path)
        recipes: List[Recipe] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("配方"):
                continue
            m = self.RE_RECIPE.search(line)
            if not m:
                continue
            pill_full = m.group("pill").strip()
            pill_name, grade = self._split_pill_grade(pill_full)
            guide = self._parse_guide(m.group("guide"))
            if guide is None:
                continue
            recipes.append(
                Recipe(
                    pill=pill_name,
                    grade=grade,
                    furnace=m.group("furnace").strip(),
                    materials=[
                        MaterialReq("主药", self.normalize_name(m.group("main")), int(m.group("main_qty"))),
                        guide,
                        MaterialReq("辅药", self.normalize_name(m.group("assist")), int(m.group("assist_qty"))),
                    ],
                    raw=line,
                )
            )
        self._recipes = recipes
        self._recipe_mtime = mtime
        return recipes

    @staticmethod
    def _read_text_guess_encoding(path: str) -> str:
        with open(path, "rb") as f:
            data = f.read()
        for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                return data.decode(enc)
            except Exception:
                continue
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _split_pill_grade(pill_full: str) -> Tuple[str, str]:
        text = str(pill_full or "").strip().rstrip("=")
        if "-" in text:
            name, grade = text.rsplit("-", 1)
            return name.strip(), grade.strip()
        return text, ""

    def _parse_guide(self, text: str) -> Optional[MaterialReq]:
        text = str(text or "").strip()
        m_any = re.match(r"任意(?P<prop>性[平寒热])(?P<qty>\d+)$", text)
        if m_any:
            return MaterialReq("药引", "", int(m_any.group("qty")), wildcard_prop=m_any.group("prop"))
        m = re.match(r"(?P<name>.+?)(?P<qty>\d+)$", text)
        if not m:
            return None
        return MaterialReq("药引", self.normalize_name(m.group("name")), int(m.group("qty")))

    def _cleanup_text(self, text: str) -> str:
        text = str(text or "")
        text = self.ZERO_WIDTH_RE.sub("", text)
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"mqqapi://\S+", "", text)
        text = text.replace("\r", "\n")
        return text.strip()

    def _looks_like_market_text(self, text: str) -> bool:
        s = self.ZERO_WIDTH_RE.sub("", str(text or ""))
        s = re.sub(r"\s+", "", s)
        return any(k in s for k in ("坊市", "药材", "售价", "价格", "单价", "市场价")) or any(name in s for name in self.herb_names)

    def parse_market_items(self, text: str) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        raw = self._normalize_market_raw_text(text)
        commands_by_name: Dict[str, str] = {}
        link_prices: Dict[str, float] = {}
        for m in self.MARKET_INLINE_RE.finditer(raw):
            name = self.normalize_name(m.group("name"))
            price = self._to_wan(m.group("price"), m.group("unit") or "")
            buy_command = self._normalize_buy_command(self._extract_inline_command(m.group("url") or ""))
            if not name or price <= 0:
                continue
            if buy_command:
                commands_by_name[name] = buy_command
            result[name] = {"price": price, "buy_command": buy_command}
        for m in self.MARKET_LINK_RE.finditer(raw):
            name = self.normalize_name(m.group("name"))
            if not name or name in {"物品功效", "翻页"}:
                continue
            buy_command = self._normalize_buy_command(self._extract_inline_command(m.group("url") or ""))
            if not buy_command:
                continue
            commands_by_name[name] = buy_command
            price = self._extract_price_before_pos(raw, m.start())
            if price is not None and price > 0:
                link_prices[name] = price
                if name not in result:
                    result[name] = {"price": price, "buy_command": buy_command}
            if name in result and not result[name].get("buy_command"):
                result[name]["buy_command"] = buy_command
        fallback_prices = self._parse_market_prices_from_text(raw)
        for name, price in fallback_prices.items():
            if price <= 0:
                continue
            if name not in result:
                result[name] = {"price": price, "buy_command": commands_by_name.get(name, "")}
            else:
                result[name]["price"] = float(result[name].get("price") or price)
                if not result[name].get("buy_command") and commands_by_name.get(name):
                    result[name]["buy_command"] = commands_by_name[name]
        for name, command in commands_by_name.items():
            if name in result and not result[name].get("buy_command"):
                result[name]["buy_command"] = command
            elif name not in result and name in link_prices:
                result[name] = {"price": link_prices[name], "buy_command": command}
        return result

    def _normalize_market_raw_text(self, text: str) -> str:
        raw = str(text or "")
        raw = html.unescape(raw).replace("\\/", "/")
        raw = raw.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
        if "\\u" in raw or "\\x" in raw:
            try:
                raw = bytes(raw, "utf-8").decode("unicode_escape")
            except Exception:
                pass
        return raw

    def _extract_inline_command(self, url: str) -> str:
        try:
            command = parse_qs(urlparse(url).query).get("command", [""])[0]
            return unquote(str(command or "")).strip()
        except Exception:
            return ""

    def _extract_price_before_pos(self, text: str, pos: int) -> Optional[float]:
        head = str(text or "")[max(0, int(pos) - 160): int(pos)]
        price_matches = list(self.PRICE_LABEL_RE.finditer(head))
        if price_matches:
            m = price_matches[-1]
            before = head[max(0, m.start() - 4):m.start()]
            if "炼金" not in before:
                return self._to_wan(m.group("price"), m.group("unit") or "")
        any_matches = list(self.ANY_PRICE_RE.finditer(head))
        if any_matches and any(k in head for k in ("价格", "售价", "单价", "坊市价", "市场价")):
            m = any_matches[-1]
            return self._to_wan(m.group("price"), m.group("unit") or "")
        return None

    def parse_market_prices(self, text: str) -> Dict[str, float]:
        return {name: float(item.get("price") or 0) for name, item in self.parse_market_items(text).items()}

    def _parse_market_prices_from_text(self, text: str) -> Dict[str, float]:
        result: Dict[str, float] = {}
        clean = self._cleanup_text(text)
        clean = re.sub(r"炼金(?:价格|价)?\s*[:：=]?\s*\d+(?:\.\d+)?\s*(?:万|w|W|灵石)?", "", clean)
        lines: List[str] = []
        for raw_line in clean.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            lines.append(line)
            if any(sep in line for sep in ("|", "｜", "，", ",", "；", ";")):
                lines.extend(x.strip() for x in re.split(r"[|｜，,；;]", line) if x.strip())
        for line in lines:
            matched_names = [name for name in self.herb_names if name in line]
            for name in matched_names:
                price = self._extract_price_near_name(line, name)
                if price is not None and price > 0:
                    result[name] = price
        return result

    def _extract_price_near_name(self, line: str, name: str) -> Optional[float]:
        idx = line.find(name)
        if idx < 0:
            return None
        head = line[max(0, idx - 80):idx]
        head_matches = list(self.PRICE_LABEL_RE.finditer(head))
        if head_matches:
            m = head_matches[-1]
            if "炼金" not in head[max(0, m.start() - 4):m.start()]:
                return self._to_wan(m.group("price"), m.group("unit") or "")
        head_any = list(self.ANY_PRICE_RE.finditer(head))
        if head_any and any(k in head for k in ("价格", "售价", "单价", "坊市价", "市场价")):
            m = head_any[-1]
            return self._to_wan(m.group("price"), m.group("unit") or "")
        tail = line[idx + len(name): idx + len(name) + 80]
        next_pos = len(tail)
        for other in self.herb_names:
            if other == name:
                continue
            p = tail.find(other)
            if p >= 0:
                next_pos = min(next_pos, p)
        tail = tail[:next_pos]
        for m in self.PRICE_LABEL_RE.finditer(tail):
            if "炼金" not in tail[max(0, m.start() - 4):m.start()]:
                return self._to_wan(m.group("price"), m.group("unit") or "")
        candidates = list(self.ANY_PRICE_RE.finditer(tail))
        if candidates:
            m = candidates[-1]
            return self._to_wan(m.group("price"), m.group("unit") or "")
        m2 = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", tail)
        if m2:
            return self._to_wan(m2.group(1), "")
        return None

    @staticmethod
    def _to_wan(value: str, unit: str) -> float:
        try:
            v = float(value)
        except Exception:
            return 0.0
        unit = str(unit or "").strip().lower()
        if unit in {"灵石", "lingshi"}:
            return v / 10000.0
        if not unit and v >= 10000:
            return v / 10000.0
        return v

    def _estimate_candidate_command_count(self, materials: List[Dict[str, Any]], *, use_purchase_qty: bool = False) -> int:
        purchase_count = 0
        pages: set[int] = set()
        for item in materials or []:
            if use_purchase_qty:
                qty = int(item.get("purchase_qty") if item.get("purchase_qty") is not None else item.get("qty") or 0)
            else:
                qty = int(item.get("qty") or 0)
            if qty <= 0:
                continue
            purchase_count += qty
            page = int(item.get("page") or 0)
            if page > 0:
                pages.add(page)
        if purchase_count <= 0:
            return 1
        # 同页同药材每买一次后购买指令失效，保守估算为每次购买约消耗 1 次刷新 + 1 次购买。
        return max(1, purchase_count * 2 + 1)

    def _attach_command_efficiency(self, cand: Dict[str, Any], *, use_purchase_qty: bool = False) -> Dict[str, Any]:
        materials = cand.get("materials", []) or []
        command_count = self._estimate_candidate_command_count(materials, use_purchase_qty=use_purchase_qty)
        score_profit = float(cand.get("score_profit", 0) or 0)
        purchase_count = 0
        for item in materials:
            if use_purchase_qty:
                qty = int(item.get("purchase_qty") if item.get("purchase_qty") is not None else item.get("qty") or 0)
            else:
                qty = int(item.get("qty") or 0)
            purchase_count += max(0, qty)
        cand["purchase_command_count"] = purchase_count
        cand["estimated_command_count"] = command_count
        cand["profit_per_command"] = score_profit / max(1, command_count)
        return cand

    def _candidate_efficiency_sort_key(self, cand: Dict[str, Any]) -> Tuple[float, float, float, str, str]:
        return (
            float(cand.get("profit_per_command", 0) or 0),
            float(cand.get("score_profit", 0) or 0),
            -float(cand.get("estimated_command_count", 0) or 0),
            cand["recipe"].pill if cand.get("recipe") else "",
            self._candidate_uid(cand),
        )

    def _compute_candidates(self, prices: Dict[str, float], buy_commands: Optional[Dict[str, str]] = None, pages_by_name: Optional[Dict[str, int]] = None, *, yield_count: Optional[int] = None, pill_filter: str = "", allow_unknown_sale: bool = False) -> Tuple[List[Dict[str, Any]], int, int]:
        recipes = self._load_recipes()
        buy_commands = buy_commands or {}
        pages_by_name = pages_by_name or {}
        yield_count = min(7, max(1, int(yield_count or self.default_yield_count)))
        pill_filter = self.normalize_name(pill_filter)
        candidates: List[Dict[str, Any]] = []
        skipped_no_price = 0
        skipped_no_wildcard = 0
        for recipe in recipes:
            if recipe.pill not in FIXED_PILL_SALE_PRICE and not (allow_unknown_sale and pill_filter and recipe.pill == pill_filter):
                continue
            if pill_filter and recipe.pill != pill_filter:
                continue
            resolved = self._resolve_recipe(recipe, prices, buy_commands, pages_by_name)
            if not resolved:
                skipped_no_price += 1
                continue
            if resolved.get("wildcard_missing"):
                skipped_no_wildcard += 1
                continue
            cost = sum(float(x["qty"]) * float(x["unit_price"]) for x in resolved["materials"])
            sale_known = recipe.pill in FIXED_PILL_SALE_PRICE
            sale = float(FIXED_PILL_SALE_PRICE.get(recipe.pill, 0.0))
            profits = [{"count": n, "revenue": sale * n, "profit": sale * n - cost} for n in range(1, 8)]
            score_profit = sale * yield_count - cost
            cand = {"recipe": recipe, "materials": resolved["materials"], "cost": cost, "sale": sale, "profits": profits, "profit7": profits[-1]["profit"], "yield_count": yield_count, "score_profit": score_profit, "unknown_sale": not sale_known, "abandoned": False}
            candidates.append(self._attach_command_efficiency(cand))
        return candidates, skipped_no_price, skipped_no_wildcard

    def _select_profitable_best_by_pill(self, candidates: List[Dict[str, Any]], *, yield_count: Optional[int] = None, min_profit: Optional[float] = None) -> List[Dict[str, Any]]:
        yield_count = min(7, max(1, int(yield_count or self.default_yield_count)))
        threshold = 0.0
        best_by_pill: Dict[str, Dict[str, Any]] = {}
        for cand in candidates:
            recipe: Recipe = cand["recipe"]
            profit = float(cand.get("score_profit", cand["sale"] * yield_count - cand["cost"]))
            if profit < threshold:
                continue
            old = best_by_pill.get(recipe.pill)
            if old is None or (profit, -float(cand["cost"]), float(cand["sale"])) > (float(old.get("score_profit", 0)), -float(old["cost"]), float(old["sale"])):
                best_by_pill[recipe.pill] = cand
        return sorted(best_by_pill.values(), key=lambda x: (float(x.get("score_profit", 0)), -float(x["cost"]), x["recipe"].pill), reverse=True)

    def _candidate_uid(self, cand: Dict[str, Any]) -> str:
        recipe = cand.get("recipe")
        if recipe is None:
            return str(cand.get("formula_uid") or "")
        mats = []
        for m in cand.get("materials", []):
            mats.append(f"{m.get('role','')}:{self.normalize_name(m.get('name',''))}:{int(m.get('qty') or 1)}")
        raw = getattr(recipe, "raw", "") or "|".join(mats)
        return f"{recipe.pill}|{raw}"

    def _select_profitable_all_candidates(self, candidates: List[Dict[str, Any]], *, min_profit: Optional[float] = None) -> List[Dict[str, Any]]:
        selected, _ = self._select_batch_primary_and_reserve(candidates, min_profit=min_profit)
        return selected

    def _select_batch_primary_and_reserve(self, candidates: List[Dict[str, Any]], *, min_profit: Optional[float] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        threshold = 0.0
        valid = [c for c in candidates if float(c.get("score_profit", 0)) >= threshold]
        valid.sort(key=lambda x: (float(x.get("score_profit", 0)), -float(x.get("cost", 0)), x["recipe"].pill, self._candidate_uid(x)), reverse=True)

        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for cand in valid:
            uid = self._candidate_uid(cand)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            base = self._clone_candidate(cand)
            base["formula_uid"] = uid
            deduped.append(base)

        limit = int(self.max_batch_formula_count or 0)
        primary_base = deduped[:limit] if limit > 0 else deduped
        reserve_base = deduped[limit:] if limit > 0 else []

        selected: List[Dict[str, Any]] = []
        repeat = max(1, int(self.max_formula_per_pill or 1))
        for base in primary_base:
            uid = str(base.get("formula_uid") or self._candidate_uid(base))
            for i in range(repeat):
                item = self._clone_candidate(base)
                item["formula_uid"] = uid
                item["formula_repeat_index"] = i + 1
                selected.append(item)
        return selected, reserve_base

    def _take_next_reserve_candidate(self, job: AutoAlchemyJob, used_uids: set[str]) -> Optional[Dict[str, Any]]:
        while job.batch_reserve_candidates:
            cand = self._clone_candidate(job.batch_reserve_candidates.pop(0))
            uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
            if not uid or uid in used_uids:
                continue
            self._refresh_candidate_prices(cand, job.prices, job.buy_commands, job.pages_by_name)
            if float(cand.get("score_profit", 0)) < 0:
                continue
            cand["formula_uid"] = uid
            used_uids.add(uid)
            return cand
        return None

    def _expand_base_candidate_for_batch(self, base: Dict[str, Any], repeat_count: Optional[int] = None) -> List[Dict[str, Any]]:
        repeat = max(1, int(repeat_count if repeat_count is not None else self.max_formula_per_pill or 1))
        uid = str(base.get("formula_uid") or self._candidate_uid(base))
        out: List[Dict[str, Any]] = []
        for i in range(repeat):
            item = self._clone_candidate(base)
            item["formula_uid"] = uid
            item["formula_repeat_index"] = i + 1
            out.append(item)
        return out

    def _select_best_for_target(self, candidates: List[Dict[str, Any]], *, min_profit: Optional[float] = None, allow_unknown_sale: bool = False) -> Optional[Dict[str, Any]]:
        if allow_unknown_sale:
            unknown = [c for c in candidates if c.get("unknown_sale")]
            if unknown:
                unknown.sort(key=lambda x: (float(x.get("cost", 0)), x["recipe"].pill))
                return unknown[0]
        threshold = float(self.min_profit_6pill if min_profit is None else min_profit)
        threshold = 0.0
        valid = [c for c in candidates if float(c.get("score_profit", 0)) >= threshold]
        if not valid:
            return None
        valid.sort(key=lambda x: (float(x.get("score_profit", 0)), -float(x.get("cost", 0)), float(x.get("sale", 0))), reverse=True)
        return valid[0]

    def _clone_candidate(self, cand: Dict[str, Any]) -> Dict[str, Any]:
        copied = dict(cand)
        copied["materials"] = [dict(x) for x in cand.get("materials", [])]
        return copied

    def _candidate_purchase_key(self, cand: Dict[str, Any]) -> Tuple[Tuple[str, str, int], ...]:
        parts: List[Tuple[str, str, int]] = []
        for item in cand.get("materials", []) or []:
            name = self.normalize_name(item.get("name", ""))
            if not name:
                continue
            parts.append((str(item.get("role") or ""), name, int(item.get("qty") or 1)))
        return tuple(sorted(parts))

    def _freeze_candidate_for_existing_queue(self, cand: Dict[str, Any]) -> Dict[str, Any]:
        frozen = self._clone_candidate(cand)
        frozen["purchase_frozen"] = True
        return frozen

    def _merge_existing_queue_candidates_with_new(self, old_active: List[Dict[str, Any]], new_selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        new_key_counts: Dict[Tuple[Tuple[str, str, int], ...], int] = {}
        for cand in new_selected or []:
            key = self._candidate_purchase_key(cand)
            if key:
                new_key_counts[key] = int(new_key_counts.get(key, 0)) + 1

        frozen_old: List[Dict[str, Any]] = []
        for cand in old_active or []:
            key = self._candidate_purchase_key(cand)
            if key and int(new_key_counts.get(key, 0)) > 0:
                new_key_counts[key] = int(new_key_counts.get(key, 0)) - 1
                continue
            frozen_old.append(self._freeze_candidate_for_existing_queue(cand))
        return frozen_old + list(new_selected or []), len(frozen_old)

    def _build_batch_purchase_plan(self, selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        aggregate: Dict[str, Dict[str, Any]] = {}
        for idx, cand in enumerate(selected):
            if cand.get("abandoned") or cand.get("purchase_frozen"):
                continue
            recipe = cand.get("recipe")
            for item in cand.get("materials", []):
                name = self.normalize_name(item.get("name", ""))
                if not name:
                    continue
                qty = int(item.get("qty") or 1)
                if name not in aggregate:
                    aggregate[name] = {"name": name, "qty": 0, "unit_price": float(item.get("unit_price") or 0), "page": int(item.get("page") or 0), "buy_command": item.get("buy_command", ""), "roles": set(), "pills": set()}
                aggregate[name]["qty"] += qty
                aggregate[name]["roles"].add(str(item.get("role") or ""))
                if recipe is not None:
                    aggregate[name]["pills"].add(recipe.pill)
                if item.get("buy_command"):
                    aggregate[name]["buy_command"] = item.get("buy_command")
                if item.get("page"):
                    aggregate[name]["page"] = int(item.get("page") or 0)
                if item.get("unit_price"):
                    aggregate[name]["unit_price"] = float(item.get("unit_price") or 0)
        plan = list(aggregate.values())
        for item in plan:
            item["roles"] = sorted(x for x in item.get("roles", set()) if x)
            item["pills"] = sorted(x for x in item.get("pills", set()) if x)
        plan.sort(key=lambda x: (int(x.get("page") or 999), x["name"]))
        return plan

    def _build_purchase_plan_for_job(self, job: AutoAlchemyJob) -> List[Dict[str, Any]]:
        if job.mode == "backpack" or job.backpack_counts:
            return self._build_batch_purchase_plan_with_backpack(job.batch_selected, job.backpack_counts)
        return self._build_batch_purchase_plan(job.batch_selected)

    def _candidate_material_price_baseline(self, cand: Dict[str, Any]) -> Dict[str, float]:
        baseline: Dict[str, float] = {}
        for item in cand.get("materials", []) or []:
            name = self.normalize_name(item.get("name", ""))
            if not name:
                continue
            price = float(item.get("unit_price") or 0)
            if price > 0:
                baseline[name] = price
        return baseline

    def _refresh_formula_price_baselines(self, job: AutoAlchemyJob) -> None:
        baselines: Dict[str, Dict[str, float]] = {}
        for cand in job.batch_selected or []:
            if cand.get("abandoned"):
                continue
            uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
            if not uid or uid in baselines:
                continue
            cand["formula_uid"] = uid
            baselines[uid] = self._candidate_material_price_baseline(cand)
        job.formula_price_baselines = baselines

    def _anchor_candidate_price_baseline(self, job: AutoAlchemyJob, cand: Dict[str, Any]) -> None:
        uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
        if not uid:
            return
        cand["formula_uid"] = uid
        job.formula_price_baselines[uid] = self._candidate_material_price_baseline(cand)

    def _candidate_within_price_baseline(self, job: AutoAlchemyJob, cand: Dict[str, Any]) -> bool:
        uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
        baseline = job.formula_price_baselines.get(uid) or {}
        if not baseline:
            return False
        for name, base_price in baseline.items():
            current = float(job.prices.get(name, 0) or 0)
            if current <= 0:
                return False
            if abs(current - float(base_price)) > self.FORMULA_REUSE_PRICE_TOLERANCE:
                return False
        return True

    def _candidate_reusable_after_price_refresh(self, job: AutoAlchemyJob, cand: Dict[str, Any]) -> bool:
        if not self._candidate_within_price_baseline(job, cand):
            return False
        if cand.get("unknown_sale"):
            return True
        return float(cand.get("score_profit", 0)) >= 0

    def _is_queue_item_pending(self, job: AutoAlchemyJob, item: Dict[str, Any]) -> bool:
        name = self.normalize_name(item.get("name", ""))
        if not name:
            return False
        for q in job.batch_buy_queue[max(0, int(job.batch_buy_index or 0)):]:
            if self.normalize_name(q.get("name", "")) == name:
                return True
        for q in job.deferred_retry_items or []:
            if self.normalize_name(q.get("name", "")) == name:
                return True
        current_name = self.normalize_name((job.batch_current_item or {}).get("name", ""))
        return bool(current_name and current_name == name)

    def _append_new_purchase_items_preserving_queue(self, job: AutoAlchemyJob, new_queue: List[Dict[str, Any]]) -> int:
        appended = 0
        for item in new_queue or []:
            job.batch_buy_queue.append(dict(item))
            appended += 1
        return appended

    def _pending_purchase_count_for_name(self, job: AutoAlchemyJob, name: str) -> int:
        target = self.normalize_name(name)
        if not target:
            return 0
        total = 0
        start = max(0, int(job.batch_buy_index or 0))
        for item in (job.batch_buy_queue or [])[start:]:
            if self.normalize_name(item.get("name", "")) == target:
                total += max(1, int(item.get("qty") or 1))
        for item in job.deferred_retry_items or []:
            if self.normalize_name(item.get("name", "")) == target:
                total += max(1, int(item.get("qty") or 1))
        return total

    def _build_incremental_purchase_queue(self, job: AutoAlchemyJob, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        append_plan: List[Dict[str, Any]] = []
        for item in plan or []:
            name = self.normalize_name(item.get("name", ""))
            if not name:
                continue
            required = int(item.get("qty") or 0)
            done = int(job.purchased_counts.get(name, 0) or 0) + int(job.failed_counts.get(name, 0) or 0)
            pending = self._pending_purchase_count_for_name(job, name)
            missing = max(0, required - done - pending)
            if missing <= 0:
                continue
            new_item = dict(item)
            new_item["name"] = name
            new_item["qty"] = missing
            append_plan.append(new_item)
        return self._expand_purchase_queue(append_plan, {}, {})

    def _refresh_batch_buy_expected_preserving_queue(self, job: AutoAlchemyJob) -> None:
        remaining = max(0, len(job.batch_buy_queue or []) - max(0, int(job.batch_buy_index or 0)))
        job.batch_buy_expected = int(job.batch_success_count) + remaining

    def _prune_formula_price_baselines(self, job: AutoAlchemyJob) -> None:
        active = {
            str(c.get("formula_uid") or self._candidate_uid(c))
            for c in job.batch_selected or []
            if not c.get("abandoned")
        }
        job.formula_price_baselines = {
            uid: dict(base)
            for uid, base in (job.formula_price_baselines or {}).items()
            if uid in active
        }

    def _expand_purchase_queue(self, plan: List[Dict[str, Any]], purchased_counts: Optional[Dict[str, int]] = None, failed_counts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        purchased_counts = purchased_counts or {}
        failed_counts = failed_counts or {}
        remaining_items: List[Tuple[Dict[str, Any], int]] = []
        for item in plan:
            name = self.normalize_name(item.get("name", ""))
            if not name:
                continue
            remaining = max(0, int(item.get("qty") or 0) - int(purchased_counts.get(name, 0) or 0) - int(failed_counts.get(name, 0) or 0))
            if remaining <= 0:
                continue
            base = dict(item)
            base["name"] = name
            remaining_items.append((base, remaining))
        remaining_items.sort(key=lambda pair: (int(pair[0].get("page") or 999), pair[0].get("name", "")))

        queue: List[Dict[str, Any]] = []
        if self.multi_round_buy_enabled:
            max_rounds = max((remain for _, remain in remaining_items), default=0)
            for round_no in range(1, max_rounds + 1):
                round_items: List[Dict[str, Any]] = []
                for item, remain in remaining_items:
                    if remain >= round_no:
                        q = dict(item)
                        q["qty"] = 1
                        q["purchase_round"] = round_no
                        round_items.append(q)
                round_items.sort(key=lambda x: (int(x.get("page") or 999), x.get("name", "")))
                queue.extend(round_items)
        else:
            for item, remain in remaining_items:
                for i in range(remain):
                    q = dict(item)
                    q["qty"] = 1
                    q["purchase_round"] = i + 1
                    queue.append(q)
            queue.sort(key=lambda x: (int(x.get("page") or 999), x.get("name", "")))
        return queue


    async def _finish_collecting_and_start_batch_buy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        """坊市扫描+背包采集完成后，进行丹方匹配并进入购买/炼丹阶段。"""
        use_backpack = self.use_backpack_for_batch_mode and bool(job.backpack_counts)
        threshold = self.batch_mode_profit_threshold
        dyn_note = ""
        if job.dynamic_buy_success > 0:
            dyn_note = f"\n🛒 坊市动态购买完成：成功 {job.dynamic_buy_success} 次，失败 {job.dynamic_buy_fail} 次。"

        if use_backpack:
            # 背包抵扣模式：使用 _resolve_recipe_with_backpack 进行智能抵扣
            try:
                selected, candidate_count, skipped_count = self._select_batch_with_backpack(
                    job, threshold=threshold,
                )
            except Exception as e:
                self.jobs.pop(key, None)
                await send_cb(f"❌ 自动炼丹失败：计算丹方异常：{e}")
                return
            job.batch_selected = selected
            job.batch_reserve_candidates = []
            if not selected:
                self.jobs.pop(key, None)
                self._write_snapshot(job.prices, "", job.buy_commands, job.pages_by_name)
                await send_cb(
                    f"❌【自动炼丹】未找到利润 > {threshold}万 的丹方。{dyn_note}\n"
                    f"背包药材种类：{len(job.backpack_counts)}\n"
                    f"坊市价格数：{len(job.prices)}\n"
                    f"可计算候选数：{candidate_count}\n"
                    f"跳过配方数：{skipped_count}\n"
                    f"筛选规则：成丹 {job.yield_count} 颗利润 > {threshold}万，背包药材智能抵扣。"
                )
                return
            await self._prepare_and_start_buying(key, job, send_cb, candidate_count, skipped_count)
        else:
            # 无背包抵扣：使用原有逻辑
            try:
                candidates, skipped_no_price, skipped_no_wildcard = self._compute_candidates(
                    job.prices, job.buy_commands, job.pages_by_name, yield_count=job.yield_count,
                )
            except Exception as e:
                self.jobs.pop(key, None)
                await send_cb(f"❌ 自动炼丹全功能流程失败：读取丹方文件异常：{e}")
                return
            if self.batch_repeat_until_threshold:
                selected, reserve = self._select_batch_primary_and_reserve(candidates, min_profit=threshold)
            else:
                base_selected = self._select_profitable_best_by_pill(candidates, yield_count=job.yield_count, min_profit=threshold)
                selected = []
                for base in base_selected[: self.max_batch_formula_count or len(base_selected)]:
                    uid = self._candidate_uid(base)
                    base = self._clone_candidate(base)
                    base["formula_uid"] = uid
                    selected.extend(self._expand_base_candidate_for_batch(base))
                reserve = []
            job.batch_selected = selected
            job.batch_reserve_candidates = reserve
            if not selected:
                self.jobs.pop(key, None)
                report = self._format_no_profitable_report(job, len(candidates), len(job.prices), skipped_no_price + skipped_no_wildcard)
                if dyn_note:
                    report += dyn_note
                self._write_snapshot(job.prices, report, job.buy_commands, job.pages_by_name)
                await send_cb(report)
                return
            await self._prepare_and_start_buying(key, job, send_cb, len(candidates), skipped_no_price + skipped_no_wildcard)

    async def _finish_collecting_and_start_target_buy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        try:
            candidates, skipped_no_price, skipped_no_wildcard = self._compute_candidates(job.prices, job.buy_commands, job.pages_by_name, yield_count=job.yield_count, pill_filter=job.target_pill, allow_unknown_sale=self.target_unknown_price_execute)
        except Exception as e:
            self.jobs.pop(key, None)
            await send_cb(f"❌ 指定丹药自动炼丹失败：读取丹方文件异常：{e}")
            return
        allow_unknown = self.target_unknown_price_execute and job.target_pill not in FIXED_PILL_SALE_PRICE
        best = self._select_best_for_target(candidates, min_profit=job.min_profit, allow_unknown_sale=allow_unknown)
        if not best:
            self.jobs.pop(key, None)
            msg = (
                f"❌ 指定丹药自动炼丹停止：{job.target_pill} 当前没有药材价格完整的可用丹方。\n"
                if allow_unknown
                else f"❌ 指定丹药自动炼丹停止：{job.target_pill} 当前没有成丹 {job.yield_count} 颗利润不亏本的可用丹方。\n"
            )
            msg += f"可计算配方数：{len(candidates)}；因缺少实时价格跳过：{skipped_no_price + skipped_no_wildcard}。"
            await send_cb(msg)
            return
        job.batch_selected = [self._clone_candidate(best) for _ in range(max(1, int(job.target_rounds or 1)))]
        await self._prepare_and_start_buying(key, job, send_cb, len(candidates), skipped_no_price + skipped_no_wildcard)

    async def _prepare_and_start_buying(self, key: str, job: AutoAlchemyJob, send_cb, candidate_count: int, skipped_count: int) -> None:
        self._refresh_formula_price_baselines(job)
        plan = self._build_purchase_plan_for_job(job)
        job.batch_purchase_plan = plan
        job.batch_formula_texts = [self._format_recipe_send_command(c["recipe"], c["materials"]) for c in job.batch_selected if not c.get("abandoned")]
        job.batch_buy_queue = self._expand_purchase_queue(job.batch_purchase_plan, job.purchased_counts, job.failed_counts)
        job.batch_buy_index = 0
        job.batch_current_item = {}
        job.batch_buy_expected = int(job.batch_success_count) + len(job.batch_buy_queue)
        job.round_refreshed_rounds = []
        job.round_refreshing_round = 0
        job.round_refreshed_page_keys = []
        job.round_refreshing_page = 0
        job.batch_report = self._format_batch_buy_plan_report(job, candidate_count, skipped_count)
        self._write_snapshot(job.prices, job.batch_report, job.buy_commands, job.pages_by_name)
        if not self.batch_buy_enabled:
            self.jobs.pop(key, None)
            await send_cb(job.batch_report)
            return
        await send_cb(job.batch_report + "\n\n🛒 开始购买药材。")
        await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)

    def _deferred_retry_name_set(self, job: AutoAlchemyJob) -> set:
        names = {self.normalize_name(x) for x in (job.deferred_retry_names or []) if self.normalize_name(x)}
        for item in job.deferred_retry_items or []:
            name = self.normalize_name(item.get("name", ""))
            if name:
                names.add(name)
        return names

    def _required_count_for_name(self, job: AutoAlchemyJob, name: str) -> int:
        target = self.normalize_name(name)
        if not target:
            return 0
        total = 0
        for item in job.batch_purchase_plan or []:
            if self.normalize_name(item.get("name", "")) == target:
                total += int(item.get("qty") or 0)
        return total

    def _remaining_count_for_name(self, job: AutoAlchemyJob, name: str) -> int:
        target = self.normalize_name(name)
        required = self._required_count_for_name(job, target)
        if required <= 0:
            return 0
        done = int(job.purchased_counts.get(target, 0) or 0) + int(job.failed_counts.get(target, 0) or 0)
        return max(0, required - done)

    def _mark_all_remaining_failed(self, job: AutoAlchemyJob, name: str) -> None:
        target = self.normalize_name(name)
        if not target:
            return
        required = self._required_count_for_name(job, target)
        purchased = int(job.purchased_counts.get(target, 0) or 0)
        if required > 0:
            job.failed_counts[target] = max(int(job.failed_counts.get(target, 0) or 0), max(0, required - purchased))
        else:
            job.failed_counts[target] = int(job.failed_counts.get(target, 0) or 0) + 1

    async def _send_next_batch_purchase_or_start_alchemy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        while job.batch_buy_index < len(job.batch_buy_queue):
            item = dict(job.batch_buy_queue[job.batch_buy_index] or {})
            name = self.normalize_name(item.get("name", ""))
            page = int(item.get("page") or job.pages_by_name.get(name, 0) or 0)
            round_no = int(item.get("purchase_round") or 1)

            if name and (not job.retry_after_batch_active) and self._remaining_count_for_name(job, name) <= 0:
                job.batch_buy_index += 1
                continue

            if name and (not job.retry_after_batch_active) and name in self._deferred_retry_name_set(job):
                job.batch_buy_index += 1
                continue

            if (
                self.multi_round_buy_enabled
                and self.refresh_pages_each_buy_round
                and page > 0
                and not job.retry_after_batch_active
            ):
                page_key = f"{round_no}:{page}"
                if page_key not in set(job.round_refreshed_page_keys or []):
                    job.phase = "BATCH_ROUND_REFRESHING"
                    job.round_refreshing_round = round_no
                    job.round_refreshing_page = page
                    job.scan_pages = [page]
                    job.scan_index = 0
                    job.current_page = page
                    job.retry_count = 0
                    job.updated_at = time.time()
                    await send_cb(f"🔄 第{round_no}轮，第{page}页刷新购买指令。")
                    await self._send_page(job, send_cb)
                    return

            cmd = self._normalize_buy_command(str(item.get("buy_command") or job.buy_commands.get(name, "") or ""))
            if name and cmd:
                item["buy_command"] = cmd
                item["purchase_round"] = round_no
                if page:
                    item["page"] = page
                await self._send_fresh_purchase_command(job, send_cb, item)
                return
            if name and page > 0:
                job.phase = "BATCH_BUY_REFRESHING"
                job.batch_current_item = item
                job.refresh_item_name = name
                job.refresh_item_page = page
                job.refresh_old_price = float(item.get("unit_price") or job.prices.get(name, 0) or 0)
                job.current_page = page
                job.scan_pages = [page]
                job.scan_index = 0
                job.updated_at = time.time()
                await self._send_page(job, send_cb)
                return
            await self._skip_current_purchase_and_continue(key, job, send_cb, f"药材 {name or '未知'} 缺少购买指令和所在页，无法购买")
            return
        if self.retry_failed_after_batch and job.deferred_retry_items and not job.retry_after_batch_started:
            await self._start_deferred_retry_refresh(key, job, send_cb)
            return
        if job.retry_after_batch_active:
            job.retry_after_batch_active = False
            if job.retry_refresh_pages or job.deferred_retry_items:
                await self._start_next_deferred_retry_page(key, job, send_cb)
                return
        await self._start_alchemy_sequence(key, job, send_cb)

    async def _handle_round_refresh_page(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if not self._looks_like_market_text(clean_text):
            return True

        page_no = int(job.round_refreshing_page or job.current_page or 0)
        page_items = self.parse_market_items(raw_text)

        tracked_old_prices: Dict[str, float] = {}
        plan_names = {
            self.normalize_name(item.get("name", ""))
            for item in (job.batch_purchase_plan or [])
            if self.normalize_name(item.get("name", "")) and self._remaining_count_for_name(job, item.get("name", "")) > 0
        }
        for name in page_items:
            norm_name = self.normalize_name(name)
            if not norm_name or norm_name not in plan_names:
                continue
            old_price = float(job.prices.get(norm_name, 0) or 0)
            if old_price > 0:
                tracked_old_prices[norm_name] = old_price

        self._merge_market_page(job, raw_text, int(job.current_page))
        if job.scan_index + 1 < len(job.scan_pages):
            job.scan_index += 1
            job.current_page = int(job.scan_pages[job.scan_index])
            job.retry_count = 0
            if self.send_interval_sec > 0:
                await asyncio.sleep(self.send_interval_sec)
            await self._send_page(job, send_cb)
            return True

        changed_prices: List[Tuple[str, float, float]] = []
        for name, old_price in tracked_old_prices.items():
            new_price = float(job.prices.get(name, 0) or 0)
            if new_price > 0 and abs(new_price - old_price) > 1e-9:
                changed_prices.append((name, old_price, new_price))

        if changed_prices:
            target, old_price, new_price = changed_prices[0]
            keep_running, note = self._handle_price_change_for_job(job, target, old_price, new_price)
            if len(changed_prices) > 1:
                note = (note or "") + f"\n本页另有 {len(changed_prices) - 1} 种待购药材价格变化，已一并按最新价格重算队列。"
            if note:
                await send_cb(note)
            job.phase = "BUYING"
            job.updated_at = time.time()
            await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)
            return True

        round_no = int(job.round_refreshing_round or 0)
        page_no = int(job.round_refreshing_page or job.current_page or 0)
        if round_no and page_no:
            page_key = f"{round_no}:{page_no}"
            if page_key not in job.round_refreshed_page_keys:
                job.round_refreshed_page_keys.append(page_key)
        if round_no and round_no not in job.round_refreshed_rounds:
            job.round_refreshed_rounds.append(round_no)
        job.round_refreshing_round = 0
        job.round_refreshing_page = 0
        for q in job.batch_buy_queue[job.batch_buy_index:]:
            name = self.normalize_name(q.get("name", ""))
            if not name:
                continue
            if name in job.prices:
                q["unit_price"] = float(job.prices.get(name) or q.get("unit_price") or 0)
            if name in job.buy_commands:
                q["buy_command"] = job.buy_commands.get(name, "")
            if name in job.pages_by_name:
                q["page"] = int(job.pages_by_name.get(name) or q.get("page") or 0)
        job.phase = "BUYING"
        job.updated_at = time.time()
        await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)
        return True


    async def _defer_current_purchase_for_retry(self, key: str, job: AutoAlchemyJob, send_cb, reason: str) -> None:
        item = dict(job.batch_current_item or (job.batch_buy_queue[job.batch_buy_index] if 0 <= job.batch_buy_index < len(job.batch_buy_queue) else {}))
        name = self.normalize_name(item.get("name", ""))
        if name:
            item["name"] = name
            if name not in job.deferred_retry_names:
                job.deferred_retry_names.append(name)
            if not any(self.normalize_name(x.get("name", "")) == name for x in job.deferred_retry_items):
                job.deferred_retry_items.append(item)
        job.batch_current_item = {}
        job.batch_buy_index += 1
        job.updated_at = time.time()
        await send_cb(f"⏭️ 暂跳过：{name or '未知药材'}。")
        await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)

    async def _start_deferred_retry_refresh(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        deferred_names = self._deferred_retry_name_set(job)
        pages = sorted({int(job.pages_by_name.get(name, 0) or next((int(x.get("page") or 0) for x in job.deferred_retry_items if self.normalize_name(x.get("name", "")) == name), 0)) for name in deferred_names if int(job.pages_by_name.get(name, 0) or next((int(x.get("page") or 0) for x in job.deferred_retry_items if self.normalize_name(x.get("name", "")) == name), 0)) > 0})
        job.retry_after_batch_started = True
        job.retry_after_batch_active = False
        job.retry_refresh_pages = pages
        if not pages:
            await self._start_retry_queue_after_refresh(key, job, send_cb, reason="失败药材缺少页码索引，无法刷新页面，直接使用已有购买指令重试")
            return
        await self._start_next_deferred_retry_page(key, job, send_cb)

    async def _start_next_deferred_retry_page(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        if not job.retry_refresh_pages:
            if job.deferred_retry_items:
                await self._start_retry_queue_after_refresh(key, job, send_cb, reason="剩余失败药材缺少页码索引，直接使用已有购买指令重试")
                return
            await self._start_alchemy_sequence(key, job, send_cb)
            return
        page = int(job.retry_refresh_pages.pop(0) or 0)
        if page <= 0:
            await self._start_next_deferred_retry_page(key, job, send_cb)
            return
        job.phase = "BATCH_RETRY_REFRESHING"
        job.scan_pages = [page]
        job.scan_index = 0
        job.current_page = page
        job.retry_count = 0
        job.updated_at = time.time()
        await send_cb(f"🔁 重试失败药材：先刷新第 {page} 页，随后立即购买本页失败药材。")
        await self._send_page(job, send_cb)

    async def _handle_retry_refresh_page(self, key: str, job: AutoAlchemyJob, raw_text: str, clean_text: str, send_cb) -> bool:
        if not self._looks_like_market_text(clean_text):
            return True
        self._merge_market_page(job, raw_text, int(job.current_page))
        await self._start_retry_queue_after_refresh(key, job, send_cb)
        return True

    async def _start_retry_queue_after_refresh(self, key: str, job: AutoAlchemyJob, send_cb, reason: str = "") -> None:
        page_no = int(job.current_page or 0)
        retry_plan: List[Dict[str, Any]] = []
        remaining_deferred: List[Dict[str, Any]] = []
        for deferred in job.deferred_retry_items or []:
            item = dict(deferred or {})
            name = self.normalize_name(item.get("name", ""))
            if not name:
                continue
            item_page = int(job.pages_by_name.get(name, 0) or item.get("page") or 0)
            if page_no > 0 and item_page > 0 and item_page != page_no:
                remaining_deferred.append(item)
                continue
            item["name"] = name
            item["qty"] = max(1, int(item.get("qty") or 1))
            if name in job.prices:
                item["unit_price"] = float(job.prices.get(name) or item.get("unit_price") or 0)
            if name in job.buy_commands:
                item["buy_command"] = job.buy_commands.get(name, "")
            if name in job.pages_by_name:
                item["page"] = int(job.pages_by_name.get(name) or item.get("page") or 0)
            retry_plan.append(item)
        retry_items = self._expand_purchase_queue(retry_plan, {}, {})
        job.deferred_retry_items = remaining_deferred
        job.deferred_retry_names = sorted({self.normalize_name(x.get("name", "")) for x in remaining_deferred if self.normalize_name(x.get("name", ""))})
        job.retry_after_batch_active = True
        job.batch_buy_queue = retry_items
        job.batch_buy_index = 0
        job.round_refreshed_rounds = []
        job.round_refreshing_round = 0
        job.round_refreshed_page_keys = []
        job.round_refreshing_page = 0
        job.batch_current_item = {}
        job.updated_at = time.time()
        if not retry_items:
            job.retry_after_batch_active = False
            await self._start_next_deferred_retry_page(key, job, send_cb)
            return
        msg = f"🔁 开始重试第 {page_no or '?'} 页购买失败的药材。\n"
        if reason:
            msg += f"说明：{reason}\n"
        msg += f"本页重试数量：{len(retry_items)}；买完本页后再刷新下一页失败药材。"
        await send_cb(msg)
        await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)

    async def _send_fresh_purchase_command(self, job: AutoAlchemyJob, send_cb, item: Dict[str, Any]) -> None:
        name = self.normalize_name(item.get("name", ""))
        cmd = self._normalize_buy_command(str(item.get("buy_command") or ""))
        if not name or not cmd:
            return
        job.phase = "BATCH_BUY_WAIT"
        job.batch_current_item = dict(item)
        job.batch_buy_sent += 1
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} {cmd}")

    async def _retry_current_purchase_after_busy(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        item = dict(job.batch_current_item or (job.batch_buy_queue[job.batch_buy_index] if 0 <= job.batch_buy_index < len(job.batch_buy_queue) else {}))
        name = self.normalize_name(item.get("name", "")) or "未知药材"
        await send_cb(f"⏳ 小小繁忙，3秒后重试：{name}")
        await asyncio.sleep(3.0)
        if self.jobs.get(key) is not job or job.phase == "PAUSED":
            return
        if job.phase not in {"BATCH_BUY_WAIT", "BATCH_BUY_SENT"}:
            return
        current = dict(job.batch_current_item or item)
        current_name = self.normalize_name(current.get("name", ""))
        cmd = self._normalize_buy_command(str(current.get("buy_command") or job.buy_commands.get(current_name, "") or ""))
        if not cmd:
            await self._skip_current_purchase_and_continue(key, job, send_cb, f"小小繁忙后重试失败：{name} 缺少有效购买指令")
            return
        current["buy_command"] = cmd
        job.batch_current_item = current
        job.batch_buy_sent += 1
        job.batch_busy_retry_done = True
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} {cmd}")


    async def _skip_current_purchase_and_continue(self, key: str, job: AutoAlchemyJob, send_cb, reason: str) -> None:
        item = dict(job.batch_current_item or (job.batch_buy_queue[job.batch_buy_index] if 0 <= job.batch_buy_index < len(job.batch_buy_queue) else {}))
        name = self.normalize_name(item.get("name", ""))
        if name:
            if job.retry_after_batch_active:
                self._mark_all_remaining_failed(job, name)
            else:
                job.failed_counts[name] = int(job.failed_counts.get(name, 0)) + 1
        job.batch_fail_count += 1
        job.batch_current_item = {}
        job.batch_buy_index += 1
        job.updated_at = time.time()
        await send_cb(f"⚠️ 已跳过本次购买：{name or '未知药材'}。\n原因：{reason}\n后续会继续购买其它药材；最终缺材料的丹方不会炼制。")
        await self._send_next_batch_purchase_or_start_alchemy(key, job, send_cb)

    def _handle_price_change_for_job(self, job: AutoAlchemyJob, target: str, old_price: float, new_price: float) -> Tuple[bool, str]:
        target = self.normalize_name(target)
        if job.mode == "backpack":
            for cand in job.batch_selected or []:
                if not cand.get("abandoned"):
                    self._refresh_candidate_prices(cand, job.prices, job.buy_commands, job.pages_by_name)
            job.batch_purchase_plan = self._build_purchase_plan_for_job(job)
            for q in job.batch_buy_queue[max(0, int(job.batch_buy_index or 0)):]:
                name = self.normalize_name(q.get("name", ""))
                if name in job.prices:
                    q["unit_price"] = float(job.prices.get(name) or q.get("unit_price") or 0)
                if name in job.buy_commands:
                    q["buy_command"] = job.buy_commands.get(name, "")
                if name in job.pages_by_name:
                    q["page"] = int(job.pages_by_name.get(name) or q.get("page") or 0)
            self._refresh_batch_buy_expected_preserving_queue(job)
            return True, f"🔄 {target} 价格由 {self._fmt_num(old_price)}万 变为 {self._fmt_num(new_price)}万；背包炼丹保留当前购买队列，仅刷新后续购买指令。"

        active: List[Dict[str, Any]] = []
        for cand in job.batch_selected or []:
            if cand.get("abandoned"):
                continue
            uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
            if uid:
                cand["formula_uid"] = uid
            self._refresh_candidate_prices(cand, job.prices, job.buy_commands, job.pages_by_name)
            active.append(cand)

        can_reuse = bool(active) and all(self._candidate_reusable_after_price_refresh(job, cand) for cand in active)
        if can_reuse:
            job.batch_purchase_plan = self._build_purchase_plan_for_job(job)
            for q in job.batch_buy_queue[max(0, int(job.batch_buy_index or 0)):]:
                name = self.normalize_name(q.get("name", ""))
                if name in job.prices:
                    q["unit_price"] = float(job.prices.get(name) or q.get("unit_price") or 0)
                if name in job.buy_commands:
                    q["buy_command"] = job.buy_commands.get(name, "")
                if name in job.pages_by_name:
                    q["page"] = int(job.pages_by_name.get(name) or q.get("page") or 0)
            self._refresh_batch_buy_expected_preserving_queue(job)
            active_count = len([c for c in job.batch_selected if not c.get("abandoned")])
            return True, f"🔄 {target} 价格由 {self._fmt_num(old_price)}万 变为 {self._fmt_num(new_price)}万；当前丹方仍在基准波动范围内且利润非负，继续沿用。当前购买队列保持不变，剩余可炼制队列：{active_count}炉。"

        try:
            candidates, _, _ = self._compute_candidates(
                job.prices,
                job.buy_commands,
                job.pages_by_name,
                yield_count=job.yield_count,
                pill_filter=job.target_pill if job.mode == "target" else "",
                allow_unknown_sale=self.target_unknown_price_execute if job.mode == "target" else False,
            )
        except Exception as e:
            self._refresh_batch_buy_expected_preserving_queue(job)
            target_label = job.target_pill if job.mode == "target" else "自动炼丹"
            return True, f"⚠️ {target} 价格变化后重新计算 {target_label} 失败：{e}。当前购买队列保持不变，按原顺序继续。"

        replaced_notes: List[str] = []
        abandoned_notes: List[str] = []
        new_selected: List[Dict[str, Any]] = []

        if job.mode == "target" and not self.target_mode_plan_lock:
            allow_unknown = self.target_unknown_price_execute and job.target_pill not in FIXED_PILL_SALE_PRICE
            best = self._select_best_for_target(candidates, min_profit=job.min_profit, allow_unknown_sale=allow_unknown)
            if best:
                new_selected = [self._clone_candidate(best) for _ in range(max(1, int(job.target_rounds or 1)))]
                uid = self._candidate_uid(best)
                for idx, cand in enumerate(new_selected, 1):
                    cand["formula_uid"] = uid
                    cand["formula_repeat_index"] = idx
                    self._anchor_candidate_price_baseline(job, cand)
                old_names = sorted({c["recipe"].pill for c in active if c.get("recipe")})
                new_recipe: Recipe = best["recipe"]
                replaced_notes.append(f"{ '、'.join(old_names) if old_names else job.target_pill } → {new_recipe.pill}")
            else:
                for cand in active:
                    cand["abandoned"] = True
                    recipe: Recipe = cand["recipe"]
                    job.abandoned_pills[recipe.pill] = "价格超出基准范围或利润为负，且当前无可切换丹方"
                    abandoned_notes.append(recipe.pill)
                new_selected = list(job.batch_selected or [])
        else:
            if self.batch_repeat_until_threshold:
                selected_base, reserve = self._select_batch_primary_and_reserve(candidates, min_profit=job.min_profit)
                job.batch_reserve_candidates = reserve
                new_selected = selected_base
            else:
                base_selected = self._select_profitable_best_by_pill(candidates, yield_count=job.yield_count, min_profit=job.min_profit)
                new_selected = []
                for base in base_selected[: self.max_batch_formula_count or len(base_selected)]:
                    uid = self._candidate_uid(base)
                    base = self._clone_candidate(base)
                    base["formula_uid"] = uid
                    new_selected.extend(self._expand_base_candidate_for_batch(base))
                job.batch_reserve_candidates = []
            if new_selected:
                old_names = sorted({c["recipe"].pill for c in active if c.get("recipe")})
                new_names = sorted({c["recipe"].pill for c in new_selected if c.get("recipe")})
                if old_names != new_names:
                    replaced_notes.append(f"{ '、'.join(old_names) if old_names else '原丹方' } → { '、'.join(new_names) }")
                for cand in new_selected:
                    uid = str(cand.get("formula_uid") or self._candidate_uid(cand))
                    cand["formula_uid"] = uid
                    self._anchor_candidate_price_baseline(job, cand)
            else:
                for cand in active:
                    cand["abandoned"] = True
                    recipe: Recipe = cand["recipe"]
                    job.abandoned_pills[recipe.pill] = "价格超出基准范围或利润为负，且当前无可切换丹方"
                    abandoned_notes.append(recipe.pill)
                new_selected = list(job.batch_selected or [])

        frozen_count = 0
        if new_selected:
            new_selected, frozen_count = self._merge_existing_queue_candidates_with_new(active, new_selected)

        job.batch_selected = new_selected
        self._prune_formula_price_baselines(job)
        job.batch_purchase_plan = self._build_purchase_plan_for_job(job)
        job.batch_formula_texts = [self._format_recipe_send_command(c["recipe"], c["materials"]) for c in job.batch_selected if not c.get("abandoned")]
        appended = self._append_new_purchase_items_preserving_queue(job, self._build_incremental_purchase_queue(job, job.batch_purchase_plan))
        self._refresh_batch_buy_expected_preserving_queue(job)
        job.batch_current_item = job.batch_current_item or {}

        active_count = len([c for c in job.batch_selected if not c.get("abandoned")])
        note = f"🔄 {target} 价格由 {self._fmt_num(old_price)}万 变为 {self._fmt_num(new_price)}万；已触发丹方重新排序。\n"
        if replaced_notes:
            note += "已切换丹方：" + "；".join(replaced_notes) + "。\n"
        if abandoned_notes:
            note += "以下丹方暂不继续新增采购：" + "、".join(sorted(set(abandoned_notes))) + "。\n"
        if frozen_count:
            note += f"已保留旧队列对应丹方 {frozen_count} 炉用于消化已生成采购；这些旧丹方不再新增采购。\n"
        note += f"当前已生成购买队列保持原顺序；新增采购追加 {appended} 项；剩余可炼制队列：{active_count}炉。"
        return active_count > 0 or bool(job.purchased_counts) or bool(job.batch_buy_queue), note

    def _refresh_candidate_prices(self, cand: Dict[str, Any], prices: Dict[str, float], buy_commands: Dict[str, str], pages_by_name: Dict[str, int]) -> None:
        cost = 0.0
        for m in cand.get("materials", []):
            name = self.normalize_name(m.get("name", ""))
            if name in prices:
                m["unit_price"] = float(prices[name])
            m["buy_command"] = buy_commands.get(name, m.get("buy_command", ""))
            m["page"] = int(pages_by_name.get(name, m.get("page", 0)) or 0)
            cost += float(m.get("qty") or 1) * float(m.get("unit_price") or 0)
        sale = float(cand.get("sale") or 0)
        yield_count = int(cand.get("yield_count") or self.default_yield_count)
        cand["cost"] = cost
        cand["score_profit"] = sale * yield_count - cost
        cand["profits"] = [{"count": n, "revenue": sale * n, "profit": sale * n - cost} for n in range(1, 8)]
        self._attach_command_efficiency(cand, use_purchase_qty=bool(cand.get("backpack_used_total") is not None))

    async def _start_alchemy_sequence(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        job.alchemy_queue = self._build_alchemy_queue_from_purchased(job)
        job.alchemy_index = 0
        job.alchemy_sent = 0
        job.alchemy_success = 0
        job.alchemy_results = []
        job.alchemy_started_at = time.time()
        if not job.alchemy_queue:
            self.jobs.pop(key, None)
            await send_cb(self._format_full_done_report(job, no_alchemy=True))
            return
        await send_cb("✅ 药材购买阶段结束，开始逐个发送炼丹配方。\n说明：炼丹指令末尾不附带丹药名；收到“恭喜道友成功炼成丹药”后才会发送下一条。")
        await self._send_next_alchemy_command(key, job, send_cb)

    def _build_alchemy_queue_from_purchased(self, job: AutoAlchemyJob) -> List[Dict[str, Any]]:
        purchased_available = {
            self.normalize_name(k): int(v or 0)
            for k, v in (job.purchased_counts or {}).items()
            if self.normalize_name(k) and int(v or 0) > 0
        }
        backpack_available: Dict[str, int] = {}
        if job.backpack_counts:
            for k, v in (job.backpack_counts or {}).items():
                n = self.normalize_name(k)
                if n and int(v or 0) > 0:
                    backpack_available[n] = int(backpack_available.get(n, 0)) + int(v or 0)

        queue: List[Dict[str, Any]] = []
        job.skipped_alchemy = []
        for cand in job.batch_selected or []:
            recipe: Recipe = cand["recipe"]
            if cand.get("abandoned"):
                job.skipped_alchemy.append(f"{recipe.pill}：已弃炼（{job.abandoned_pills.get(recipe.pill, '利润低于阈值')}）")
                continue
            needed: Dict[str, int] = {}
            for m in cand.get("materials", []):
                name = self.normalize_name(m.get("name", ""))
                if name:
                    needed[name] = needed.get(name, 0) + int(m.get("qty") or 1)

            missing = {}
            for n, q in needed.items():
                have = int(backpack_available.get(n, 0)) + int(purchased_available.get(n, 0))
                if have < q:
                    missing[n] = q - have
            if missing:
                miss_text = "、".join(f"{n}×{q}" for n, q in missing.items())
                job.skipped_alchemy.append(f"{recipe.pill}：缺少 {miss_text}")
                continue

            for n, q in needed.items():
                need_left = int(q)
                use_bag = min(int(backpack_available.get(n, 0)), need_left)
                if use_bag > 0:
                    backpack_available[n] = int(backpack_available.get(n, 0)) - use_bag
                    need_left -= use_bag
                if need_left > 0:
                    purchased_available[n] = int(purchased_available.get(n, 0)) - need_left
            cmd = self._format_recipe_send_command(recipe, cand.get("materials", []))
            if cmd:
                queue.append({"pill": recipe.pill, "command": cmd, "profit": float(cand.get("score_profit", 0)), "cost": float(cand.get("cost", 0))})

        job.overbuy_counts = {n: int(c) for n, c in purchased_available.items() if int(c or 0) > 0}
        job.overbuy_value = sum(float(job.prices.get(n, 0) or 0) * int(c) for n, c in job.overbuy_counts.items())
        return queue

    async def _send_next_alchemy_command(self, key: str, job: AutoAlchemyJob, send_cb) -> None:
        if job.alchemy_index >= len(job.alchemy_queue):
            self.jobs.pop(key, None)
            await send_cb(self._format_full_done_report(job))
            return
        current = job.alchemy_queue[job.alchemy_index]
        cmd = str(current.get("command") or "").strip()
        if not cmd:
            await self._pause_job(key, job, send_cb, "炼丹队列中出现空配方。")
            return
        job.phase = "ALCHEMY_WAIT"
        job.alchemy_sent += 1
        job.last_command_ts = job.updated_at = time.time()
        await send_cb(f"@{self.official_qq} {cmd}")

    async def _pause_job(self, key: str, job: AutoAlchemyJob, send_cb, reason: str) -> None:
        if job.phase != "PAUSED":
            job.phase_before_pause = job.phase
        job.phase = "PAUSED"
        job.paused_reason = reason
        job.paused_at = job.updated_at = time.time()
        self.jobs[key] = job
        await send_cb(f"⚠️【自动炼丹已暂停】\n原因：{reason}\n当前不会继续发送购买或炼丹指令。\n请处理后发送「继续自动炼丹」恢复，或发送「关闭自动炼丹」终止。")

    def _resolve_recipe(self, recipe: Recipe, prices: Dict[str, float], buy_commands: Optional[Dict[str, str]] = None, pages_by_name: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        buy_commands = buy_commands or {}
        pages_by_name = pages_by_name or {}
        materials: List[Dict[str, Any]] = []
        for req in recipe.materials:
            if req.wildcard_prop:
                picked = self._pick_cheapest_guide(req.wildcard_prop, prices)
                if not picked:
                    return {"wildcard_missing": True, "materials": []}
                name, unit_price, prop_value = picked
                materials.append({"role": req.role, "name": name, "qty": int(req.qty), "unit_price": float(unit_price), "source": f"任意{req.wildcard_prop}", "property": prop_value, "buy_command": buy_commands.get(name, ""), "page": int(pages_by_name.get(name, 0) or 0)})
                continue
            price = prices.get(req.name)
            if price is None or float(price) <= 0:
                return None
            prop = self.herb_props.get(req.name, {})
            role_key = "main" if req.role == "主药" else "assist" if req.role == "辅药" else "guide"
            materials.append({"role": req.role, "name": req.name, "qty": int(req.qty), "unit_price": float(price), "source": "固定药材", "property": prop.get(role_key, ""), "buy_command": buy_commands.get(req.name, ""), "page": int(pages_by_name.get(req.name, 0) or 0)})
        return {"materials": materials}

    def _pick_cheapest_guide(self, prop_prefix: str, prices: Dict[str, float]) -> Optional[Tuple[str, float, str]]:
        candidates: List[Tuple[str, float, str]] = []
        for name, p in prices.items():
            prop = self.herb_props.get(name, {}).get("guide", "")
            if prop.startswith(prop_prefix) and float(p) > 0:
                candidates.append((name, float(p), prop))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[1], x[0]))
        return candidates[0]

    def _read_page_index(self) -> Dict[str, int]:
        if not self.page_index_cache_enabled or not self.page_index_path or not os.path.exists(self.page_index_path):
            return {}
        try:
            with open(self.page_index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {self.normalize_name(k): int(v) for k, v in data.items() if str(v).isdigit() or isinstance(v, int)}
        except Exception:
            return {}
        return {}

    def _write_page_index(self, pages: Dict[str, int]) -> None:
        if not self.page_index_cache_enabled or not self.page_index_path:
            return
        data = self._read_page_index()
        for k, v in (pages or {}).items():
            name = self.normalize_name(k)
            try:
                page = int(v)
            except Exception:
                continue
            if name and 1 <= page <= self.max_page:
                data[name] = page
        try:
            os.makedirs(os.path.dirname(self.page_index_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="alchemy_page_index_", suffix=".json", dir=os.path.dirname(self.page_index_path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.page_index_path)
        except Exception as e:
            self._warn(f"写入自动炼丹页码索引失败：{e}")

    def _page_for_name(self, name: str) -> int:
        idx = self._read_page_index()
        return int(idx.get(self.normalize_name(name), 0) or 0)

    def _target_scan_pages(self, pill: str) -> List[int]:
        pill = self.normalize_name(pill)
        idx = self._read_page_index()
        if not idx:
            return []
        pages: set[int] = set()
        missing_required = False
        recipes = [r for r in self._load_recipes() if r.pill == pill]
        if not recipes:
            return []
        for recipe in recipes:
            for req in recipe.materials:
                if req.wildcard_prop:
                    prop_names = [n for n, prop in self.herb_props.items() if str(prop.get("guide", "")).startswith(req.wildcard_prop)]
                    found_any = False
                    for n in prop_names:
                        p = int(idx.get(n, 0) or 0)
                        if p:
                            pages.add(p)
                            found_any = True
                    if not found_any:
                        missing_required = True
                else:
                    p = int(idx.get(req.name, 0) or 0)
                    if p:
                        pages.add(p)
                    else:
                        missing_required = True
        if missing_required:
            return []
        return sorted(pages)

    def _format_recipe_send_command(self, recipe: Recipe, materials: List[Dict[str, Any]]) -> str:
        by_role = {str(x.get("role")): x for x in materials}
        main = by_role.get("主药", {})
        guide = by_role.get("药引", {})
        assist = by_role.get("辅药", {})
        main_name = self.normalize_name(main.get("name", ""))
        guide_name = self.normalize_name(guide.get("name", ""))
        assist_name = self.normalize_name(assist.get("name", ""))
        main_qty = int(main.get("qty") or 1)
        guide_qty = int(guide.get("qty") or 1)
        assist_qty = int(assist.get("qty") or 1)
        furnace = str(recipe.furnace or "寒铁铸心炉").strip()
        return f"配方主药{main_name}{main_qty}药引{guide_name}{guide_qty}辅药{assist_name}{assist_qty}丹炉{furnace}"


    def _format_no_profitable_report(self, job: AutoAlchemyJob, candidate_count: int, price_count: int, skipped_count: int) -> str:
        return f"❌ 未找到不亏本丹方。成丹{job.yield_count}颗。"

    def _format_batch_buy_plan_report(self, job: AutoAlchemyJob, candidate_count: int, skipped_count: int) -> str:
        selected = [c for c in (job.batch_selected or []) if not c.get("abandoned")]
        plan = job.batch_purchase_plan or []
        total_purchase_count = sum(int(x.get("qty") or 0) for x in plan)
        total_profit = sum(float(c.get("score_profit", 0)) for c in selected if not c.get("unknown_sale"))
        title = "指定丹药" if job.mode == "target" else "背包炼丹" if job.mode == "backpack" else "自动炼丹"
        lines: List[str] = []
        lines.append(f"💰【{title}利润丹方】")
        lines.append(f"成丹：{job.yield_count}颗｜丹方：{len(selected)}条｜购买：{total_purchase_count}次｜预计利润：{self._fmt_num(total_profit)}万")
        for idx, cand in enumerate(selected, 1):
            r: Recipe = cand["recipe"]
            if cand.get("unknown_sale"):
                lines.append(f"{idx}. {r.pill}｜成本 {self._fmt_num(cand.get('cost', 0))}万")
            else:
                lines.append(f"{idx}. {r.pill}｜利润 {self._fmt_num(cand.get('score_profit', 0))}万｜成本 {self._fmt_num(cand.get('cost', 0))}万")
            lines.append(f"   {self._format_recipe_send_command(r, cand.get('materials', []))}")
        return "\n".join(lines)

    def _format_full_done_report(self, job: AutoAlchemyJob, no_alchemy: bool = False) -> str:
        lines: List[str] = []
        title = "✅【自动炼丹结束】" if not no_alchemy else "✅【自动炼丹购买结束】"
        lines.append(title)
        lines.append(f"购买成功：{job.batch_success_count}/{job.batch_buy_expected}｜炼丹成功：{job.alchemy_success}/{len(job.alchemy_queue)}")
        active = [c for c in (job.batch_selected or []) if not c.get("abandoned")]
        total_profit = sum(float(c.get("score_profit", 0)) for c in active if not c.get("unknown_sale"))
        lines.append(f"预计利润：{self._fmt_num(total_profit)}万")
        if job.alchemy_queue:
            lines.append("\n🧾【利润丹方】")
            for idx, item in enumerate(job.alchemy_queue[: self.max_profitable_report_count], 1):
                profit = self._fmt_num(item.get("profit", 0))
                lines.append(f"{idx}. {item.get('pill', '')}｜利润 {profit}万｜{item.get('command', '')}")
        if job.skipped_alchemy:
            lines.append(f"\n⚠️ 跳过未炼：{len(job.skipped_alchemy)}条")
        if job.overbuy_counts:
            detail_items = []
            for name, count in sorted(job.overbuy_counts.items(), key=lambda x: (-float(job.prices.get(x[0], 0) or 0) * int(x[1]), x[0])):
                unit_price = float(job.prices.get(name, 0) or 0)
                subtotal = unit_price * int(count)
                if subtotal > 0:
                    detail_items.append(f"{name}×{int(count)}≈{self._fmt_num(subtotal)}万")
                else:
                    detail_items.append(f"{name}×{int(count)}")
            lines.append("\n💸【失败导致多买药材】")
            lines.append(f"估算总价值：{self._fmt_num(job.overbuy_value)}万（按本轮实时坊市价格计算）")
            if detail_items:
                lines.append("明细：" + "、".join(detail_items[:12]))
                if len(detail_items) > 12:
                    lines.append(f"其余 {len(detail_items) - 12} 种略。")
        return "\n".join(lines)

    def _read_snapshot(self) -> Dict[str, Any]:
        if not self.snapshot_path or not os.path.exists(self.snapshot_path):
            return {}
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            updated_at = int(data.get("updated_at") or 0)
            if self.batch_snapshot_max_age_sec > 0 and updated_at > 0:
                if int(time.time()) - updated_at > self.batch_snapshot_max_age_sec:
                    return {}
            prices_raw = data.get("prices") or {}
            pages_raw = data.get("pages_by_name") or {}
            commands_raw = data.get("buy_commands") or {}
            prices: Dict[str, float] = {}
            pages: Dict[str, int] = {}
            commands: Dict[str, str] = {}
            if isinstance(prices_raw, dict):
                for k, v in prices_raw.items():
                    name = self.normalize_name(k)
                    try:
                        price = float(v)
                    except Exception:
                        continue
                    if name and price > 0:
                        prices[name] = price
            if isinstance(pages_raw, dict):
                for k, v in pages_raw.items():
                    name = self.normalize_name(k)
                    try:
                        page = int(v)
                    except Exception:
                        continue
                    if name and 1 <= page <= self.max_page:
                        pages[name] = page
            if isinstance(commands_raw, dict):
                for k, v in commands_raw.items():
                    name = self.normalize_name(k)
                    cmd = self._normalize_buy_command(str(v or ""))
                    if name and cmd:
                        commands[name] = cmd
            return {"updated_at": updated_at, "prices": prices, "pages_by_name": pages, "buy_commands": commands}
        except Exception as e:
            self._warn(f"读取自动炼丹快照失败：{e}")
            return {}

    def _batch_pages_from_cached_snapshot(self, prices: Dict[str, float], buy_commands: Dict[str, str], pages_by_name: Dict[str, int]) -> List[int]:
        try:
            candidates, _, _ = self._compute_candidates(prices, buy_commands, pages_by_name, yield_count=self.default_yield_count)
        except Exception:
            return []
        if self.batch_repeat_until_threshold:
            selected = self._select_profitable_all_candidates(candidates, min_profit=self.min_profit_6pill)
        else:
            selected = self._select_profitable_best_by_pill(candidates, yield_count=self.default_yield_count, min_profit=self.min_profit_6pill)
        pages: set[int] = set()
        for cand in selected:
            for m in cand.get("materials", []):
                try:
                    page = int(m.get("page") or pages_by_name.get(self.normalize_name(m.get("name", "")), 0) or 0)
                except Exception:
                    page = 0
                if 1 <= page <= self.max_page:
                    pages.add(page)
        return sorted(pages)

    def _write_snapshot(self, prices: Dict[str, float], report: str, buy_commands: Optional[Dict[str, str]] = None, pages_by_name: Optional[Dict[str, int]] = None) -> None:
        if not self.snapshot_path:
            return
        data = {
            "updated_at": int(time.time()),
            "prices": prices,
            "buy_commands": buy_commands or {},
            "pages_by_name": pages_by_name or {},
            "report": report,
            "note": "自动炼丹实时快照，仅供排查；成本计算以本轮实时采集为准。",
        }
        try:
            os.makedirs(os.path.dirname(self.snapshot_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="auto_alchemy_", suffix=".json", dir=os.path.dirname(self.snapshot_path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.snapshot_path)
        except Exception as e:
            self._warn(f"写入自动炼丹快照失败：{e}")

    @staticmethod
    def _fmt_num(v) -> str:
        try:
            x = float(v)
        except Exception:
            return str(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return f"{x:.2f}".rstrip("0").rstrip(".")
