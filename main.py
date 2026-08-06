# 模块：主入口与指令分发
import asyncio
import html
import json
import os
import re
import shutil
import sys
import time
from typing import Optional, List, Any, Dict

from astrbot.api.star import Star, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import logger

try:
    from astrbot.api.web import request, json_response, error_response
    _WEB_API_AVAILABLE = True
except Exception:
    _WEB_API_AVAILABLE = False
    request = None
    json_response = None
    error_response = None

_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

try:
    from .storage import JsonStore, make_key
    from .bounty import BountyController
    from .secret import SecretController
    from .routine import RoutineController
    from .sect import SectController
    from .cultivate import CultivateController, MODE_CULTIVATE, MODE_SECLUSION, MODE_SECT_SECLUSION
    from .time_utils import fmt_ts_compact
    from .market_price import MarketPriceProvider
    from .inventory_ops import InventoryOpsController
    from .auto_alchemy_optimizer import AutoAlchemyOptimizer
    from .send_stats import DailySendStats
    from .linjie import LinjieUpgradeController, format_money as format_linjie_money
    from .endless import EndlessTowerController
    from .captcha_guard import CaptchaGuard, is_click_action_accepted
    from .protocol_compat import event_field, event_fingerprint, normalize_qq_id as _normalize_qq_id
except ImportError:
    from storage import JsonStore, make_key
    from bounty import BountyController
    from secret import SecretController
    from routine import RoutineController
    from sect import SectController
    from cultivate import CultivateController, MODE_CULTIVATE, MODE_SECLUSION, MODE_SECT_SECLUSION
    from time_utils import fmt_ts_compact
    from market_price import MarketPriceProvider
    from inventory_ops import InventoryOpsController
    from auto_alchemy_optimizer import AutoAlchemyOptimizer
    from send_stats import DailySendStats
    from linjie import LinjieUpgradeController, format_money as format_linjie_money
    from endless import EndlessTowerController
    from captcha_guard import CaptchaGuard, is_click_action_accepted
    from protocol_compat import event_field, event_fingerprint, normalize_qq_id as _normalize_qq_id

PLUGIN_NAME = "astrbot_plugin_xiao_xiuxian_auto"
OFFICIAL_BOT_QQ_DEFAULT = "3889001741"
BIND_KEY = "__bound__"
SEND_BLOCKED_KEY = "__send_blocked__"

DEFAULT_MARKET_PRICE_URL = "http://81.71.44.7:8808/api/prices/latest"

LINJIE_UPGRADE_CONFIG_DEFAULTS = {
    "_comment_linjie_upgrade": "灵界升级模块；启动后先查询当前灵界数据，之后按成功回执更新缓存并按 ROI 升级。",
    "linjie_upgrade": {
        "_comment_enabled": "灵界升级总开关；false 时相关指令仅提示关闭。",
        "enabled": True,
        "_comment_reserve_lingkuang": "最低保留灵矿石数量；0 表示不保留。",
        "reserve_lingkuang": 0,
        "_comment_success_delay_sec": "收到升级成功回执后，间隔多少秒继续判断下一次升级。",
        "success_delay_sec": 0.5,
        "_comment_query_timeout_sec": "查询灵界页面后等待回执的超时时间。",
        "query_timeout_sec": 20.0,
        "_comment_action_timeout_sec": "发出升级指令后等待成功/失败回执的超时时间。",
        "action_timeout_sec": 25.0,
        "_comment_max_failures": "连续失败多少次后停止。",
        "max_failures": 3,
        "_comment_cache_ttl_sec": "灵界缓存有效期；有效期内灵界规划优先使用缓存，避免频繁查询。",
        "cache_ttl_sec": 21600,
        "_comment_confirm_after_success": "升级成功后是否只查询对应页面做轻量确认；默认 false，优先使用成功回执更新缓存。",
        "confirm_after_success": False,
        "_comment_roi_formula_source": "ROI收益计算来源；excel_formula 表示按本地 Excel 复刻公式推算，game_display 表示按游戏回执显示值推算。",
        "roi_formula_source": "excel_formula",
        "_comment_default_abundance": "是否默认按丰饶印记开启计算；游戏回执暂未直接给出该状态。",
        "default_abundance": True,
        "_comment_include_skill_training": "是否把「灵界技艺修行」纳入 ROI 候选。",
        "include_skill_training": True,
        "_comment_include_skill_breakthrough": "是否把「灵界技艺突破」纳入候选；当前突破成本/收益未完全确认，默认关闭。",
        "include_skill_breakthrough": False,
        "_comment_max_sim_steps": "多步滚动 ROI 模拟最大步数；默认15步，用于「灵界规划序列」和「灵界规划详情」。",
        "max_sim_steps": 15,
    },
}

ENDLESS_TOWER_CONFIG_DEFAULTS = {
    "_comment_endless_tower": "无尽妖塔模块；支持限定挑战次数、真元检测和真元不足时宗门闭关恢复。",
    "endless_tower": {
        "_comment_enabled": "无尽妖塔总开关；false 时相关指令仅提示关闭。",
        "enabled": True,
        "_comment_mp_check_enabled": "默认是否开启真元检测；可通过指令 开启/关闭真元检测 调整。",
        "mp_check_enabled": True,
        "_comment_mp_threshold": "真元检测阈值，范围 0-9999；低于该百分比时先宗门闭关恢复。",
        "mp_threshold": 600,
        "_comment_rest_duration_sec": "真元不足时宗门闭关持续秒数；7分钟10秒为 430。",
        "rest_duration_sec": 430,
        "_comment_action_delay_sec": "每次成功或状态切换后，间隔多少秒继续下一步。",
        "action_delay_sec": 1.0,
        "_comment_status_timeout_sec": "发送 我的状态 后等待回执的超时时间。",
        "status_timeout_sec": 20.0,
        "_comment_challenge_timeout_sec": "发送 挑战无尽妖塔 后等待回执的超时时间。",
        "challenge_timeout_sec": 60.0,
        "_comment_max_failures": "连续超时或解析失败多少次后停止。",
        "max_failures": 3,
    },
}

__plugin_name__ = "astrbot_plugin_xiao_xiuxian_auto"
__plugin_version__ = "1.0.0"
__plugin_author__ = "cclite123"
__plugin_desc__ = "小小修仙任务挂机插件 —— 悬赏、秘境、宗门、炼丹、签到、挖矿、灵田、闭关修炼，一键解放双手。"


ACTIVITY_MODULE_BOUNTY = "bounty"
ACTIVITY_MODULE_SECRET = "secret"
ACTIVITY_MODULE_SECT = "sect"
ACTIVITY_PRIORITY = {
    ACTIVITY_MODULE_BOUNTY: 10,
    ACTIVITY_MODULE_SECRET: 20,
    ACTIVITY_MODULE_SECT: 30,
}
INACTIVE_PHASES = {"IDLE", "SLEEPING"}

PAGE_HIDDEN_TOP_LEVEL = {
    "official_bot_qq",
    "test_mode",
    "multi_account",
    "coordinator",
    "send_fail_policy",
    "market_price",
}
PAGE_HIDDEN_NESTED = {
    "inventory_ops": {"market_command_price_format"},
}


def extract_pure_text(event) -> str:
    text = ""
    raw = event.get("raw_message") if isinstance(event, dict) else getattr(event, "raw_message", "")
    if raw and isinstance(raw, str):
        text = raw
    if not text:
        msg = event.get("message") if isinstance(event, dict) else getattr(event, "message", None)
        if isinstance(msg, list):
            parts = []
            for seg in msg:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            text = "".join(parts)
        elif isinstance(msg, str):
            text = msg
    if not text:
        astr_text = getattr(event, "message_str", "")
        if astr_text:
            text = str(astr_text)
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\[CQ:[^\]]+\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[At:\d+\]", "", text)
    text = re.sub(r"<@!?\d+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _normalize_raw_candidate(value) -> str:
    try:
        text = value.decode("utf-8", errors="ignore") if isinstance(value, (bytes, bytearray)) else str(value)
    except Exception:
        return ""
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("\\/", "/")
    text = text.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    # 有些适配器会把 markdown 放在 JSON 字符串里，表现为 \uXXXX。只在存在转义时尝试还原。
    if "\\u" in text or "\\x" in text:
        try:
            text = bytes(text, "utf-8").decode("unicode_escape")
        except Exception:
            pass
    return text


def _iter_event_strings(obj, *, depth: int = 0, seen: set | None = None):
    """递归从 AstrBot/OneBot 事件对象中寻找仍保留 markdown inlinecmd 的原始字符串。"""
    if obj is None or depth > 5:
        return
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    if isinstance(obj, (str, bytes, bytearray)):
        text = _normalize_raw_candidate(obj)
        if text:
            yield text
        return

    if isinstance(obj, dict):
        # raw/message/data 这些字段优先，但仍递归全量 value，适配不同 aiocqhttp 事件封装。
        priority_keys = (
            "raw_message", "message", "message_str", "message_chain", "message_obj",
            "plain_result", "raw_event", "event", "data", "content", "markdown", "text",
        )
        for k in priority_keys:
            if k in obj:
                yield from _iter_event_strings(obj.get(k), depth=depth + 1, seen=seen)
        for v in obj.values():
            yield from _iter_event_strings(v, depth=depth + 1, seen=seen)
        return

    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            yield from _iter_event_strings(item, depth=depth + 1, seen=seen)
        return

    # AstrMessageEvent 往往是对象包装；优先读取常见属性，再浅扫 __dict__。
    attrs = (
        "raw_message", "message", "message_str", "message_chain", "message_obj",
        "plain_result", "raw_event", "event", "data", "content", "markdown", "text",
    )
    for attr in attrs:
        try:
            val = getattr(obj, attr, None)
        except Exception:
            val = None
        if val is not None and val is not obj:
            yield from _iter_event_strings(val, depth=depth + 1, seen=seen)

    if depth <= 2:
        try:
            d = getattr(obj, "__dict__", None)
        except Exception:
            d = None
        if isinstance(d, dict):
            for v in d.values():
                yield from _iter_event_strings(v, depth=depth + 1, seen=seen)


def extract_raw_text(event) -> str:
    """尽量保留 QQ markdown / mqqapi inlinecmd 链接；炼丹依赖这里解析真实购买 UUID。"""
    candidates = []
    fallback = []
    for text in _iter_event_strings(event):
        if not text:
            continue
        if "mqqapi://aio/inlinecmd" in text or "command=" in text:
            candidates.append(text)
        else:
            fallback.append(text)
    if candidates:
        # 取最长的原始 markdown，避免只拿到单个片段或纯文本备份。
        text = max(candidates, key=len)
    elif fallback:
        text = fallback[0]
    else:
        return ""
    text = re.sub(r"\[CQ:[^\]]+\]", "", text)
    text = re.sub(r"\[At:\d+\]", "", text)
    text = re.sub(r"<@!?\d+>", "", text)
    return text.strip()


def _get_platform_list(context) -> List[Any]:
    pm = getattr(context, "platform_manager", None)
    if pm is None: return []
    for attr in ("get_insts", "platform_insts", "platforms"):
        try:
            val = getattr(pm, attr, None)
            res = val() if callable(val) else val
            if res: return list(res)
        except Exception: continue
    return []


def _dig_bot(plat) -> Any:
    for name in ("bot", "client", "cqhttp", "ws", "_bot", "_client"):
        bot = getattr(plat, name, None)
        if bot is not None: return bot
    for getter in ("get_client", "get_bot", "get_platform_client"):
        fn = getattr(plat, getter, None)
        if callable(fn):
            try:
                bot = fn()
                if bot: return bot
            except Exception: pass
    adapter = getattr(plat, "adapter", None)
    if adapter is not None:
        for name in ("bot", "client", "cqhttp", "_bot"):
            bot = getattr(adapter, name, None)
            if bot is not None: return bot
    return None


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result.get(key, {}), value)
        else:
            result[key] = value
    return result


def _filter_account_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in (schema or {}).items():
        if key in PAGE_HIDDEN_TOP_LEVEL or not isinstance(value, dict):
            continue
        item = dict(value)
        nested_hidden = PAGE_HIDDEN_NESTED.get(key, set())
        if nested_hidden and isinstance(item.get("items"), dict):
            item["items"] = {
                child_key: child_value
                for child_key, child_value in item["items"].items()
                if child_key not in nested_hidden
            }
        result[key] = item
    return result


def _config_for_schema(config: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, item in (schema or {}).items():
        if key not in config:
            continue
        value = config[key]
        if isinstance(value, dict) and isinstance(item, dict) and isinstance(item.get("items"), dict):
            allowed = item["items"]
            result[key] = {child: value[child] for child in allowed if child in value}
        else:
            result[key] = value
    return result


def _config_delta(base: Dict[str, Any], submitted: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in (submitted or {}).items():
        base_value = (base or {}).get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            nested = _config_delta(base_value, value)
            if nested:
                result[key] = nested
        elif value != base_value:
            result[key] = value
    return result


def _merge_missing_dict(target: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
    changed = False
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
            changed = True
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            changed = _merge_missing_dict(target[key], value) or changed
    return changed


def _ensure_local_config_defaults() -> None:
    path = os.path.join(_plugin_dir, "config.json")
    data: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                logger.warning("[xiao_xiuxian_auto] config.json 不是 JSON 对象，已跳过自动补全配置。")
                return
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 读取 config.json 失败，已跳过自动补全配置：{e}")
            return

    changed = _merge_missing_dict(data, LINJIE_UPGRADE_CONFIG_DEFAULTS)
    changed = _merge_missing_dict(data, ENDLESS_TOWER_CONFIG_DEFAULTS) or changed
    if not changed:
        return

    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        logger.info("[xiao_xiuxian_auto] 已自动补全 config.json 中缺失的模块配置项。")
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 自动补全 config.json 失败：{e}")


def _load_local_config() -> Dict[str, Any]:
    path = os.path.join(_plugin_dir, "config.json")
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 读取本地 config.json 失败：{e}")
        return {}


def _page_override_path() -> str:
    return os.path.join(_plugin_dir, "data", "page_config_override.json")


def _load_page_override() -> Dict[str, Any]:
    path = _page_override_path()
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 读取 Page 覆盖配置失败：{e}")
        return {}


def _save_page_override(data: Dict[str, Any]) -> None:
    path = _page_override_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 保存 Page 覆盖配置失败：{e}")
        raise


def _account_overrides_path() -> str:
    return os.path.join(_plugin_dir, "data", "account_config_overrides.json")


def _load_account_overrides() -> Dict[str, Dict[str, Any]]:
    path = _account_overrides_path()
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 读取账号配置失败：{e}")
        return {}


def _save_account_overrides(data: Dict[str, Dict[str, Any]]) -> None:
    path = _account_overrides_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[xiao_xiuxian_auto] 保存账号配置失败：{e}")
        raise


class _AccountControllerProxy:
    def __init__(self, owner, name: str, default_controller):
        self._owner = owner
        self._name = name
        self._default = default_controller

    def __getattr__(self, attr_name: str):
        default_attr = getattr(self._default, attr_name)
        if not callable(default_attr):
            return default_attr

        def routed(*args, **kwargs):
            if args and isinstance(args[0], str) and ":" in args[0]:
                controller = self._owner._controller(self._name, args[0])
                return getattr(controller, attr_name)(*args, **kwargs)
            return default_attr(*args, **kwargs)

        return routed


@register(
    "astrbot_plugin_xiao_xiuxian_auto",
    "cclite123",
    "小小修仙任务挂机插件",
    "1.0.0",
)
class XiaoXiuxianAuto(Star):

    def __init__(self, context, config: dict | None = None):
        super().__init__(context)
        _ensure_local_config_defaults()
        self.context = context
        self._astrbot_config = config or {}
        self.data_dir = os.path.join(_plugin_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.store = JsonStore(os.path.join(self.data_dir, "bounty_state.json"))
        self.send_stats = DailySendStats(self.store)
        self._reload_lock = asyncio.Lock()
        self._page_api_enabled = False
        self._tick_task: Optional[asyncio.Task] = None
        self._known_keys: set[str] = set()
        self._native_hooked = False
        self._native_self_hooked = False
        self._native_official_hooked = False
        self._cached_bots: Dict[str, Any] = {}
        self._any_bot: Any = None
        self._native_hooked_bot_ids: set[str] = set()
        self._recent_self_commands: Dict[str, float] = {}
        self._recent_official_events: Dict[str, float] = {}
        self._last_native_hook_ts: float = 0.0
        self._send_queues: Dict[str, List[str]] = {}
        self._send_locks: Dict[str, asyncio.Lock] = {}
        self._send_tasks: Dict[str, asyncio.Task] = {}
        self._activity_owner: Dict[str, str] = {}
        self._send_blocked_keys: Dict[str, str] = {}
        self._last_official_command: Dict[str, str] = {}
        self._seclusion_pending_commands: Dict[str, List[str]] = {}
        self._seclusion_exit_attempts: Dict[str, int] = {}
        self._seclusion_exit_mode: Dict[str, str] = {}
        self._init_controllers()
        self._register_page_api()

    def _init_controllers(self) -> None:
        # 配置合并优先级：config.json < AstrBot 注入配置 < Page 覆盖配置
        self.cfg = _deep_merge_dict(_load_local_config(), self._astrbot_config or {})
        self.cfg = _deep_merge_dict(self.cfg, _load_page_override())
        self._captcha_guards: Dict[str, CaptchaGuard] = {}

        self.multi_cfg = dict(self.cfg.get("multi_account", {}) or {})
        self.multi_account_enabled = bool(self.multi_cfg.get("enabled", True))
        self.allow_multi_groups_per_account = bool(self.multi_cfg.get("allow_multi_groups_per_account", True))
        self.default_official_qq = str(self.cfg.get("official_bot_qq", OFFICIAL_BOT_QQ_DEFAULT))
        official_qq = self.default_official_qq

        self.market_price_config_path = os.path.join(self.data_dir, "market_price_runtime_config.json")
        file_mcfg = dict(self.cfg.get("market_price", {}) or {})
        runtime_mcfg = self._load_market_price_runtime_config()
        mcfg = dict(file_mcfg)
        mcfg.update(runtime_mcfg)

        default_remote_url = self._normalize_price_center_url(
            str(
                os.environ.get("XIAO_XIUXIAN_MARKET_URL")
                or file_mcfg.get("default_remote_url")
                or DEFAULT_MARKET_PRICE_URL
            ).strip()
        )
        configured_remote_url = self._normalize_price_center_url(str(mcfg.get("remote_url", "")).strip())
        if not configured_remote_url or self._is_placeholder_price_center_url(configured_remote_url):
            configured_remote_url = default_remote_url

        local_path = str(mcfg.get("local_path", os.path.join(self.data_dir, "market_prices_cache.json")))
        if local_path and not os.path.isabs(local_path):
            local_path = os.path.abspath(os.path.join(_plugin_dir, local_path))
        self.market_price = MarketPriceProvider(
            enabled=bool(mcfg.get("enabled", True)),
            source=str(mcfg.get("source", "hybrid")),
            local_path=local_path,
            remote_url=configured_remote_url,
            api_key=str(mcfg.get("api_key", "")),
            ttl_seconds=int(mcfg.get("ttl_seconds", 21600)),
            refresh_interval_sec=int(mcfg.get("refresh_interval_sec", 300)),
            timeout_sec=float(mcfg.get("timeout_sec", 5.0)),
            logger=logger,
        )

        bcfg = self.cfg.get("bounty", {})
        self.bounty = BountyController(
            store=self.store, official_qq=official_qq,
            default_strategy=bcfg.get("default_strategy", "修为"),
            retry_when_running_sec=int(bcfg.get("retry_when_running_sec", 30)),
            post_finish_delay_sec=int(bcfg.get("post_finish_delay_sec", 30)),
            next_morning_hour=int(bcfg.get("next_morning_hour", 8)),
            daily_start_time=bcfg.get("daily_start_time", "08:30"),
            jitter_seconds=int(bcfg.get("jitter_seconds", 600)),
            response_timeout_sec=int(bcfg.get("response_timeout_sec", 300)),
            max_response_retries=int(bcfg.get("max_response_retries", 3)),
            logger=logger,
            market_price=self.market_price,
        )

        scfg = self.cfg.get("secret", {})
        self.secret = SecretController(
            store=self.store, official_qq=official_qq,
            daily_start_time=scfg.get("daily_start_time", "12:35"),
            jitter_seconds=int(scfg.get("jitter_seconds", 600)),
            logger=logger,
        )

        self.routine = RoutineController(store=self.store, official_qq=official_qq, logger=logger)
        sect_cfg = dict(self.cfg.get("sect", {}) or {})
        self.sect = SectController(
            store=self.store,
            official_qq=official_qq,
            config=sect_cfg,
            logger=logger,
        )
        self.cultivate = CultivateController(store=self.store, official_qq=official_qq, logger=logger)
        self.sect.bind_cultivate(self.cultivate)

        inv_cfg = dict(self.cfg.get("inventory_ops", {}) or {})
        self.inventory_ops = InventoryOpsController(
            official_qq=official_qq,
            market_price=self.market_price,
            config=inv_cfg,
            runtime_path=os.path.join(self.data_dir, "inventory_ops_runtime_config.json"),
            logger=logger,
        )

        auto_alchemy_cfg = dict(self.cfg.get("auto_alchemy", {}) or {})
        self.auto_alchemy = AutoAlchemyOptimizer(
            official_qq=official_qq,
            recipe_path=os.path.join(self.data_dir, "alchemy_recipes.txt"),
            snapshot_path=os.path.join(self.data_dir, "auto_alchemy_snapshot.json"),
            page_index_path=os.path.join(self.data_dir, "alchemy_page_index.json"),
            config=auto_alchemy_cfg,
            logger=logger,
        )
        linjie_cfg = dict(self.cfg.get("linjie_upgrade", {}) or {})
        self.linjie = LinjieUpgradeController(
            store=self.store,
            official_qq=official_qq,
            config=linjie_cfg,
            logger=logger,
        )
        endless_cfg = dict(self.cfg.get("endless_tower", {}) or {})
        self.endless = EndlessTowerController(
            store=self.store,
            official_qq=official_qq,
            config=endless_cfg,
            logger=logger,
        )
        logger.info(
            "[xiao_xiuxian_auto] 炼丹配置：max_batch_formula_count=%s, max_formula_per_pill=%s, refresh_pages_each_buy_round=%s",
            self.auto_alchemy.max_batch_formula_count,
            self.auto_alchemy.max_formula_per_pill,
            self.auto_alchemy.refresh_pages_each_buy_round,
        )

        coord_cfg = self.cfg.get("coordinator", {})
        self.command_delay_sec = max(0.0, float(coord_cfg.get("command_delay_sec", 2.0)))
        name_to_module = {
            "悬赏": ACTIVITY_MODULE_BOUNTY,
            "秘境": ACTIVITY_MODULE_SECRET,
            "宗门任务": ACTIVITY_MODULE_SECT,
            "宗门": ACTIVITY_MODULE_SECT,
            ACTIVITY_MODULE_BOUNTY: ACTIVITY_MODULE_BOUNTY,
            ACTIVITY_MODULE_SECRET: ACTIVITY_MODULE_SECRET,
            ACTIVITY_MODULE_SECT: ACTIVITY_MODULE_SECT,
        }
        configured_order = coord_cfg.get("activity_order", ["悬赏", "秘境", "宗门任务"])
        self._activity_priority = dict(ACTIVITY_PRIORITY)
        for idx, name in enumerate(configured_order if isinstance(configured_order, list) else []):
            module = name_to_module.get(str(name).strip())
            if module:
                self._activity_priority[module] = (idx + 1) * 10

        fail_cfg = self.cfg.get("send_fail_policy", {}) or {}
        self.auto_block_permanent_send_error = bool(fail_cfg.get("auto_block_permanent_send_error", True))
        self.auto_unbind_on_permanent_send_error = bool(fail_cfg.get("auto_unbind_on_permanent_send_error", True))

        sg_cfg = self.cfg.get("seclusion_guard", {}) or {}
        self.seclusion_guard_enabled = bool(sg_cfg.get("enabled", True))
        self.seclusion_guard_retry_delay_sec = max(0.0, float(sg_cfg.get("retry_delay_sec", 1.5)))
        self.seclusion_guard_max_attempts = max(1, int(sg_cfg.get("max_exit_attempts", 2)))
        self.seclusion_guard_prefer_sect_exit = bool(sg_cfg.get("prefer_sect_exit", True))

        self._account_config_overrides = _load_account_overrides()
        self._account_controllers: Dict[str, Dict[str, Any]] = {}
        for name in (
            "bounty", "secret", "routine", "sect", "cultivate",
            "inventory_ops", "auto_alchemy", "linjie", "endless",
        ):
            setattr(self, name, _AccountControllerProxy(self, name, getattr(self, name)))

    @staticmethod
    def _self_id_from_key(key: str) -> str:
        return str(key or "").split(":", 1)[0].strip()

    def _effective_account_config(self, self_id: str) -> Dict[str, Any]:
        override = getattr(self, "_account_config_overrides", {}).get(str(self_id), {})
        return _deep_merge_dict(getattr(self, "cfg", {}), override)

    def _captcha_for_key(self, key: str) -> CaptchaGuard:
        self_id = self._self_id_from_key(key)
        guards = getattr(self, "_captcha_guards", None)
        if guards is None:
            guards = self._captcha_guards = {}
        cfg = self._effective_account_config(self_id)
        if self_id not in guards:
            guards[self_id] = CaptchaGuard(cfg.get("captcha", {}), logger=logger)
        else:
            # 同步 AstrBot 自带配置页或外部热更新写入的最新验证码配置。
            guards[self_id].configure(cfg.get("captcha", {}))
        return guards[self_id]

    def _account_data_dir(self, self_id: str) -> str:
        safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(self_id or "default")) or "default"
        return os.path.join(self.data_dir, "accounts", safe_id)

    @staticmethod
    def _seed_account_file(source: str, target: str) -> None:
        if os.path.exists(target) or not os.path.exists(source):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            shutil.copy2(source, target)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 初始化账号数据文件失败：{e}")

    def _build_account_controllers(self, self_id: str) -> Dict[str, Any]:
        sid = str(self_id)
        cfg = self._effective_account_config(sid)
        official_qq = self._official_qq_for_self(sid)
        account_dir = self._account_data_dir(sid)
        os.makedirs(account_dir, exist_ok=True)

        bcfg = dict(cfg.get("bounty", {}) or {})
        bounty = BountyController(
            store=self.store,
            official_qq=official_qq,
            default_strategy=bcfg.get("default_strategy", "修为"),
            retry_when_running_sec=int(bcfg.get("retry_when_running_sec", 30)),
            post_finish_delay_sec=int(bcfg.get("post_finish_delay_sec", 30)),
            next_morning_hour=int(bcfg.get("next_morning_hour", 8)),
            daily_start_time=bcfg.get("daily_start_time", "08:30"),
            jitter_seconds=int(bcfg.get("jitter_seconds", 600)),
            response_timeout_sec=int(bcfg.get("response_timeout_sec", 300)),
            max_response_retries=int(bcfg.get("max_response_retries", 3)),
            logger=logger,
            market_price=self.market_price,
        )
        secret_cfg = dict(cfg.get("secret", {}) or {})
        secret = SecretController(
            store=self.store,
            official_qq=official_qq,
            daily_start_time=secret_cfg.get("daily_start_time", "12:35"),
            jitter_seconds=int(secret_cfg.get("jitter_seconds", 600)),
            logger=logger,
        )
        routine = RoutineController(store=self.store, official_qq=official_qq, logger=logger)
        sect = SectController(
            store=self.store,
            official_qq=official_qq,
            config=dict(cfg.get("sect", {}) or {}),
            logger=logger,
        )
        cultivate = CultivateController(store=self.store, official_qq=official_qq, logger=logger)
        sect.bind_cultivate(cultivate)

        inventory_runtime = os.path.join(account_dir, "inventory_ops_runtime_config.json")
        self._seed_account_file(
            os.path.join(self.data_dir, "inventory_ops_runtime_config.json"),
            inventory_runtime,
        )
        inventory_ops = InventoryOpsController(
            official_qq=official_qq,
            market_price=self.market_price,
            config=dict(cfg.get("inventory_ops", {}) or {}),
            runtime_path=inventory_runtime,
            logger=logger,
        )

        herb_prices_path = os.path.join(account_dir, "herb_max_prices.yaml")
        self._seed_account_file(os.path.join(self.data_dir, "herb_max_prices.yaml"), herb_prices_path)
        auto_cfg = dict(cfg.get("auto_alchemy", {}) or {})
        auto_cfg["herb_max_prices_path"] = herb_prices_path
        auto_cfg["herb_grade_catalog_path"] = os.path.join(self.data_dir, "herb_max_prices.yaml")
        auto_alchemy = AutoAlchemyOptimizer(
            official_qq=official_qq,
            recipe_path=os.path.join(self.data_dir, "alchemy_recipes.txt"),
            snapshot_path=os.path.join(account_dir, "auto_alchemy_snapshot.json"),
            page_index_path=os.path.join(account_dir, "alchemy_page_index.json"),
            config=auto_cfg,
            logger=logger,
        )
        linjie = LinjieUpgradeController(
            store=self.store,
            official_qq=official_qq,
            config=dict(cfg.get("linjie_upgrade", {}) or {}),
            logger=logger,
        )
        endless = EndlessTowerController(
            store=self.store,
            official_qq=official_qq,
            config=dict(cfg.get("endless_tower", {}) or {}),
            logger=logger,
        )
        return {
            "bounty": bounty,
            "secret": secret,
            "routine": routine,
            "sect": sect,
            "cultivate": cultivate,
            "inventory_ops": inventory_ops,
            "auto_alchemy": auto_alchemy,
            "linjie": linjie,
            "endless": endless,
        }

    def _controller(self, name: str, key: str):
        sid = self._self_id_from_key(key)
        account_controllers = getattr(self, "_account_controllers", None)
        if not sid or account_controllers is None:
            controller = getattr(self, name)
            return controller._default if isinstance(controller, _AccountControllerProxy) else controller
        if sid not in account_controllers:
            account_controllers[sid] = self._build_account_controllers(sid)
        return account_controllers[sid][name]

    async def _reload_account_controllers(self, self_id: str) -> None:
        sid = str(self_id)
        for key, task in list(self._send_tasks.items()):
            if self._self_id_from_key(key) != sid:
                continue
            self._send_queues.pop(key, None)
            self._activity_owner.pop(key, None)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._send_tasks.pop(key, None)
        self._account_config_overrides = _load_account_overrides()
        self._account_controllers[sid] = self._build_account_controllers(sid)
        getattr(self, "_captcha_guards", {}).pop(sid, None)

    async def reload_config(self) -> None:
        async with self._reload_lock:
            logger.info("[xiao_xiuxian_auto] 开始热重载配置...")
            if self._tick_task:
                self._tick_task.cancel()
                try:
                    await self._tick_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                self._tick_task = None
            for task in list(self._send_tasks.values()):
                task.cancel()
            for task in list(self._send_tasks.values()):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            self._send_tasks.clear()
            self._init_controllers()
            try:
                await self.market_price.refresh(force=True)
            except Exception as e:
                logger.warning(f"[xiao_xiuxian_auto] 重载后刷新坊市价格失败：{e}")
            self._tick_task = asyncio.create_task(self._tick_loop())
            logger.info("[xiao_xiuxian_auto] 配置热重载完成")

    def _register_page_api(self) -> None:
        if not _WEB_API_AVAILABLE or not hasattr(self.context, "register_web_api"):
            self._page_api_enabled = False
            return
        try:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/config/load", self._page_load_config, ["POST"], "获取小小修仙插件配置"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/accounts", self._page_get_accounts, ["GET"], "获取可配置账号"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/config/save", self._page_save_config, ["POST"], "保存小小修仙插件配置"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/status", self._page_get_status, ["GET"], "小小修仙运行状态"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/send_stats/load", self._page_load_send_stats, ["POST"], "每日发送统计"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/alchemy_rules/load", self._page_load_alchemy_rules, ["POST"], "炼金白黑名单"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/alchemy_rules/save", self._page_save_alchemy_rules, ["POST"], "保存炼金白黑名单"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/herb_prices/load", self._page_load_herb_prices, ["POST"], "药材上限价"
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/herb_prices/save", self._page_save_herb_prices, ["POST"], "保存药材上限价"
            )
            self._page_api_enabled = True
            logger.info("[xiao_xiuxian_auto] 插件 Page API 已注册")
        except Exception as e:
            self._page_api_enabled = False
            logger.warning(f"[xiao_xiuxian_auto] 注册 Page API 失败：{e}")

    @staticmethod
    def _load_page_schema() -> Dict[str, Any]:
        schema = {}
        schema_path = os.path.join(_plugin_dir, "_conf_schema.json")
        try:
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as f:
                    schema = json.load(f)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 读取 _conf_schema.json 失败：{e}")
        return _filter_account_schema(schema)

    async def _page_account_rows(self) -> List[Dict[str, Any]]:
        bounds = await self._get_bound_dict()
        account_ids = {str(sid) for sid in bounds if str(sid) != "default"}
        configured = self.multi_cfg.get("accounts", {})
        if isinstance(configured, dict):
            account_ids.update(
                str(sid)
                for sid, profile in configured.items()
                if str(sid) != "default" and isinstance(profile, dict)
            )
        return [
            {"self_id": sid, "groups": list(bounds.get(sid, []))}
            for sid in sorted(account_ids)
        ]

    async def _page_get_accounts(self):
        return json_response({"accounts": await self._page_account_rows()})

    async def _page_validate_account(self, self_id: str) -> bool:
        return str(self_id) in {row["self_id"] for row in await self._page_account_rows()}

    async def _page_load_config(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        schema = self._load_page_schema()
        effective = self._effective_account_config(self_id)
        return json_response({
            "self_id": self_id,
            "config": _config_for_schema(effective, schema),
            "schema": schema,
        })

    async def _page_save_config(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        new_config = payload.get("config") if isinstance(payload, dict) else None
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        if not isinstance(new_config, dict):
            return error_response("config 必须是对象", status_code=400)
        try:
            schema = self._load_page_schema()
            sanitized = _config_for_schema(new_config, schema)
            overrides = _load_account_overrides()
            delta = _config_delta(_config_for_schema(self.cfg, schema), sanitized)
            if delta:
                overrides[self_id] = delta
            else:
                overrides.pop(self_id, None)
            _save_account_overrides(overrides)
        except Exception:
            return error_response("保存配置失败", status_code=500)
        reloaded = False
        try:
            await self._reload_account_controllers(self_id)
            reloaded = True
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 账号配置热重载失败：{e}")
        return json_response({"ok": True, "self_id": self_id, "reloaded": reloaded})

    async def _page_get_status(self):
        return json_response({
            "initialized": True,
            "page_api_enabled": getattr(self, "_page_api_enabled", False),
            "bound_keys": len(self._known_keys),
        })

    async def _page_load_send_stats(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        try:
            snapshot = await self.send_stats.snapshot(self_id)
        except Exception as e:
            return error_response(f"加载发送统计失败：{e}", status_code=500)
        return json_response({"self_id": self_id, **snapshot})

    async def _page_load_alchemy_rules(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        inv = self._controller("inventory_ops", f"{self_id}:page")
        return json_response({
            "self_id": self_id,
            "whitelist_pill": sorted(inv.alchemy_whitelist.get("丹药", set())),
            "blacklist_equip": sorted(inv.alchemy_blacklist.get("装备", set())),
            "blacklist_artifact": sorted(inv.alchemy_blacklist.get("神物", set())),
        })

    async def _page_save_alchemy_rules(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        try:
            self._controller("inventory_ops", f"{self_id}:page").set_alchemy_rules(
                payload.get("whitelist_pill") or [],
                payload.get("blacklist_equip") or [],
                payload.get("blacklist_artifact") or [],
            )
        except Exception as e:
            return error_response(f"保存名单失败：{e}", status_code=500)
        return json_response({"ok": True, "self_id": self_id})

    async def _page_load_herb_prices(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        optimizer = self._controller("auto_alchemy", f"{self_id}:page")
        return json_response({"self_id": self_id, **optimizer.get_herb_price_config()})

    async def _page_save_herb_prices(self):
        if request is None:
            return error_response("web API 不可用", status_code=500)
        payload = await request.json(default={})
        groups = payload.get("groups") if isinstance(payload, dict) else None
        self_id = str(payload.get("self_id") or "").strip() if isinstance(payload, dict) else ""
        if not self_id or not await self._page_validate_account(self_id):
            return error_response("请选择已配置或已绑定账号", status_code=400)
        if not isinstance(groups, dict):
            return error_response("groups 必须是对象", status_code=400)
        try:
            self._controller("auto_alchemy", f"{self_id}:page").set_herb_price_groups(groups)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        except Exception as e:
            return error_response(f"保存药材价格失败：{e}", status_code=500)
        return json_response({"ok": True, "self_id": self_id})

    async def initialize(self):
        self.store.start()
        await self._load_send_blocked_keys()
        try:
            await self.market_price.refresh(force=True)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 坊市价格初始化刷新失败：{e}")
        await self._restore_known_keys()
        self._tick_task = asyncio.create_task(self._tick_loop())
        asyncio.create_task(self._delayed_hook_native())
        logger.info("[xiao_xiuxian_auto] 插件全模块已就绪")


    def _load_market_price_runtime_config(self) -> Dict[str, Any]:




        try:
            if not os.path.exists(self.market_price_config_path):
                return {}
            with open(self.market_price_config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 读取价格中心运行时配置失败：{e}")
            return {}

    def _save_market_price_runtime_config(self) -> None:

        try:
            data = {
                "enabled": bool(self.market_price.enabled),
                "source": str(self.market_price.source or "hybrid"),
                "remote_url": str(self.market_price.remote_url or ""),
                "api_key": str(self.market_price.api_key or ""),
                "local_path": str(self.market_price.local_path or ""),
                "ttl_seconds": int(self.market_price.ttl_seconds),
                "refresh_interval_sec": int(self.market_price.refresh_interval_sec),
                "timeout_sec": float(self.market_price.timeout_sec),
            }
            os.makedirs(os.path.dirname(self.market_price_config_path), exist_ok=True)
            tmp = self.market_price_config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.market_price_config_path)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 保存价格中心运行时配置失败：{e}")

    @staticmethod
    def _normalize_price_center_url(url: str) -> str:








        url = str(url or "").strip()
        if not url:
            return ""
        if url.endswith("/api/prices/bulk"):
            return url[: -len("/api/prices/bulk")] + "/api/prices/latest"
        if url.endswith("/api/prices/latest"):
            return url
        if "/api/" not in url:
            return url.rstrip("/") + "/api/prices/latest"
        return url

    @staticmethod
    def _is_placeholder_price_center_url(url: str) -> bool:

        u = str(url or "").strip().lower()
        if not u:
            return True
        placeholders = (
            "你的服务器ip",
            "你的服务器公网ip",
            "你的公网ip",
            "server_ip",
            "your_server_ip",
            "example.com",
            "0.0.0.0",
        )
        return any(x in u for x in placeholders)

    def _get_effective_default_price_center_url(self) -> str:

        raw = str(
            os.environ.get("XIAO_XIUXIAN_MARKET_URL")
            or (self.cfg.get("market_price", {}) or {}).get("default_remote_url")
            or DEFAULT_MARKET_PRICE_URL
        ).strip()
        return self._normalize_price_center_url(raw)

    async def cmd_reset_price_center_url(self) -> str:

        url = self._get_effective_default_price_center_url()
        if not url or self._is_placeholder_price_center_url(url):
            return ("❌ 当前没有可用的默认价格中心地址。\n"
                    "请先在 main.py 顶部修改 DEFAULT_MARKET_PRICE_URL，或在 config.json 中设置 market_price.default_remote_url。")
        self.market_price.remote_url = url
        self.market_price.source = "hybrid"
        self.market_price.enabled = True
        self._save_market_price_runtime_config()
        ok = await self.market_price.refresh(force=True)
        summary = await self.market_price.summary()
        return ("✅ 已恢复使用默认价格中心\n"
                f"默认地址：{url}\n"
                f"刷新结果：{'成功' if ok else '失败或暂无价格'}\n"
                f"{summary}")

    async def cmd_set_price_center_url(self, raw_url: str) -> str:
        raw_url = str(raw_url or "").strip()
        if not raw_url:
            return await self.cmd_reset_price_center_url()
        url = self._normalize_price_center_url(raw_url)
        if not (url.startswith("http://") or url.startswith("https://")):
            return "❌ 价格中心地址必须以 http:// 或 https:// 开头"
        self.market_price.remote_url = url
        self.market_price.source = "hybrid"
        self.market_price.enabled = True
        self._save_market_price_runtime_config()
        ok = await self.market_price.refresh(force=True)
        summary = await self.market_price.summary()
        return ("✅ 已设置并开启价格中心读取\n"
                f"读取地址：{url}\n"
                f"刷新结果：{'成功' if ok else '失败或暂无价格'}\n"
                f"{summary}")

    async def cmd_set_price_center_key(self, api_key: str) -> str:
        self.market_price.api_key = str(api_key or "").strip()
        self._save_market_price_runtime_config()
        return "✅ 已更新价格中心读取密钥" if self.market_price.api_key else "✅ 已清空价格中心读取密钥"

    async def cmd_enable_price_center(self) -> str:
        self.market_price.enabled = True
        if not self.market_price.source:
            self.market_price.source = "hybrid"
        self._save_market_price_runtime_config()
        ok = await self.market_price.refresh(force=True)
        summary = await self.market_price.summary()
        return ("✅ 已开启价格中心动态估价\n" if ok else "⚠️ 已开启价格中心，但当前未拉取到可用价格\n") + summary

    async def cmd_disable_price_center(self) -> str:
        self.market_price.enabled = False
        self._save_market_price_runtime_config()
        return "🛑 已关闭价格中心动态估价；悬赏价值策略将回退为插件内置估价。"


    def _account_profile(self, self_id: str) -> Dict[str, Any]:





        accounts = self.multi_cfg.get("accounts", {})
        if not isinstance(accounts, dict):
            return {}
        sid = str(self_id or "").strip()
        profile = accounts.get(sid)
        if not isinstance(profile, dict):
            profile = accounts.get("default", {})
        return dict(profile or {}) if isinstance(profile, dict) else {}

    def _account_enabled(self, self_id: str) -> bool:
        profile = self._account_profile(self_id)
        return bool(profile.get("enabled", True))

    @staticmethod
    def _normalize_group_list(value) -> List[str]:

        if value is None or value == "":
            return []
        if isinstance(value, (list, tuple, set)):
            out = []
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
            return out
        text = str(value or "").strip()
        if not text:
            return []

        if "," in text or "，" in text:
            parts = re.split(r"[,，]", text)
            return [x.strip() for x in parts if x.strip()]
        return [text]

    def _configured_bound_dict(self) -> Dict[str, List[str]]:

        result: Dict[str, List[str]] = {}
        accounts = self.multi_cfg.get("accounts", {})
        if not isinstance(accounts, dict):
            return result
        for sid, profile in accounts.items():
            if str(sid) == "default" or not isinstance(profile, dict):
                continue
            if profile.get("enabled", True) is False:
                continue
            groups: List[str] = []
            for k in ("groups", "group_ids", "group_id", "bind_group", "bind_groups"):
                groups.extend(self._normalize_group_list(profile.get(k)))
            dedup: List[str] = []
            for gid in groups:
                if gid and gid not in dedup:
                    dedup.append(gid)
            if dedup:
                result[str(sid)] = dedup
        return result

    def _official_qq_for_self(self, self_id: str) -> str:

        profile = self._account_profile(self_id)
        return str(profile.get("official_bot_qq") or self.default_official_qq or OFFICIAL_BOT_QQ_DEFAULT)

    def _official_qq_for_key(self, key: str) -> str:
        try:
            self_id = str(key).split(":", 1)[0]
        except Exception:
            self_id = ""
        return self._official_qq_for_self(self_id)

    def _rewrite_official_target(self, key: str, text: str) -> str:

        text = str(text or "").strip()
        target = self._official_qq_for_key(key)
        if not target:
            return text
        default = str(self.default_official_qq or OFFICIAL_BOT_QQ_DEFAULT)
        if text.startswith(f"@{target}"):
            return text
        if text.startswith(f"@{default}"):
            return f"@{target}" + text[len(default) + 1:]

        if self._is_official_command(text, key=key):
            return f"@{target} {text}"
        return text

    async def _load_send_blocked_keys(self) -> None:





        try:
            data = await self.store.get(SEND_BLOCKED_KEY, {})
            if isinstance(data, dict):
                self._send_blocked_keys = {str(k): str(v) for k, v in data.items()}
            else:
                self._send_blocked_keys = {}
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 读取发送屏蔽列表失败：{e}")
            self._send_blocked_keys = {}

    async def _save_send_blocked_keys(self) -> None:

        try:
            await self.store.set(SEND_BLOCKED_KEY, dict(self._send_blocked_keys))
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 保存发送屏蔽列表失败：{e}")

    def _is_key_send_blocked(self, key: str) -> bool:

        return str(key) in self._send_blocked_keys

    @staticmethod
    def _is_permanent_send_error(error: Exception | str | None) -> bool:






        msg = str(error or "")
        keywords = (
            "你已被移出该群",
            "请重新加群",
            "bot is not in group",
            "not in group",
            "group not found",
            "群不存在",
            "群聊不存在",
            "不在该群",
            "不在群内",
        )
        return any(k.lower() in msg.lower() for k in keywords)

    @staticmethod
    def _send_error_reason(error: Exception | str | None) -> str:
        msg = str(error or "发送失败")
        if "你已被移出该群" in msg or "请重新加群" in msg:
            return "机器人已被移出该群"
        if "not in group" in msg.lower() or "不在群" in msg or "不在该群" in msg:
            return "机器人不在该群"
        if "群不存在" in msg or "群聊不存在" in msg or "group not found" in msg.lower():
            return "群不存在或不可访问"
        return "永久发送失败"

    async def _clear_send_blocked(self, key: str) -> None:

        key = str(key)
        if key in self._send_blocked_keys:
            self._send_blocked_keys.pop(key, None)
            await self._save_send_blocked_keys()
            logger.info(f"[xiao_xiuxian_auto] 已清除发送屏蔽：{key}")

    async def _mark_send_blocked(self, key: str, error: Exception | str | None) -> None:

        key = str(key)
        reason = self._send_error_reason(error)
        first_time = key not in self._send_blocked_keys
        self._send_blocked_keys[key] = reason
        await self._save_send_blocked_keys()


        self._send_queues.pop(key, None)
        self._activity_owner.pop(key, None)
        task = self._send_tasks.get(key)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        if task is not current:
            self._send_tasks.pop(key, None)


        if self.auto_unbind_on_permanent_send_error:
            try:
                self_id, group_id = key.split(":", 1)
                await self._remove_bound(self_id, group_id)
            except Exception as e:
                logger.warning(f"[xiao_xiuxian_auto] 自动解绑失败 key={key}: {e}")

        if first_time:
            logger.warning(
                f"[xiao_xiuxian_auto] 检测到永久发送失败，已停用该账号/群的自动任务：key={key}，原因：{reason}。"
                "如机器人已重新加群，请在该群发送“绑定此群”恢复。"
            )

    async def _get_bound_dict(self) -> Dict[str, List[str]]:









        runtime = await self.store.get(BIND_KEY, {})
        result: Dict[str, List[str]] = {}

        if isinstance(runtime, dict):
            if "self_id" in runtime and "group_id" in runtime:
                result[str(runtime["self_id"])] = [str(runtime["group_id"])]
            else:
                for sid, groups in runtime.items():
                    if str(sid).startswith("__"):
                        continue
                    glist = self._normalize_group_list(groups)
                    if glist:
                        result[str(sid)] = glist


        for sid, groups in self._configured_bound_dict().items():
            cur = result.setdefault(str(sid), [])
            for gid in groups:
                if gid not in cur:
                    cur.append(gid)

        return result

    async def _iter_bound_pairs(self) -> List[tuple[str, str]]:
        pairs: List[tuple[str, str]] = []
        bounds = await self._get_bound_dict()
        for self_id, groups in bounds.items():
            if not self._account_enabled(self_id):
                continue
            for group_id in groups:
                pairs.append((str(self_id), str(group_id)))
        return pairs

    async def _get_bound_groups(self, self_id: str) -> List[str]:
        return list((await self._get_bound_dict()).get(str(self_id), []))

    async def _get_bound_group(self, self_id: str) -> Optional[str]:
        groups = await self._get_bound_groups(self_id)
        return groups[0] if groups else None

    async def _set_bound(self, self_id: str, group_id: str, replace: bool = False) -> None:
        runtime = await self.store.get(BIND_KEY, {})
        if not isinstance(runtime, dict) or ("self_id" in runtime and "group_id" in runtime):
            runtime = {}
        sid = str(self_id)
        gid = str(group_id)
        if replace or not self.allow_multi_groups_per_account:
            runtime[sid] = [gid]
        else:
            groups = self._normalize_group_list(runtime.get(sid))
            if gid not in groups:
                groups.append(gid)
            runtime[sid] = groups
        await self.store.set(BIND_KEY, runtime)
        await self._clear_send_blocked(f"{sid}:{gid}")

    async def _remove_bound(self, self_id: str, group_id: str) -> bool:
        runtime = await self.store.get(BIND_KEY, {})
        if not isinstance(runtime, dict):
            return False
        sid = str(self_id)
        gid = str(group_id)
        groups = self._normalize_group_list(runtime.get(sid))
        if gid not in groups:
            return False
        groups = [x for x in groups if x != gid]
        if groups:
            runtime[sid] = groups
        else:
            runtime.pop(sid, None)
        await self.store.set(BIND_KEY, runtime)
        return True

    async def _is_bound_match(self, self_id: str, group_id) -> bool:
        if group_id is None:
            return False
        if not self._account_enabled(str(self_id)):
            return False
        groups = await self._get_bound_groups(str(self_id))
        return str(group_id) in {str(x) for x in groups}

    async def cmd_account_bind_status(self, self_id: Optional[str] = None) -> str:
        bounds = await self._get_bound_dict()
        if self_id:
            bounds = {str(self_id): bounds.get(str(self_id), [])}
        if not bounds:
            return "📋 当前没有任何账号绑定。\n请用对应机器人账号在目标群发送：绑定此群"
        lines = ["📋 【多账号绑定状态】"]
        for sid in sorted(bounds.keys()):
            groups = bounds.get(sid, [])
            enabled = "✅启用" if self._account_enabled(sid) else "🛑禁用"
            official = self._official_qq_for_self(sid)
            group_text = "、".join(str(x) for x in groups) if groups else "未绑定"
            lines.append(f"账号 {sid}：{enabled} | 小小 {official} | 群 {group_text}")
        lines.append("\n指令：绑定此群 / 更改绑定 / 解绑此群 / 绑定列表")
        return "\n".join(lines)

    async def _restore_known_keys(self):
        for self_id, group_id in await self._iter_bound_pairs():
            self._known_keys.add(f"{self_id}:{group_id}")

    async def _delayed_hook_native(self):
        for _ in range(20):
            if self._hook_native_self_message(): return
            await asyncio.sleep(0.5)
        logger.warning("[xiao_xiuxian_auto] 挂载原生钩子失败")

    async def terminate(self):
        if self._tick_task:
            self._tick_task.cancel()
            try: await self._tick_task
            except asyncio.CancelledError: pass
            except Exception: pass
        for task in list(self._send_tasks.values()):
            task.cancel()
        for task in list(self._send_tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._send_tasks.clear()
        await self.store.stop()

    async def _tick_loop(self):
        while True:
            try:
                await asyncio.sleep(1.0)

                now_ts = time.time()
                if now_ts - getattr(self, "_last_native_hook_ts", 0.0) >= 10.0:
                    self._last_native_hook_ts = now_ts
                    self._hook_native_self_message()
                pairs = await self._iter_bound_pairs()
                if not pairs: continue
                for self_id, group_id in pairs:
                    bound_key = f"{self_id}:{group_id}"
                    if self._is_key_send_blocked(bound_key):
                        continue
                    self._known_keys.add(bound_key)
                    send_cb = self._make_send_cb(bound_key)
                    if send_cb is not None:
                        await self._controller("bounty", bound_key).tick(bound_key, send_cb)
                        await self._controller("secret", bound_key).tick(bound_key, send_cb)
                        await self._controller("routine", bound_key).tick(bound_key, send_cb)
                        await self._controller("sect", bound_key).tick(bound_key, send_cb)
                        await self._maybe_restore_rest_after_activities_done(bound_key, send_cb)
                        await self._controller("cultivate", bound_key).tick(bound_key, send_cb)
                        await self._controller("inventory_ops", bound_key).tick(bound_key, send_cb)
                        await self._controller("auto_alchemy", bound_key).tick(bound_key, send_cb)
                        await self._controller("linjie", bound_key).tick(bound_key, send_cb)
                        await self._controller("endless", bound_key).tick(bound_key, send_cb)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[xiao_xiuxian_auto] tick 异常：{e}")

    def _phase_label(self, phase: str) -> str:





        mapping = {
            "IDLE": "未启动",
            "SLEEPING": "等待中",
            "WORKING": "运行中",
            "PROBING": "查询中",
            "QUERYING": "查询中",
            "REFRESHING": "刷新中",
            "CHOOSING": "选择中",
            "SETTLING": "结算中",
            "EXPLORING": "进行中",
            "SETTLING_1": "结算中",
            "SETTLING_2": "结算中",
            "VERIFYING": "确认结果中",
            "WAITING_REFRESH": "等待刷新",
            "WAITING_HP": "等待气血恢复",
        }
        return mapping.get(str(phase or "").strip(), "运行中")

    def _next_text(self, enabled: bool, phase: str, action_ts: float = 0.0, wake_ts: float = 0.0, settle_ts: float = 0.0) -> str:




        if not enabled:
            return "已关闭"
        if settle_ts:
            return f"预计 {fmt_ts_compact(settle_ts)} 结算"
        if action_ts:
            return f"下一动作 {fmt_ts_compact(action_ts)}"
        if wake_ts:
            return f"下次执行 {fmt_ts_compact(wake_ts)}"
        return self._phase_label(phase)

    async def cmd_task_status(self, key: str) -> str:

        linjie = self._controller("linjie", key)
        bounty_st = await self._controller("bounty", key)._get(key)
        secret_st = await self._controller("secret", key)._get(key)
        routine_st = await self._controller("routine", key)._get(key)
        sect_st = await self._controller("sect", key)._get(key)
        cult_st = await self._controller("cultivate", key)._get(key)
        linjie_st = await linjie._get(key)
        endless_st = await self._controller("endless", key)._get(key)

        bounty_next = "已关闭"
        if bounty_st.enabled:
            if bounty_st.phase == "WORKING" and bounty_st.settle_at_ts:
                bounty_next = f"悬赏已接收《{bounty_st.current_title or '未知'}》，预计 {fmt_ts_compact(bounty_st.settle_at_ts)} 结算"
            elif bounty_st.phase == "SLEEPING" and bounty_st.wake_at_ts:
                bounty_next = f"今日结束，下次 {fmt_ts_compact(bounty_st.wake_at_ts)} 执行"
            else:
                bounty_next = f"下一动作 {fmt_ts_compact(bounty_st.last_action_ts)}" if bounty_st.last_action_ts else "等待小小回执"

        secret_next = "已关闭"
        if secret_st.enabled:
            if secret_st.phase == "EXPLORING" and secret_st.settle_at_ts:
                secret_next = f"秘境《{secret_st.current_area or '未知'}》进行中，预计 {fmt_ts_compact(secret_st.settle_at_ts)} 结算"
            elif secret_st.phase == "SLEEPING" and secret_st.wake_at_ts:
                secret_next = f"今日结束，下次 {fmt_ts_compact(secret_st.wake_at_ts)} 执行"
            elif secret_st.next_step_ts:
                secret_next = f"下一动作 {fmt_ts_compact(secret_st.next_step_ts)}"
            else:
                secret_next = "等待小小回执"

        sect_next = "已关闭"
        if sect_st.enabled:
            if sect_st.phase == "SLEEPING" and sect_st.wake_at_ts:
                sect_next = f"今日结束，下次 {fmt_ts_compact(sect_st.wake_at_ts)} 执行"
            elif sect_st.next_action_ts:
                sect_next = f"下一动作 {fmt_ts_compact(sect_st.next_action_ts)}"
            else:
                sect_next = "等待小小回执"

        def routine_line(name: str, enabled: bool, phase: str, action_ts: float, wake_ts: float) -> str:
            if not enabled:
                return f"{name}：已关闭"
            ts = action_ts if phase == "WORKING" else wake_ts
            if ts:
                label = "下一动作" if phase == "WORKING" else "下次执行"
                return f"{name}：{label} {fmt_ts_compact(ts)}"
            return f"{name}：{self._phase_label(phase)}"

        cult_next = "已关闭"
        if cult_st.mode:
            cult_next = f"{cult_st.mode} / {'休息中' if cult_st.is_resting else '活动中'}，气血 {cult_st.hp_percent:.1f}%"
        endless_next = self._next_text(
            endless_st.enabled,
            endless_st.phase,
            action_ts=endless_st.next_action_ts,
            wake_ts=endless_st.wake_at_ts,
        )
        captcha_pause = self._captcha_for_key(key).status(key)
        captcha_next = (
            f"⏸️暂停：{captcha_pause.reason}\n阶段：{captcha_pause.phase}\n消息序号：{captcha_pause.msg_seq or '无'}"
            if captcha_pause.active else "✅正常"
        )

        return ("📊 【任务状态总览】\n"
                f"🎯 悬赏：{bounty_next}\n"
                f"🗺️ 秘境：{secret_next}\n"
                f"🏯 宗门任务：{sect_next}\n"
                f"📅 签到：{routine_line('签到', routine_st.signin_enabled, routine_st.sign_phase, routine_st.sign_action_ts, routine_st.sign_wake_ts).split('：',1)[1]}\n"
                f"💊 领丹：{routine_line('领丹', routine_st.pill_enabled, routine_st.pill_phase, routine_st.pill_action_ts, routine_st.pill_wake_ts).split('：',1)[1]}\n"
                f"⛏️ 挖灵石：{routine_line('挖灵石', routine_st.mine_enabled, routine_st.mine_phase, routine_st.mine_action_ts, routine_st.mine_wake_ts).split('：',1)[1]}\n"
                f"🌾 灵田：{routine_line('灵田', routine_st.farm_enabled, routine_st.farm_phase, routine_st.farm_action_ts, routine_st.farm_wake_ts).split('：',1)[1]}\n"
                f"🏔️ 灵界升级：{linjie.summary_line(linjie_st)}\n"
                f"🗼 无尽妖塔：{endless_next}，进度 {endless_st.done_count}/{endless_st.target_count if endless_st.target_count > 0 else '无限'}\n"
                f"🧩 验证码守卫：{captcha_next}\n"
                f"🧘 修炼/闭关：{cult_next}")


    async def cmd_menu(self, key: str, sub_menu: str) -> str:
        self_id = key.split(":")[0]
        bound_groups = await self._get_bound_groups(self_id)
        bound_group = "、".join(bound_groups) if bound_groups else "未绑定"

        linjie = self._controller("linjie", key)
        bounty_st = await self._controller("bounty", key)._get(key)
        secret_st = await self._controller("secret", key)._get(key)
        routine_st = await self._controller("routine", key)._get(key)
        sect_st = await self._controller("sect", key)._get(key)
        cult_st = await self._controller("cultivate", key)._get(key)
        linjie_st = await linjie._get(key)
        endless_st = await self._controller("endless", key)._get(key)

        if not sub_menu:
            return (f"📜 【小小修仙】总菜单 📜\n"
                    f"🏠 当前绑定群：{bound_group}\n\n"
                    f"📊 模块运行状态：\n"
                    f"🔹 [悬赏]：{'✅开启' if bounty_st.enabled else '🛑关闭'} (策略:{bounty_st.strategy}) | {self._next_text(bounty_st.enabled, bounty_st.phase, wake_ts=bounty_st.wake_at_ts, settle_ts=bounty_st.settle_at_ts)}\n"
                    f"🔹 [秘境]：{'✅开启' if secret_st.enabled else '🛑关闭'} | {self._next_text(secret_st.enabled, secret_st.phase, action_ts=secret_st.next_step_ts, wake_ts=secret_st.wake_at_ts, settle_ts=secret_st.settle_at_ts)}\n"
                    f"🔹 [宗门]：{'✅开启' if sect_st.enabled else '🛑关闭'} ({sect_st.daily_hour:02d}:{sect_st.daily_minute:02d}) | {self._next_text(sect_st.enabled, sect_st.phase, action_ts=sect_st.next_action_ts, wake_ts=sect_st.wake_at_ts)}\n"
                    f"🔹 [日常]：签到{'✅' if routine_st.signin_enabled else '🛑'}({fmt_ts_compact(routine_st.sign_action_ts or routine_st.sign_wake_ts)}) 领丹{'✅' if routine_st.pill_enabled else '🛑'}({fmt_ts_compact(routine_st.pill_action_ts or routine_st.pill_wake_ts)}) 挖矿{'✅' if routine_st.mine_enabled else '🛑'}({fmt_ts_compact(routine_st.mine_action_ts or routine_st.mine_wake_ts)}) 灵田{'✅' if routine_st.farm_enabled else '🛑'}({fmt_ts_compact(routine_st.farm_action_ts or routine_st.farm_wake_ts)})\n"
                    f"🔹 [物品]：一键上架 / 一键炼金\n"
                    f"🔹 [炼丹]：开启炼丹 / 开启背包炼丹 / 开启购买药材 / 开启动态购买 / 炼丹 丹药 数量 / 暂停继续关闭\n"
                    f"🔹 [灵界]：{'✅开启' if linjie_st.enabled else '🛑关闭'} | {linjie.summary_line(linjie_st)}\n"
                    f"🔹 [无尽]：{'✅开启' if endless_st.enabled else '🛑关闭'} | 进度:{endless_st.done_count}/{endless_st.target_count if endless_st.target_count > 0 else '无限'} 真元检测:{'✅' if endless_st.check_mp_enabled else '🛑'}({endless_st.mp_threshold}%)\n"
                    f"🔹 [坊市]：刷新坊市价格 / 更新坊市价格\n"
                    f"🔹 [休息]：{cult_st.mode or '未设置'} ({'休息中' if cult_st.is_resting else '活动中'}) | 气血:{cult_st.hp_percent:.1f}%\n\n"
                    f"📖 查看详细子目录指令，请发送：\n"
                    f"▶ 修仙菜单 悬赏\n"
                    f"▶ 修仙菜单 秘境\n"
                    f"▶ 修仙菜单 宗门\n"
                    f"▶ 修仙菜单 日常\n"
                    f"▶ 修仙菜单 修炼\n"
                    f"▶ 修仙菜单 物品\n"
                    f"▶ 修仙菜单 炼丹\n"
                    f"▶ 修仙菜单 灵界\n"
                    f"▶ 修仙菜单 无尽\n"
                    f"▶ 修仙菜单 系统\n"
                    f"▶ 任务状态 / 修仙状态")

        elif sub_menu == "悬赏":
            return (f"📜 【悬赏模块】指令说明 📜\n"
                    f"当前状态：{'✅开启' if bounty_st.enabled else '🛑关闭'}\n"
                    f"当前策略：{bounty_st.strategy}\n\n"
                    f"▶ 开启悬赏 / 关闭悬赏\n"
                    f"▶ 悬赏[修为/价值/耗时]\n"
                    f"▶ 统计")

        elif sub_menu == "秘境":
            return (f"📜 【秘境模块】指令说明 📜\n"
                    f"当前状态：{'✅开启' if secret_st.enabled else '🛑关闭'}\n"
                    f"今日完成轮数：{secret_st.daily_count}\n\n"
                    f"▶ 开启秘境 / 关闭秘境")

        elif sub_menu == "宗门":
            tasks = [k for k, v in sect_st.tasks.items() if v]
            t_str = "、".join(tasks) if tasks else "无"
            return (f"📜 【宗门任务模块】指令说明 📜\n"
                    f"当前状态：{'✅开启' if sect_st.enabled else '🛑关闭'}\n"
                    f"执行时间：每日 {sect_st.daily_hour:02d}:{sect_st.daily_minute:02d}\n"
                    f"接取目标：{t_str}\n\n"
                    f"▶ 开启宗门任务 / 关闭宗门任务\n"
                    f"▶ 宗门任务时间 xx.xx\n"
                    f"▶ 宗门任务状态\n"
                    f"▶ 开启宗门任务[除魔/密令/仙丹/疏财/红尘]\n"
                    f"▶ 关闭宗门任务[除魔/密令/仙丹/疏财/红尘]\n"
                    f"▶ 宗门任务接取")

        elif sub_menu == "日常":
            return (f"📜 【日常任务模块】指令说明 📜\n"
                    f"签到：{'✅开启' if routine_st.signin_enabled else '🛑关闭'}\n"
                    f"领丹：{'✅开启' if routine_st.pill_enabled else '🛑关闭'} (失败计数:{routine_st.pill_fail_count})\n"
                    f"挖矿：{'✅开启' if routine_st.mine_enabled else '🛑关闭'}\n"
                    f"灵田：{'✅开启' if routine_st.farm_enabled else '🛑关闭'}\n\n"
                    f"▶ 开启签到 / 关闭签到\n"
                    f"▶ 开启领丹 / 关闭领丹\n"
                    f"▶ 开启挖矿 / 关闭挖矿\n"
                    f"▶ 开启灵田 / 关闭灵田")

        elif sub_menu == "修炼":
            return (f"📜 【修炼闭关模块】指令说明 📜\n"
                    f"当前模式：{cult_st.mode or '未设置'}\n"
                    f"休息状态：{'正在休息' if cult_st.is_resting else '自由活动'}\n"
                    f"当前气血：{cult_st.hp_percent:.1f}%\n\n"
                    f"▶ 开启修炼 / 关闭修炼\n"
                    f"▶ 开启闭关 / 关闭闭关\n"
                    f"▶ 开启宗门闭关 / 关闭宗门闭关\n"
                    f"▶ 查询气血")

        elif sub_menu == "物品":
            return ("📜 【一键上架 / 一键炼金模块】指令说明 📜\n"
                    "上架指令：\n"
                    "▶ 一键上架药材 / 一键上架丹药\n"
                    "▶ 一键上架装备 / 一键上架神物\n\n"
                    "炼金指令：\n"
                    "▶ 一键炼金药材 / 一键炼金丹药\n"
                    "▶ 一键炼金装备 / 一键炼金神物\n\n"
                    "名单配置：\n"
                    "▶ 炼金名单\n"
                    "▶ 添加炼金白名单 丹药 物品名1 物品名2\n"
                    "▶ 删除炼金白名单 丹药 物品名1\n"
                    "▶ 添加炼金黑名单 装备 物品名1\n"
                    "▶ 删除炼金黑名单 装备 物品名1\n"
                    "▶ 添加炼金黑名单 神物 物品名1\n"
                    "▶ 删除炼金黑名单 神物 物品名1")

        elif sub_menu == "炼丹":
            return ("📜 【炼丹利润模块】指令说明 📜\n"
                    "▶ 开启炼丹\n"
                    "▶ 开启背包炼丹\n"
                    "▶ 炼丹 丹药名称 数量\n"
                    "▶ 暂停炼丹 / 继续炼丹 / 关闭炼丹\n"
                    "▶ 炼丹状态\n"
                    "\n"
                    "📜 【购买药材】\n"
                    "▶ 开启购买药材\n"
                    "▶ 开启购买药材 X  （X为轮数1-99）\n"
                    "▶ 关闭购买药材\n"
                    "说明：购买药材会按 herb_max_prices.yaml 中的最高价筛选坊市药材，符合则购买。\n"
                    "\n"
                    "📜 【动态购买开关】\n"
                    "▶ 开启动态购买 / 关闭动态购买\n"
                    "说明：开启后，炼丹遍历坊市时会购买符合最高价的药材（仅当前页，不触发多轮购买）。\n"
                    "\n"
                    "说明：开启炼丹会遍历坊市1-8页采集价格，然后读取背包药材进行背包抵扣，筛选利润>配置阈值的丹方；"
                    "开启背包炼丹只使用背包药材，不做坊市购买，盈利>10万即可匹配丹方。")

        elif sub_menu == "灵界":
            return (f"📜 【灵界升级模块】指令说明 📜\n"
                    f"当前状态：{'✅开启' if linjie_st.enabled else '🛑关闭'}\n"
                    f"运行阶段：{linjie.summary_line(linjie_st)}\n"
                    f"灵矿石缓存：{format_linjie_money(linjie_st.balance)}\n\n"
                    f"▶ 开启灵界升级\n"
                    f"▶ 关闭灵界升级\n"
                    f"▶ 灵界规划\n"
                    f"▶ 灵界刷新规划\n"
                    f"▶ 灵界规划详情\n"
                    f"▶ 灵界规划序列\n"
                    f"▶ 灵界状态\n"
                    f"说明：启动时集中查询灵界信息，后续按成功回执更新缓存，按 ROI 性价比选择下一项。")

        elif sub_menu == "无尽":
            last_mp = "未知" if endless_st.last_mp < 0 else f"{endless_st.last_mp:g}%"
            return (f"📜 【无尽妖塔模块】指令说明 📜\n"
                    f"当前状态：{'✅开启' if endless_st.enabled else '🛑关闭'}\n"
                    f"挑战进度：{endless_st.done_count}/{endless_st.target_count if endless_st.target_count > 0 else '无限'}\n"
                    f"真元检测：{'✅开启' if endless_st.check_mp_enabled else '🛑关闭'}，阈值：{endless_st.mp_threshold}%，最近真元：{last_mp}\n\n"
                    f"▶ 开启无尽\n"
                    f"▶ 开启无尽 100\n"
                    f"▶ 关闭无尽\n"
                    f"▶ 开启真元检测 / 关闭真元检测\n"
                    f"▶ 设置真元检测 600\n"
                    f"▶ 无尽状态")

        elif sub_menu == "系统":
            return (f"📜 【系统模块】指令说明 📜\n"
                    f"当前绑定群：{bound_group}\n\n"
                    f"▶ 绑定此群\n"
                    f"▶ 更改绑定\n"
                    f"▶ 解绑此群\n"
                    f"▶ 绑定列表 / 多账号状态\n"
                    f"▶ 任务状态 / 修仙状态\n"
                    f"▶ 坊市价格状态 / 价格状态\n"
                    f"▶ 刷新坊市价格 / 更新坊市价格\n"
                    f"▶ 开启坊市价格 / 关闭坊市价格\n"
                    f"▶ 默认价格中心 / 重置价格中心\n"
                    f"▶ 修仙菜单")

        else:
            return "❌ 未知的子目录，请输入：悬赏、秘境、宗门、日常、修炼、物品、炼丹、灵界、无尽、系统"

    def _make_send_cb(self, key: str):









        try:
            self_id, group_id = key.split(":", 1)
        except ValueError:
            return None

        async def _send(text: str):
            text = str(text or "").strip()
            if not text:
                return




            is_activity_cmd = (
                "探索秘境" in text
                or any(x in text for x in ["悬赏令查看", "悬赏令刷新", "悬赏令接取", "悬赏令结算"])
                or any(x in text for x in ["宗门任务接取", "宗门任务刷新", "宗门任务完成"])
            )
            if is_activity_cmd:
                cultivate = self._controller("cultivate", key)
                if not await cultivate.request_idle(key, _send):
                    cultivate.queue_pending(key, text)
                    return
                if "探索秘境" in text and not await cultivate.ensure_secret_entry_hp(key, _send, min_pct=80.0):
                    cultivate.queue_pending(key, text)
                    return

            text = self._rewrite_official_target(key, text)
            if self._is_official_command(text, key=key):
                await self._enqueue_official_command(key, text)
            else:
                await self._raw_send_by_key(key, text)

        return _send




    def _is_official_command(self, text: str, key: Optional[str] = None) -> bool:






        text = str(text or "").strip()
        official_qqs = {str(self.default_official_qq or OFFICIAL_BOT_QQ_DEFAULT)}
        if key:
            official_qqs.add(str(self._official_qq_for_key(key)))
        for official_qq in official_qqs:
            if official_qq and text.startswith(f"@{official_qq}"):
                return True


        exact_commands = {
            "悬赏令查看", "悬赏令刷新", "悬赏令结算",
            "探索秘境", "秘境结算",
            "宗门任务接取", "宗门任务刷新", "宗门任务完成",
            "修仙签到", "宗门丹药领取", "挖灵石", "灵田结算",
            "我的状态", "修炼", "闭关", "出关", "宗门闭关", "宗门出关",
            "挑战无尽妖塔",
            "灵界我的信息", "灵界建筑列表", "灵界升级列表", "灵界杂役名录",
            "灵界技艺修行", "灵界技艺突破", "灵界杂役升阶", "灵界挖灵石",
        }
        if text in exact_commands:
            return True
        return bool(re.fullmatch(r"悬赏令接取\d+|药材背包\d*|丹药背包\d*|我的背包\d*|确认坊市上架\s+.+?\s+\d+\s+\d+|炼金\s+.+?\s+\d+|灵界建造.+?\s+\d+|灵界招募.+?\s+\d+|灵界升级建筑.+", text))

    def _activity_module_of(self, text: str) -> Optional[str]:




        text = str(text or "")
        if any(k in text for k in ("悬赏令查看", "悬赏令刷新", "悬赏令接取", "悬赏令结算")):
            return ACTIVITY_MODULE_BOUNTY
        if any(k in text for k in ("探索秘境", "秘境结算")):
            return ACTIVITY_MODULE_SECRET
        if any(k in text for k in ("宗门任务接取", "宗门任务刷新", "宗门任务完成")):
            return ACTIVITY_MODULE_SECT
        return None

    async def _is_activity_state_active(self, key: str, module: str) -> bool:

        try:
            if module == ACTIVITY_MODULE_BOUNTY:
                st = await self._controller("bounty", key)._get(key)
                return bool(st.enabled and st.phase not in INACTIVE_PHASES)
            if module == ACTIVITY_MODULE_SECRET:
                st = await self._controller("secret", key)._get(key)
                return bool(st.enabled and st.phase not in INACTIVE_PHASES)
            if module == ACTIVITY_MODULE_SECT:
                st = await self._controller("sect", key)._get(key)
                return bool(st.enabled and st.phase not in INACTIVE_PHASES)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 检查玩法状态失败: key={key} module={module} err={e}")
        return False

    async def _any_activity_state_active(self, key: str) -> bool:

        for module in (ACTIVITY_MODULE_BOUNTY, ACTIVITY_MODULE_SECRET, ACTIVITY_MODULE_SECT):
            if await self._is_activity_state_active(key, module):
                return True
        return False

    async def _any_activity_state_sleeping(self, key: str) -> bool:

        try:
            b = await self._controller("bounty", key)._get(key)
            if b.enabled and b.phase == "SLEEPING":
                return True
            sec = await self._controller("secret", key)._get(key)
            if sec.enabled and sec.phase == "SLEEPING":
                return True
            sect = await self._controller("sect", key)._get(key)
            if sect.enabled and sect.phase == "SLEEPING":
                return True
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 检查玩法休息状态失败: key={key} err={e}")
        return False

    async def _maybe_restore_rest_after_activities_done(self, key: str, send_cb) -> None:






        if send_cb is None:
            return
        if await self._any_activity_state_active(key):
            return
        if not await self._any_activity_state_sleeping(key):
            return
        try:
            await self._controller("cultivate", key).request_rest(key, send_cb)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 活动结束后恢复修炼/闭关失败: key={key} err={e}")

    async def _refresh_activity_owner(self, key: str) -> Optional[str]:







        owner = self._activity_owner.get(key)
        if not owner:
            return None
        if await self._is_activity_state_active(key, owner):
            return owner
        self._activity_owner.pop(key, None)
        return None

    def _official_command_body(self, key: str, text: str) -> str:

        text = str(text or "").strip()
        official_qq = str(self._official_qq_for_key(key)) if key else str(self.default_official_qq or OFFICIAL_BOT_QQ_DEFAULT)
        if official_qq:
            text = re.sub(rf"^@{re.escape(official_qq)}\s*", "", text).strip()
        return text

    def _is_exit_command_text(self, key: str, text: str) -> bool:
        body = self._official_command_body(key, text)
        return body in {"出关", "宗门出关"}

    def _is_status_or_rest_command_text(self, key: str, text: str) -> bool:
        body = self._official_command_body(key, text)
        return body in {"我的状态", "修炼", "闭关", "宗门闭关", "出关", "宗门出关"}

    def _seclusion_guard_settings(self, key: str) -> Dict[str, Any]:
        cfg = dict(self._effective_account_config(self._self_id_from_key(key)).get("seclusion_guard", {}) or {})
        return {
            "enabled": bool(cfg.get("enabled", getattr(self, "seclusion_guard_enabled", True))),
            "prefer_sect_exit": bool(cfg.get("prefer_sect_exit", getattr(self, "seclusion_guard_prefer_sect_exit", True))),
            "retry_delay_sec": max(0.0, float(cfg.get("retry_delay_sec", getattr(self, "seclusion_guard_retry_delay_sec", 1.5)))),
            "max_exit_attempts": max(1, int(cfg.get("max_exit_attempts", getattr(self, "seclusion_guard_max_attempts", 2)))),
        }

    def _detect_seclusion_block_mode(self, key: str, text: str) -> Optional[str]:

        if not self._seclusion_guard_settings(key)["enabled"]:
            return None
        text = str(text or "")


        if any(k in text for k in ("预计闭关时长", "闭关入定")):
            return None

        if "走火入魔" in text and "闭关" in text:
            return "sect" if "宗门" in text else "normal"
        if "正在宗门闭关" in text or "宗门闭关" in text and any(k in text for k in ("正在", "现在", "小心")):
            return "sect"
        if "正在闭关" in text or "闭关中" in text:
            return "normal"
        return None

    def _detect_exit_result(self, text: str) -> str:

        text = str(text or "")
        success_keywords = (
            "修为突破", "出关捷报", "闭关结算", "成功出关", "已经出关", "已出关",
            "结束闭关", "结束了闭关", "破关而出", "出关了", "本次闭关",
        )
        if any(k in text for k in success_keywords):
            return "success"


        fallback_keywords = (
            "不在宗门闭关", "未在宗门闭关", "没有在宗门闭关", "并未在宗门闭关",
            "不是宗门闭关", "当前没有宗门闭关",
        )
        if any(k in text for k in fallback_keywords):
            return "fallback_normal"


        idle_keywords = (
            "道友现在什么都没干", "现在什么都没干", "什么都没干",
            "不在闭关", "未在闭关", "没有在闭关", "并未闭关", "当前没有闭关",
            "道友还没有闭关", "无需出关",
        )
        if any(k in text for k in idle_keywords):
            return "already_idle"
        return "none"

    def _remember_official_command_sent(self, key: str, text: str) -> None:

        if not self._seclusion_guard_settings(key)["enabled"]:
            return
        body = self._official_command_body(key, text)
        if not body:
            self._last_official_command.pop(key, None)
            return



        if self._is_status_or_rest_command_text(key, text):
            self._last_official_command.pop(key, None)
            return
        self._last_official_command[key] = text

    def _queue_seclusion_pending(self, key: str, text: str) -> None:
        text = str(text or "").strip()
        if not text or self._is_exit_command_text(key, text):
            return
        queue = self._seclusion_pending_commands.setdefault(key, [])
        if text not in queue:
            queue.append(text)

    async def _replay_seclusion_pending(self, key: str) -> None:
        pendings = self._seclusion_pending_commands.pop(key, [])
        self._seclusion_exit_attempts.pop(key, None)
        self._seclusion_exit_mode.pop(key, None)
        self._last_official_command.pop(key, None)
        if not pendings:
            return
        retry_delay = self._seclusion_guard_settings(key)["retry_delay_sec"]
        if retry_delay > 0:
            await asyncio.sleep(retry_delay)
        for text in pendings:
            await self._enqueue_official_command(key, text)
        logger.info(f"[xiao_xiuxian_auto] {key} 已出关，重放被闭关拦截的任务 {len(pendings)} 条")

    async def _send_seclusion_exit(self, key: str, mode: str) -> None:
        attempts = self._seclusion_exit_attempts.get(key, 0)
        if attempts >= self._seclusion_guard_settings(key)["max_exit_attempts"]:
            logger.warning(f"[xiao_xiuxian_auto] {key} 闭关出关尝试已达上限，暂停重放任务")
            return
        self._seclusion_exit_attempts[key] = attempts + 1
        self._seclusion_exit_mode[key] = mode


        try:
            await self._controller("cultivate", key).mark_activity_exit_requested(key)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 同步闭关出关状态失败: key={key} err={e}")
        cmd = "宗门出关" if mode == "sect" else "出关"
        official_qq = self._official_qq_for_key(key)
        await self._enqueue_official_command(key, f"@{official_qq} {cmd}")
        logger.info(f"[xiao_xiuxian_auto] {key} 检测到闭关拦截，已发送 {cmd}，等待回执后恢复任务")

    async def _handle_seclusion_guard_text(self, key: str, text: str) -> bool:

        settings = self._seclusion_guard_settings(key)
        if not settings["enabled"]:
            return False
        text = str(text or "")

        block_mode = self._detect_seclusion_block_mode(key, text)
        if block_mode:
            blocked_cmd = self._last_official_command.get(key)


            if not blocked_cmd or self._is_status_or_rest_command_text(key, blocked_cmd):
                self._last_official_command.pop(key, None)
                return False
            self._queue_seclusion_pending(key, blocked_cmd)
            mode = "sect" if (block_mode == "sect" and settings["prefer_sect_exit"]) else "normal"
            await self._send_seclusion_exit(key, mode)
            return True

        if key not in self._seclusion_pending_commands:
            return False

        result = self._detect_exit_result(text)
        if result in {"success", "already_idle"}:
            await self._replay_seclusion_pending(key)
            return True

        if result == "fallback_normal":

            self._seclusion_exit_attempts[key] = max(0, self._seclusion_exit_attempts.get(key, 0) - 1)
            await self._send_seclusion_exit(key, "normal")
            return True

        return False

    async def _enqueue_official_command(self, key: str, text: str) -> None:

        if self._is_key_send_blocked(key):
            return
        lock = self._send_locks.setdefault(key, asyncio.Lock())
        async with lock:
            queue = self._send_queues.setdefault(key, [])
            queue.append(text)
            task = self._send_tasks.get(key)
            if task is None or task.done():
                self._send_tasks[key] = asyncio.create_task(self._send_worker(key))
        try:
            await self.send_stats.record(self._self_id_from_key(key), text)
        except Exception as e:
            logger.warning(f"[xiao_xiuxian_auto] 记录每日发送统计失败: key={key} err={e}")

    async def _pick_next_command_index(self, key: str, snapshot: List[str]) -> Optional[int]:








        if not snapshot:
            return None

        owner = await self._refresh_activity_owner(key)


        first_module = self._activity_module_of(snapshot[0])
        if first_module is None:
            return 0

        if owner:
            for i, text in enumerate(snapshot):
                module = self._activity_module_of(text)
                if module is None or module == owner:
                    return i
            return None


        best_i: Optional[int] = None
        best_rank = 10 ** 9
        for i, text in enumerate(snapshot):
            module = self._activity_module_of(text)
            if module is None:

                continue
            rank = self._activity_priority.get(module, 10 ** 6)
            if rank < best_rank:
                best_rank = rank
                best_i = i
        return best_i

    async def _send_worker(self, key: str) -> None:



        await asyncio.sleep(0.05)

        try:
            while True:
                if self._captcha_for_key(key).is_paused(key):
                    await asyncio.sleep(0.5)
                    continue
                lock = self._send_locks.setdefault(key, asyncio.Lock())
                async with lock:
                    snapshot = list(self._send_queues.get(key, []))

                if not snapshot:
                    break

                idx = await self._pick_next_command_index(key, snapshot)
                if idx is None:

                    await asyncio.sleep(max(self.command_delay_sec, 0.5))
                    continue

                async with lock:
                    queue = self._send_queues.get(key, [])
                    if idx >= len(queue):
                        continue
                    text = queue.pop(idx)

                module = self._activity_module_of(text)
                if module:
                    owner = await self._refresh_activity_owner(key)
                    if owner is None:
                        self._activity_owner[key] = module
                        logger.info(f"[xiao_xiuxian_auto] {key} 玩法队列启动：{module}")

                self._remember_official_command_sent(key, text)


                if module:
                    try:
                        await self._controller("cultivate", key).mark_activity_exit_requested(key)
                    except Exception as e:
                        logger.warning(f"[xiao_xiuxian_auto] 标记活动征用休息状态失败: key={key} err={e}")
                await self._raw_send_by_key(key, text)


                if self.command_delay_sec > 0:
                    await asyncio.sleep(self.command_delay_sec)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[xiao_xiuxian_auto] 指令队列异常 key={key}: {e}")
        finally:

            lock = self._send_locks.setdefault(key, asyncio.Lock())
            async with lock:
                has_more = bool(self._send_queues.get(key))
                current = asyncio.current_task()
                if not has_more and self._send_tasks.get(key) is current:
                    self._send_tasks.pop(key, None)
                elif has_more and (self._send_tasks.get(key) is current):
                    self._send_tasks[key] = asyncio.create_task(self._send_worker(key))

    async def _handle_captcha(self, key: str, event, raw_text: str) -> bool:
        guard = self._captcha_for_key(key)

        async def notify(message: str) -> None:
            await self._raw_send_by_key(key, message)

        async def click(payload: Dict[str, str]):
            self_id = self._self_id_from_key(key)
            client = self._find_client_by_self_id(self_id)
            if client is None:
                raise RuntimeError("未找到 OneBot 客户端")
            result = await self._do_send_action(client, "click_inline_keyboard_button", payload)
            if not is_click_action_accepted(result):
                raise RuntimeError(f"OneBot 未明确接受点击请求：{result}")
            return result

        return await guard.handle(key, event, raw_text, self._self_id_from_key(key), notify, click)

    async def _raw_send_by_key(self, key: str, text: str) -> None:

        key = str(key)
        if self._is_key_send_blocked(key):
            return
        try:
            self_id, group_id = key.split(":", 1)
        except ValueError:
            return
        client = self._find_client_by_self_id(self_id)
        if client is None:
            logger.warning(f"[xiao_xiuxian_auto] 未找到 self_id={self_id} 的客户端，无法发送: {text}")
            return
        payload = self._build_message(text)
        result = await self._do_send(client, group_id, payload, self_id=self_id)
        if result is True:
            return
        if self.auto_block_permanent_send_error and self._is_permanent_send_error(result):
            await self._mark_send_blocked(key, result)
        elif result:
            logger.warning(f"[xiao_xiuxian_auto] 发送消息失败: {result}")

    async def _do_send_action(self, client, action: str, payload: Dict[str, Any]):
        for target in (getattr(client, "call_action", None), getattr(getattr(client, "api", None), "call_action", None)):
            if target is None:
                continue
            try:
                return await target(action, **payload)
            except Exception as exc:
                last_error = exc
        return locals().get("last_error", RuntimeError("没有可用的 OneBot action 接口"))

    async def _do_send(self, client, group_id, payload, self_id: Optional[str] = None):
        group_id = str(group_id)
        if group_id.startswith("private:"):
            user_id = int(group_id.split(":", 1)[1])
            action, kw = "send_private_msg", {"user_id": user_id, "message": payload}
        else:
            action, kw = "send_group_msg", {"group_id": int(group_id), "message": payload}
        normalized_self_id = _normalize_qq_id(self_id)
        if normalized_self_id:
            kw["self_id"] = int(normalized_self_id)
        candidates = [
            ("call_action", getattr(client, "call_action", None)),
            ("call_action", getattr(getattr(client, "api", None), "call_action", None)),
            ("direct", getattr(client, action, None)),
        ]
        last_error = None
        for kind, fn in candidates:
            if fn is None: continue
            try:
                if kind == "direct": await fn(**kw)
                else: await fn(action, **kw)
                return True
            except Exception as e:
                last_error = e
                continue
        if last_error:
            return last_error
        return RuntimeError("没有可用的发送接口")

    def _find_client_by_self_id(self, self_id: str):
        target_self_id = _normalize_qq_id(self_id)
        if not target_self_id:
            return None
        if bot := self._cached_bots.get(target_self_id):
            return bot

        candidates = []
        candidate_ids = set()

        def remember_candidate(candidate) -> None:
            if candidate is None or id(candidate) in candidate_ids:
                return
            candidate_ids.add(id(candidate))
            candidates.append(candidate)

        remember_candidate(self._any_bot)
        try:
            for plat in _get_platform_list(self.context):
                if bot := _dig_bot(plat):
                    if isinstance(bot, dict):
                        for sid_hint, candidate in bot.items():
                            remember_candidate(candidate)
                            candidate_self_id = _normalize_qq_id(sid_hint)
                            if not candidate_self_id:
                                candidate_self_id = _normalize_qq_id(getattr(candidate, "self_id", None))
                            if not candidate_self_id:
                                candidate_self_id = _normalize_qq_id(getattr(candidate, "qq", None))
                            if candidate_self_id:
                                self._cached_bots[candidate_self_id] = candidate
                            if candidate_self_id == target_self_id:
                                return candidate
                    else:
                        remember_candidate(bot)
                        candidate_self_id = _normalize_qq_id(getattr(bot, "self_id", None))
                        if not candidate_self_id:
                            candidate_self_id = _normalize_qq_id(getattr(bot, "qq", None))
                        if candidate_self_id:
                            self._cached_bots[candidate_self_id] = bot
                        if candidate_self_id == target_self_id:
                            return bot
        except Exception as e:
            logger.debug(f"[xiao_xiuxian_auto] 枚举 OneBot 客户端失败: {e}")

        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _build_message(text: str):
        m = re.match(r"@(\d+)\s+(.+)", text)
        if m: return [{"type": "at", "data": {"qq": m.group(1)}}, {"type": "text", "data": {"text": " " + m.group(2)}}]
        return [{"type": "text", "data": {"text": text}}]

    def _hook_native_self_message(self) -> bool:
        try:
            plats = _get_platform_list(self.context)
            if not plats: return False
            hooked_any = False
            already_hooked = False
            hooked_self = False
            hooked_official = False
            for plat in plats:
                cls_name = plat.__class__.__name__.lower()
                if "aiocqhttp" not in cls_name and "onebot" not in cls_name: continue
                raw_bot = _dig_bot(plat)
                if not raw_bot: continue



                if isinstance(raw_bot, dict):
                    bot_items = list(raw_bot.items())
                else:
                    sid = _normalize_qq_id(getattr(raw_bot, "self_id", None))
                    if not sid:
                        sid = _normalize_qq_id(getattr(raw_bot, "qq", None))
                    bot_items = [(sid, raw_bot)]

                for sid_hint, bot in bot_items:
                    if not bot:
                        continue
                    self._any_bot = bot
                    sid = _normalize_qq_id(sid_hint)
                    if not sid:
                        sid = _normalize_qq_id(getattr(bot, "self_id", None))
                    if not sid:
                        sid = _normalize_qq_id(getattr(bot, "qq", None))
                    bot_key = sid or str(id(bot))
                    if bot_key in self._native_hooked_bot_ids:
                        already_hooked = True
                        continue
                    if sid:
                        self._cached_bots[sid] = bot

                    @bot.on_message("group")
                    async def _on_grp_msg(event, _plat=plat, _bot=bot, _sid_hint=sid):
                        self._remember_bot(event, _bot, _sid_hint)
                        _sid, _uid, _, _ = self._get_base_info(event, bot=_bot, sid_hint=_sid_hint)
                        _is_self = bool(_sid and _uid == _sid)
                        await self._handle_native_self(event, force_self=_is_self, sid_hint=_sid_hint, bot=_bot)
                        if not _is_self:
                            await self._handle_native_official(event, sid_hint=_sid_hint, bot=_bot)
                    hooked_official = True

                    @bot.on_message("private")
                    async def _on_pri_msg(event, _plat=plat, _bot=bot, _sid_hint=sid):
                        self._remember_bot(event, _bot, _sid_hint)
                        _sid, _uid, _, _ = self._get_base_info(event, bot=_bot, sid_hint=_sid_hint)
                        _is_self = bool(_sid and _uid == _sid)
                        await self._handle_native_self(event, force_self=_is_self, sid_hint=_sid_hint, bot=_bot)
                        if not _is_self:
                            await self._handle_native_official(event, sid_hint=_sid_hint, bot=_bot)
                    hooked_official = True

                    try:
                        @bot.on("message_sent")
                        async def _on_self_msg(event, _plat=plat, _bot=bot, _sid_hint=sid):
                            self._remember_bot(event, _bot, _sid_hint)
                            await self._handle_native_self(event, force_self=True, sid_hint=_sid_hint, bot=_bot)
                        hooked_self = True
                    except Exception as e:
                        logger.debug(f"[xiao_xiuxian_auto] 当前 OneBot 客户端不支持 message_sent 钩子: {e}")

                    self._native_hooked_bot_ids.add(bot_key)
                    hooked_any = True
                    logger.info(f"[xiao_xiuxian_auto] 原生拦截器已生效 ({plat.__class__.__name__}, self_id={sid or 'unknown'})")
            if hooked_any:
                self._native_hooked = True
                self._native_self_hooked = self._native_self_hooked or hooked_self
                self._native_official_hooked = self._native_official_hooked or hooked_official
            return hooked_any or already_hooked
        except Exception as e:
            logger.exception(f"原生钩子挂载失败: {e}")
            return False

    def _remember_bot(self, event, bot, sid_hint: Optional[str] = None):
        if bot is None:
            return
        sid = _normalize_qq_id(event_field(event, "self_id"))
        if not sid:
            sid = _normalize_qq_id(getattr(bot, "self_id", None))
        if not sid:
            sid = _normalize_qq_id(getattr(bot, "qq", None))
        if not sid:
            sid = _normalize_qq_id(sid_hint)
        if sid:
            self._cached_bots[sid] = bot
        self._any_bot = bot

    def _get_base_info(self, event, bot=None, sid_hint: Optional[str] = None):
        sid = _normalize_qq_id(event_field(event, "self_id"))
        if not sid and bot is not None:
            sid = _normalize_qq_id(getattr(bot, "self_id", None))
        if not sid and bot is not None:
            sid = _normalize_qq_id(getattr(bot, "qq", None))
        if not sid:
            sid = _normalize_qq_id(sid_hint)
        uid = _normalize_qq_id(event_field(event, "user_id"))
        gid = event_field(event, "group_id")
        if gid is not None:
            gid = str(gid)
        post_type = str(event_field(event, "post_type", "") or "")
        return sid, uid, gid, post_type

    def _claim_official_event(self, event, self_id: str, group_id, text: str, window: float = 3.0) -> bool:
        now = time.time()
        recent = getattr(self, "_recent_official_events", None)
        if not isinstance(recent, dict):
            recent = {}
            self._recent_official_events = recent
        for fingerprint, timestamp in list(recent.items()):
            if now - timestamp > window:
                recent.pop(fingerprint, None)
        fingerprint = event_fingerprint(event, self_id, group_id, text)
        timestamp = recent.get(fingerprint)
        if timestamp is not None and now - timestamp <= window:
            return False
        recent[fingerprint] = now
        return True

    def _self_cmd_sig(self, self_id: str, group_id, text: str) -> str:
        return f"{self_id}:{group_id}:{text}"

    def _mark_self_command_seen(self, self_id: str, group_id, text: str) -> None:
        now = time.time()

        for k, ts in list(self._recent_self_commands.items()):
            if now - ts > 5.0:
                self._recent_self_commands.pop(k, None)
        self._recent_self_commands[self._self_cmd_sig(self_id, group_id, text)] = now

    def _is_recent_self_command_seen(self, self_id: str, group_id, text: str, window: float = 1.2) -> bool:
        ts = self._recent_self_commands.get(self._self_cmd_sig(self_id, group_id, text))
        return bool(ts and (time.time() - ts) <= window)

    async def _handle_native_self(self, event, force_self: bool = False, sid_hint: Optional[str] = None, bot=None):
        try:
            self_id, user_id, group_id, post_type = self._get_base_info(event, bot=bot, sid_hint=sid_hint)
            if not ((user_id == self_id) or force_self or (post_type == "message_sent")): return

            text = extract_pure_text(event)
            if not text: return
            text = text.strip()
            if self._is_recent_self_command_seen(self_id, group_id, text):
                return
            self._mark_self_command_seen(self_id, group_id, text)

            if text in ("绑定列表", "账号配置", "多账号状态"):
                send_cb_local = self._make_send_cb(f"{self_id}:{group_id}") if group_id else None
                msg = await self.cmd_account_bind_status(self_id if text != "多账号状态" else None)
                if send_cb_local: await send_cb_local(msg)
                return

            if text in ("绑定此群", "更改绑定"):
                if not group_id: return
                send_cb_local = self._make_send_cb(f"{self_id}:{group_id}")
                old_groups = await self._get_bound_groups(self_id)
                await self._set_bound(self_id, group_id, replace=(text == "更改绑定"))
                new_groups = await self._get_bound_groups(self_id)
                if text == "更改绑定":
                    msg = f"🔁 绑定已更改\n原绑定：{'、'.join(old_groups) if old_groups else '无'}\n新绑定：{group_id}"
                else:
                    msg = f"✅ 已绑定本群({group_id})\n本账号当前绑定群：{'、'.join(new_groups)}"
                if send_cb_local: await send_cb_local(msg)
                return

            if text == "解绑此群":
                if not group_id: return
                send_cb_local = self._make_send_cb(f"{self_id}:{group_id}")
                ok = await self._remove_bound(self_id, group_id)
                msg = f"✅ 已解绑本群({group_id})" if ok else f"ℹ️ 本群({group_id})不是运行时绑定群；如来自 config.json 预设，请修改配置文件。"
                if send_cb_local: await send_cb_local(msg)
                return

            if not await self._is_bound_match(self_id, group_id): return

            key = f"{self_id}:{group_id}"
            self._known_keys.add(key)
            send_cb = self._make_send_cb(key)
            reply = ""

            if text == "开启悬赏": reply = await self.bounty.cmd_enable(key, send_cb)
            elif text == "关闭悬赏": reply = await self.bounty.cmd_disable(key)
            elif text == "开启秘境": reply = await self.secret.cmd_enable(key, send_cb)
            elif text == "关闭秘境": reply = await self.secret.cmd_disable(key)
            elif text == "开启签到": reply = await self.routine.cmd_enable_signin(key, send_cb)
            elif text == "关闭签到": reply = await self.routine.cmd_disable_signin(key)
            elif text == "开启领丹": reply = await self.routine.cmd_enable_pill(key, send_cb)
            elif text == "关闭领丹": reply = await self.routine.cmd_disable_pill(key)
            elif text == "开启挖矿": reply = await self.routine.cmd_enable_mine(key, send_cb)
            elif text == "关闭挖矿": reply = await self.routine.cmd_disable_mine(key)
            elif text == "开启灵田": reply = await self.routine.cmd_enable_farm(key, send_cb)
            elif text == "关闭灵田": reply = await self.routine.cmd_disable_farm(key)
            elif text == "开启宗门任务": reply = await self.sect.cmd_enable(key, send_cb)
            elif text == "关闭宗门任务": reply = await self.sect.cmd_disable(key)
            elif text == "宗门任务状态": reply = await self.sect.cmd_status(key)
            elif text.startswith("宗门任务时间"): reply = await self.sect.cmd_set_time(key, text.replace("宗门任务时间", "").strip())
            elif text.startswith("开启宗门任务"): reply = await self.sect.cmd_toggle_task(key, text.replace("开启宗门任务", "").strip(), True)
            elif text.startswith("关闭宗门任务"): reply = await self.sect.cmd_toggle_task(key, text.replace("关闭宗门任务", "").strip(), False)
            elif text == "开启修炼":    reply = await self.cultivate.cmd_enable(key, MODE_CULTIVATE, send_cb)
            elif text == "关闭修炼":    reply = await self.cultivate.cmd_disable(key, MODE_CULTIVATE, send_cb)
            elif text == "开启闭关":    reply = await self.cultivate.cmd_enable(key, MODE_SECLUSION, send_cb)
            elif text == "关闭闭关":    reply = await self.cultivate.cmd_disable(key, MODE_SECLUSION, send_cb)
            elif text == "开启宗门闭关": reply = await self.cultivate.cmd_enable(key, MODE_SECT_SECLUSION, send_cb)
            elif text == "关闭宗门闭关": reply = await self.cultivate.cmd_disable(key, MODE_SECT_SECLUSION, send_cb)
            elif text == "查询气血":         reply = await self.cultivate.cmd_check_hp(key, send_cb)
            elif text == "宗门任务接取":
                st = await self.sect._get(key)
                if not st.enabled:
                    reply = await self.sect.cmd_enable(key, send_cb)
                    if reply and send_cb: await send_cb(reply)
                    return
            elif text == "统计": reply = await self.bounty.cmd_stats(key)
            elif text == "开启炼丹": reply = await self.auto_alchemy.cmd_start(key, send_cb)
            elif text == "开启背包炼丹": reply = await self.auto_alchemy.cmd_backpack(key, send_cb)
            elif text.startswith("炼丹 "): reply = await self.auto_alchemy.cmd_target(key, text.replace("炼丹", "", 1).strip(), send_cb)
            elif text == "暂停炼丹": reply = await self.auto_alchemy.cmd_pause(key)
            elif text == "继续炼丹": reply = await self.auto_alchemy.cmd_resume(key, send_cb)
            elif text == "关闭炼丹": reply = await self.auto_alchemy.cmd_stop(key)
            elif text == "炼丹状态": reply = await self.auto_alchemy.cmd_status(key)
            elif text == "开启购买药材": reply = await self.auto_alchemy.cmd_auto_buy_herbs_start(key, 1, send_cb)
            elif text.startswith("开启购买药材 "):
                try:
                    _rounds = int(text.replace("开启购买药材", "", 1).strip())
                except Exception:
                    _rounds = 1
                reply = await self.auto_alchemy.cmd_auto_buy_herbs_start(key, _rounds, send_cb)
            elif text == "关闭购买药材": reply = await self.auto_alchemy.cmd_auto_buy_herbs_stop(key)
            elif text == "开启动态购买": reply = await self._controller("auto_alchemy", key).cmd_toggle_dynamic_buy(True)
            elif text == "关闭动态购买": reply = await self._controller("auto_alchemy", key).cmd_toggle_dynamic_buy(False)
            elif text == "开启灵界升级": reply = await self.linjie.cmd_enable(key, send_cb)
            elif text == "关闭灵界升级": reply = await self.linjie.cmd_disable(key)
            elif text == "灵界状态": reply = await self.linjie.cmd_status(key)
            elif text == "灵界规划": reply = await self.linjie.cmd_plan(key, send_cb)
            elif text == "灵界刷新规划": reply = await self.linjie.cmd_refresh_plan(key, send_cb)
            elif text == "灵界规划详情": reply = await self.linjie.cmd_plan_detail(key, send_cb)
            elif text == "灵界规划序列": reply = await self.linjie.cmd_plan_sequence(key, send_cb)
            elif text == "开启真元检测": reply = await self.endless.cmd_enable_mp_check(key)
            elif text == "关闭真元检测": reply = await self.endless.cmd_disable_mp_check(key)
            elif text.startswith("设置真元检测"):
                reply = await self.endless.cmd_set_mp_threshold(key, text.replace("设置真元检测", "", 1).strip())
            elif text.startswith("开启无尽"):
                reply = await self.endless.cmd_enable(key, text.replace("开启无尽", "", 1).strip(), send_cb)
            elif text == "关闭无尽": reply = await self.endless.cmd_disable(key)
            elif text == "无尽状态": reply = await self.endless.cmd_status(key)
            elif text.startswith("一键上架"):
                reply = await self.inventory_ops.cmd_start_market(key, text.replace("一键上架", "", 1).strip(), send_cb)
            elif text.startswith("一键炼金"):
                reply = await self.inventory_ops.cmd_start_alchemy(key, text.replace("一键炼金", "", 1).strip(), send_cb)
            elif text in ("炼金名单", "炼金白名单", "炼金黑名单"):
                reply = self._controller("inventory_ops", key).list_rules()
            elif text.startswith("添加炼金白名单"):
                _args = text.replace("添加炼金白名单", "", 1).strip().split()
                reply = self._controller("inventory_ops", key).add_whitelist(_args[0] if _args else "丹药", _args[1:] if len(_args) > 1 else [])
            elif text.startswith("删除炼金白名单"):
                _args = text.replace("删除炼金白名单", "", 1).strip().split()
                reply = self._controller("inventory_ops", key).remove_whitelist(_args[0] if _args else "丹药", _args[1:] if len(_args) > 1 else [])
            elif text.startswith("添加炼金黑名单"):
                _args = text.replace("添加炼金黑名单", "", 1).strip().split()
                reply = self._controller("inventory_ops", key).add_blacklist(_args[0] if _args else "装备", _args[1:] if len(_args) > 1 else [])
            elif text.startswith("删除炼金黑名单"):
                _args = text.replace("删除炼金黑名单", "", 1).strip().split()
                reply = self._controller("inventory_ops", key).remove_blacklist(_args[0] if _args else "装备", _args[1:] if len(_args) > 1 else [])
            elif text in ("坊市价格状态", "价格状态", "坊市状态", "价格中心状态", "计算中心状态"):
                reply = await self.market_price.summary()
            elif text in ("刷新坊市价格", "更新坊市价格", "刷新价格中心", "刷新计算中心"):
                ok = await self.market_price.refresh(force=True)
                summary = await self.market_price.summary()
                reply = ("✅ 价格中心已刷新\n" if ok else "⚠️ 价格中心刷新失败或无可用数据\n") + summary
            elif text in ("开启价格中心", "开启计算中心", "开启坊市价格"):
                reply = await self.cmd_enable_price_center()
            elif text in ("关闭价格中心", "关闭计算中心", "关闭坊市价格"):
                reply = await self.cmd_disable_price_center()
            elif text in ("默认价格中心", "重置价格中心", "恢复默认价格中心", "默认计算中心", "重置计算中心"):
                reply = await self.cmd_reset_price_center_url()
            elif text.startswith("设置价格中心地址"):
                reply = await self.cmd_set_price_center_url(text.replace("设置价格中心地址", "", 1).strip())
            elif text.startswith("设置计算中心地址"):
                reply = await self.cmd_set_price_center_url(text.replace("设置计算中心地址", "", 1).strip())
            elif text.startswith("设置坊市价格地址"):
                reply = await self.cmd_set_price_center_url(text.replace("设置坊市价格地址", "", 1).strip())
            elif text.startswith("设置价格中心密钥"):
                reply = await self.cmd_set_price_center_key(text.replace("设置价格中心密钥", "", 1).strip())
            elif text.startswith("设置计算中心密钥"):
                reply = await self.cmd_set_price_center_key(text.replace("设置计算中心密钥", "", 1).strip())
            elif text == "继续任务":
                self._captcha_for_key(key).resume(key)
                reply = "▶️ 已解除验证码暂停，自动任务将按原队列继续。"
            elif text == "验证码状态":
                pause = self._captcha_for_key(key).status(key)
                reply = f"验证码守卫：{'⏸️暂停' if pause.active else '✅正常'}\n原因：{pause.reason or '无'}\n阶段：{pause.phase}\n消息序号：{pause.msg_seq or '无'}"
            elif text in ("任务状态", "修仙状态"): reply = await self.cmd_task_status(key)
            elif text in ("悬赏修为", "悬赏价值", "悬赏耗时"):
                reply = await self.bounty.cmd_set_strategy(key, text.replace("悬赏", "", 1).strip())
            elif text.startswith("修仙菜单"):
                sub_menu = text.replace("修仙菜单", "").strip()
                reply = await self.cmd_menu(key, sub_menu)

            if reply and send_cb: await send_cb(reply)
        except Exception as e:
            logger.exception(f"处理自身原生消息异常: {e}")

    async def _handle_native_official(self, event, sid_hint: Optional[str] = None, bot=None):
        await self._handle_official_event(event, sid_hint=sid_hint, bot=bot)

    async def _handle_official_event(self, event, sid_hint: Optional[str] = None, bot=None):
        try:
            self_id, user_id, group_id, _ = self._get_base_info(event, bot=bot, sid_hint=sid_hint)
            official_qq = self._official_qq_for_self(self_id)
            if str(user_id) != str(official_qq): return

            text = extract_pure_text(event)
            raw_text = extract_raw_text(event) or text
            if not text and not raw_text: return
            if not self._claim_official_event(event, self_id, group_id, raw_text or text): return
            if not await self._is_bound_match(self_id, group_id): return

            key = f"{self_id}:{group_id}"
            self._known_keys.add(key)
            send_cb = self._make_send_cb(key)

            if await self._handle_captcha(key, event, raw_text):
                return

            handled_auto_alchemy = await self.auto_alchemy.on_official_text(key, raw_text, send_cb)
            handled_inventory = False
            if not handled_auto_alchemy:
                handled_inventory = await self.inventory_ops.on_official_text(key, text, send_cb)
            if not handled_auto_alchemy and not handled_inventory:
                await self.bounty.on_official_text(key, text, send_cb)
                await self.secret.on_official_text(key, text, send_cb)
                await self.routine.on_official_text(key, text, send_cb)
                await self.sect.on_official_text(key, text, send_cb)
                await self.cultivate.on_official_text(key, text, send_cb)
                await self.linjie.on_official_text(key, text, send_cb)
                await self.endless.on_official_text(key, text, send_cb)

            await self._handle_seclusion_guard_text(key, text)




            await self._maybe_restore_rest_after_activities_done(key, send_cb)

        except Exception as e:
            logger.exception(f"处理官方消息异常: {e}")




    def _is_self(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) == str(event.message_obj.self_id)

    def _is_official(self, event: AstrMessageEvent) -> bool:
        sid = str(getattr(event.message_obj, "self_id", ""))
        return str(event.get_sender_id()) == str(self._official_qq_for_self(sid))

    def _key_of_astr_event(self, event: AstrMessageEvent) -> str:
        sid = str(event.message_obj.self_id)
        gid = getattr(event.message_obj, "group_id", None)
        return f"{sid}:{gid}" if gid else f"{sid}:private:{event.get_sender_id()}"

    @filter.regex(r"^(绑定此群|更改绑定|解绑此群|绑定列表|账号配置|多账号状态|开启悬赏|关闭悬赏|悬赏(修为|价值|耗时)|统计|开启秘境|关闭秘境|开启签到|关闭签到|开启领丹|关闭领丹|开启挖矿|关闭挖矿|开启灵田|关闭灵田|开启宗门任务|关闭宗门任务|宗门任务状态|宗门任务接取|宗门任务时间.*|开启宗门任务.*|关闭宗门任务.*|开启修炼|关闭修炼|开启闭关|关闭闭关|开启宗门闭关|关闭宗门闭关|查询气血|坊市价格状态|价格状态|坊市状态|价格中心状态|计算中心状态|刷新坊市价格|更新坊市价格|刷新价格中心|刷新计算中心|开启价格中心|关闭价格中心|开启计算中心|关闭计算中心|开启坊市价格|关闭坊市价格|默认价格中心|重置价格中心|恢复默认价格中心|默认计算中心|重置计算中心|设置价格中心地址.*|设置计算中心地址.*|设置坊市价格地址.*|设置价格中心密钥.*|设置计算中心密钥.*|开启炼丹|开启背包炼丹|炼丹 .+|暂停炼丹|继续炼丹|关闭炼丹|炼丹状态|开启购买药材(?:\s+\d+)?|关闭购买药材|开启动态购买|关闭动态购买|开启灵界升级|关闭灵界升级|灵界状态|灵界规划|灵界刷新规划|灵界规划详情|灵界规划序列|开启真元检测|关闭真元检测|设置真元检测.*|开启无尽(?:\s+\d+)?|关闭无尽|无尽状态|一键上架(药材|装备|神物|丹药)|一键炼金(药材|装备|神物|丹药)|炼金名单|炼金白名单|炼金黑名单|添加炼金白名单.*|删除炼金白名单.*|添加炼金黑名单.*|删除炼金黑名单.*|继续任务|验证码状态|任务状态|修仙状态|修仙菜单.*)$")
    async def on_self_command(self, event: AstrMessageEvent):



        if not self._is_self(event): return
        text = extract_pure_text(event)
        if not text: return
        text = text.strip()

        self_id = str(event.message_obj.self_id)
        group_id = getattr(event.message_obj, "group_id", None)
        if group_id is not None: group_id = str(group_id)
        if self._is_recent_self_command_seen(self_id, group_id, text):
            return
        self._mark_self_command_seen(self_id, group_id, text)

        if text in ("绑定列表", "账号配置", "多账号状态"):
            reply = await self.cmd_account_bind_status(self_id if text != "多账号状态" else None)
            yield event.plain_result(reply)
            return

        if text in ("绑定此群", "更改绑定"):
            if not group_id: return
            old_groups = await self._get_bound_groups(self_id)
            await self._set_bound(self_id, group_id, replace=(text == "更改绑定"))
            new_groups = await self._get_bound_groups(self_id)
            if text == "更改绑定":
                msg = f"🔁 绑定已更改\n原绑定：{'、'.join(old_groups) if old_groups else '无'}\n新绑定：{group_id}"
            else:
                msg = f"✅ 已绑定本群({group_id})\n本账号当前绑定群：{'、'.join(new_groups)}"
            yield event.plain_result(msg)
            return

        if text == "解绑此群":
            if not group_id: return
            ok = await self._remove_bound(self_id, group_id)
            msg = f"✅ 已解绑本群({group_id})" if ok else f"ℹ️ 本群({group_id})不是运行时绑定群；如来自 config.json 预设，请修改配置文件。"
            yield event.plain_result(msg)
            return

        if not await self._is_bound_match(self_id, group_id): return

        key = self._key_of_astr_event(event)
        self._known_keys.add(key)
        send_cb = self._make_send_cb(key)
        reply = ""

        if text == "开启悬赏": reply = await self.bounty.cmd_enable(key, send_cb)
        elif text == "关闭悬赏": reply = await self.bounty.cmd_disable(key)
        elif text == "开启秘境": reply = await self.secret.cmd_enable(key, send_cb)
        elif text == "关闭秘境": reply = await self.secret.cmd_disable(key)
        elif text == "开启签到": reply = await self.routine.cmd_enable_signin(key, send_cb)
        elif text == "关闭签到": reply = await self.routine.cmd_disable_signin(key)
        elif text == "开启领丹": reply = await self.routine.cmd_enable_pill(key, send_cb)
        elif text == "关闭领丹": reply = await self.routine.cmd_disable_pill(key)
        elif text == "开启挖矿": reply = await self.routine.cmd_enable_mine(key, send_cb)
        elif text == "关闭挖矿": reply = await self.routine.cmd_disable_mine(key)
        elif text == "开启灵田": reply = await self.routine.cmd_enable_farm(key, send_cb)
        elif text == "关闭灵田": reply = await self.routine.cmd_disable_farm(key)
        elif text == "开启宗门任务": reply = await self.sect.cmd_enable(key, send_cb)
        elif text == "关闭宗门任务": reply = await self.sect.cmd_disable(key)
        elif text == "宗门任务状态": reply = await self.sect.cmd_status(key)
        elif text.startswith("宗门任务时间"): reply = await self.sect.cmd_set_time(key, text.replace("宗门任务时间", "").strip())
        elif text.startswith("开启宗门任务"): reply = await self.sect.cmd_toggle_task(key, text.replace("开启宗门任务", "").strip(), True)
        elif text.startswith("关闭宗门任务"): reply = await self.sect.cmd_toggle_task(key, text.replace("关闭宗门任务", "").strip(), False)
        elif text == "开启修炼":    reply = await self.cultivate.cmd_enable(key, MODE_CULTIVATE, send_cb)
        elif text == "关闭修炼":    reply = await self.cultivate.cmd_disable(key, MODE_CULTIVATE, send_cb)
        elif text == "开启闭关":    reply = await self.cultivate.cmd_enable(key, MODE_SECLUSION, send_cb)
        elif text == "关闭闭关":    reply = await self.cultivate.cmd_disable(key, MODE_SECLUSION, send_cb)
        elif text == "开启宗门闭关": reply = await self.cultivate.cmd_enable(key, MODE_SECT_SECLUSION, send_cb)
        elif text == "关闭宗门闭关": reply = await self.cultivate.cmd_disable(key, MODE_SECT_SECLUSION, send_cb)
        elif text == "查询气血":         reply = await self.cultivate.cmd_check_hp(key, send_cb)
        elif text == "宗门任务接取":
            st = await self.sect._get(key)
            if not st.enabled:
                reply = await self.sect.cmd_enable(key, send_cb)
        elif text == "统计": reply = await self.bounty.cmd_stats(key)
        elif text == "开启炼丹": reply = await self.auto_alchemy.cmd_start(key, send_cb)
        elif text == "开启背包炼丹": reply = await self.auto_alchemy.cmd_backpack(key, send_cb)
        elif text.startswith("炼丹 "): reply = await self.auto_alchemy.cmd_target(key, text.replace("炼丹", "", 1).strip(), send_cb)
        elif text == "暂停炼丹": reply = await self.auto_alchemy.cmd_pause(key)
        elif text == "继续炼丹": reply = await self.auto_alchemy.cmd_resume(key, send_cb)
        elif text == "关闭炼丹": reply = await self.auto_alchemy.cmd_stop(key)
        elif text == "炼丹状态": reply = await self.auto_alchemy.cmd_status(key)
        elif text == "开启购买药材": reply = await self.auto_alchemy.cmd_auto_buy_herbs_start(key, 1, send_cb)
        elif text.startswith("开启购买药材 "):
            try:
                _rounds = int(text.replace("开启购买药材", "", 1).strip())
            except Exception:
                _rounds = 1
            reply = await self.auto_alchemy.cmd_auto_buy_herbs_start(key, _rounds, send_cb)
        elif text == "关闭购买药材": reply = await self.auto_alchemy.cmd_auto_buy_herbs_stop(key)
        elif text == "开启动态购买": reply = await self._controller("auto_alchemy", key).cmd_toggle_dynamic_buy(True)
        elif text == "关闭动态购买": reply = await self._controller("auto_alchemy", key).cmd_toggle_dynamic_buy(False)
        elif text == "开启灵界升级": reply = await self.linjie.cmd_enable(key, send_cb)
        elif text == "关闭灵界升级": reply = await self.linjie.cmd_disable(key)
        elif text == "灵界状态": reply = await self.linjie.cmd_status(key)
        elif text == "灵界规划": reply = await self.linjie.cmd_plan(key, send_cb)
        elif text == "灵界刷新规划": reply = await self.linjie.cmd_refresh_plan(key, send_cb)
        elif text == "灵界规划详情": reply = await self.linjie.cmd_plan_detail(key, send_cb)
        elif text == "灵界规划序列": reply = await self.linjie.cmd_plan_sequence(key, send_cb)
        elif text == "开启真元检测": reply = await self.endless.cmd_enable_mp_check(key)
        elif text == "关闭真元检测": reply = await self.endless.cmd_disable_mp_check(key)
        elif text.startswith("设置真元检测"):
            reply = await self.endless.cmd_set_mp_threshold(key, text.replace("设置真元检测", "", 1).strip())
        elif text.startswith("开启无尽"):
            reply = await self.endless.cmd_enable(key, text.replace("开启无尽", "", 1).strip(), send_cb)
        elif text == "关闭无尽": reply = await self.endless.cmd_disable(key)
        elif text == "无尽状态": reply = await self.endless.cmd_status(key)
        elif text.startswith("一键上架"):
            reply = await self.inventory_ops.cmd_start_market(key, text.replace("一键上架", "", 1).strip(), send_cb)
        elif text.startswith("一键炼金"):
            reply = await self.inventory_ops.cmd_start_alchemy(key, text.replace("一键炼金", "", 1).strip(), send_cb)
        elif text in ("炼金名单", "炼金白名单", "炼金黑名单"):
            reply = self._controller("inventory_ops", key).list_rules()
        elif text.startswith("添加炼金白名单"):
            _args = text.replace("添加炼金白名单", "", 1).strip().split()
            reply = self._controller("inventory_ops", key).add_whitelist(_args[0] if _args else "丹药", _args[1:] if len(_args) > 1 else [])
        elif text.startswith("删除炼金白名单"):
            _args = text.replace("删除炼金白名单", "", 1).strip().split()
            reply = self._controller("inventory_ops", key).remove_whitelist(_args[0] if _args else "丹药", _args[1:] if len(_args) > 1 else [])
        elif text.startswith("添加炼金黑名单"):
            _args = text.replace("添加炼金黑名单", "", 1).strip().split()
            reply = self._controller("inventory_ops", key).add_blacklist(_args[0] if _args else "装备", _args[1:] if len(_args) > 1 else [])
        elif text.startswith("删除炼金黑名单"):
            _args = text.replace("删除炼金黑名单", "", 1).strip().split()
            reply = self._controller("inventory_ops", key).remove_blacklist(_args[0] if _args else "装备", _args[1:] if len(_args) > 1 else [])
        elif text in ("坊市价格状态", "价格状态", "坊市状态", "价格中心状态", "计算中心状态"):
            reply = await self.market_price.summary()
        elif text in ("刷新坊市价格", "更新坊市价格", "刷新价格中心", "刷新计算中心"):
            ok = await self.market_price.refresh(force=True)
            summary = await self.market_price.summary()
            reply = ("✅ 价格中心已刷新\n" if ok else "⚠️ 价格中心刷新失败或无可用数据\n") + summary
        elif text in ("开启价格中心", "开启计算中心", "开启坊市价格"):
            reply = await self.cmd_enable_price_center()
        elif text in ("关闭价格中心", "关闭计算中心", "关闭坊市价格"):
            reply = await self.cmd_disable_price_center()
        elif text.startswith("设置价格中心地址"):
            reply = await self.cmd_set_price_center_url(text.replace("设置价格中心地址", "", 1).strip())
        elif text.startswith("设置计算中心地址"):
            reply = await self.cmd_set_price_center_url(text.replace("设置计算中心地址", "", 1).strip())
        elif text.startswith("设置坊市价格地址"):
            reply = await self.cmd_set_price_center_url(text.replace("设置坊市价格地址", "", 1).strip())
        elif text.startswith("设置价格中心密钥"):
            reply = await self.cmd_set_price_center_key(text.replace("设置价格中心密钥", "", 1).strip())
        elif text.startswith("设置计算中心密钥"):
            reply = await self.cmd_set_price_center_key(text.replace("设置计算中心密钥", "", 1).strip())
        elif text == "继续任务":
            self._captcha_for_key(key).resume(key)
            reply = "▶️ 已解除验证码暂停，自动任务将按原队列继续。"
        elif text == "验证码状态":
            pause = self._captcha_for_key(key).status(key)
            reply = f"验证码守卫：{'⏸️暂停' if pause.active else '✅正常'}\n原因：{pause.reason or '无'}\n阶段：{pause.phase}\n消息序号：{pause.msg_seq or '无'}"
        elif text in ("任务状态", "修仙状态"): reply = await self.cmd_task_status(key)
        elif text in ("悬赏修为", "悬赏价值", "悬赏耗时"):
            reply = await self.bounty.cmd_set_strategy(key, text.replace("悬赏", "", 1).strip())
        elif text.startswith("修仙菜单"):
            sub_menu = text.replace("修仙菜单", "").strip()
            reply = await self.cmd_menu(key, sub_menu)

        if reply: yield event.plain_result(reply)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_official_reply(self, event: AstrMessageEvent):
        await self._handle_official_event(event)
