# 每日发送统计实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 按账号持久化统计当天进入发送队列的坊市查看、购买和炼丹命令次数，并在 WebUI 独立页签实时显示。

**架构：** 新建 `DailySendStats` 深模块封装分类、日期滚动、并发递增和快照读取。`main.py` 只在官方命令入队后记录，并暴露账号级读取 API；WebUI 新页签负责定时刷新和响应式呈现。

**技术栈：** Python 3、`asyncio`、现有 `JsonStore`、`unittest`、原生 JavaScript/CSS。

---

## 文件结构

- 创建 `send_stats.py`：每日统计模型、命令分类、并发安全持久化。
- 创建 `tests/test_send_stats.py`：统计模块的分类、跨日、并发和账号隔离测试。
- 修改 `main.py`：初始化统计器、命令入队计数、注册并实现读取 API。
- 修改 `tests/test_state_machine_runtime.py`：验证暂停队列计数、统计失败不影响队列和 API 账号隔离。
- 修改 `pages/config/app.js`：新增发送统计页签、5 秒刷新和账号切换保持页签。
- 修改 `pages/config/style.css`：新增桌面三列与窄屏单列统计布局。
- 修改 `tests/test_core_regressions.py`：锁定 WebUI 接入、刷新和响应式样式。

### 任务 1：每日统计深模块

**文件：**
- 创建：`send_stats.py`
- 创建：`tests/test_send_stats.py`

- [ ] **步骤 1：编写分类、跨日和账号隔离的失败测试**

```python
class DailySendStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.day = "2026-07-29"
        self.stats = DailySendStats(self.store, date_provider=lambda: self.day)

    def test_classify_only_three_command_prefixes(self):
        self.assertEqual("market_view", DailySendStats.classify("@3889001741 坊市查看药材1"))
        self.assertEqual("purchase", DailySendStats.classify("@3889001741 坊市购买uuid 1"))
        self.assertEqual("alchemy", DailySendStats.classify("@3889001741 配方主药离火梧桐芝1药引炼心芝1"))
        self.assertIsNone(DailySendStats.classify("@3889001741 我的状态"))

    async def test_snapshot_isolated_by_account_and_rolls_over_date(self):
        await self.stats.record("111", "@3889001741 坊市查看药材1")
        await self.stats.record("222", "@3889001741 坊市购买abc 1")
        first = await self.stats.snapshot("111")
        self.assertEqual({"market_view": 1, "purchase": 0, "alchemy": 0}, first["counts"])
        self.assertEqual(1, first["total"])
        self.day = "2026-07-30"
        next_day = await self.stats.snapshot("111")
        self.assertEqual("2026-07-30", next_day["date"])
        self.assertEqual(0, next_day["total"])
```

- [ ] **步骤 2：运行测试确认红灯**

运行：`py -3 -m unittest tests.test_send_stats -v`

预期：ERROR，提示无法导入 `send_stats.DailySendStats`。

- [ ] **步骤 3：实现分类与规范化快照**

```python
class DailySendStats:
    STORAGE_KEY = "daily_send_stats"
    CATEGORIES = ("market_view", "purchase", "alchemy")

    def __init__(self, store, date_provider=None):
        self.store = store
        self.date_provider = date_provider or beijing_date
        self._lock = asyncio.Lock()

    @classmethod
    def classify(cls, text: str) -> Optional[str]:
        command = re.sub(r"^@\d+\s*", "", str(text or "").strip())
        if command.startswith("坊市查看"):
            return "market_view"
        if command.startswith("坊市购买"):
            return "purchase"
        if command.startswith("配方主药"):
            return "alchemy"
        return None
```

`beijing_date()` 使用 `datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")`，时区不可用时使用本地时间。内部 `_normalize(raw, today)` 仅保留当天、合法账号和三个非负整数计数；日期不匹配时返回当天空结构。

- [ ] **步骤 4：编写并发递增失败测试**

```python
async def test_concurrent_records_do_not_lose_counts(self):
    await asyncio.gather(*[
        self.stats.record("111", "@3889001741 坊市查看药材1")
        for _ in range(50)
    ])
    snapshot = await self.stats.snapshot("111")
    self.assertEqual(50, snapshot["counts"]["market_view"])
    self.assertEqual(50, snapshot["total"])
```

- [ ] **步骤 5：实现锁内读取、递增和写回**

`record(self_id, text)` 先分类，非目标命令直接返回 `False`；匹配命令在 `_lock` 中调用 `store.get`、规范化当天数据、递增账号类别并 `store.set`，成功返回 `True`。`snapshot(self_id)` 在同一锁内规范化数据；跨日或脏数据被修正时写回，再返回 `{date, counts, total}`。

- [ ] **步骤 6：运行统计模块测试**

运行：`py -3 -m unittest tests.test_send_stats -v`

预期：分类、账号隔离、跨日和并发测试全部 PASS。

- [ ] **步骤 7：提交任务 1**

```powershell
git add -- send_stats.py tests/test_send_stats.py
git commit -m "feat: 添加每日发送统计模型（任务 1/4）"
```

### 任务 2：发送队列接入与账号 API

**文件：**
- 修改：`main.py:512-542,881-920,993-1000,2187-2198`
- 修改：`tests/test_state_machine_runtime.py`

- [ ] **步骤 1：编写暂停队列仍计数和统计失败不阻塞的失败测试**

```python
async def test_official_command_is_counted_when_queued_during_captcha_pause(self):
    main = _import_main_with_astrbot_stubs()
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    plugin._send_locks = {}
    plugin._send_queues = {}
    blocker = asyncio.create_task(asyncio.Event().wait())
    plugin._send_tasks = {"111:group": blocker}
    recorded = []
    class Stats:
        async def record(self, self_id, text):
            recorded.append((self_id, text))
    plugin.send_stats = Stats()
    await plugin._enqueue_official_command("111:group", "@3889001741 坊市查看药材1")
    self.assertEqual(["@3889001741 坊市查看药材1"], plugin._send_queues["111:group"])
    self.assertEqual([("111", "@3889001741 坊市查看药材1")], recorded)
    blocker.cancel()
    with self.assertRaises(asyncio.CancelledError):
        await blocker

async def test_stats_failure_does_not_remove_queued_command(self):
    main = _import_main_with_astrbot_stubs()
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    plugin._send_locks = {}
    plugin._send_queues = {}
    blocker = asyncio.create_task(asyncio.Event().wait())
    plugin._send_tasks = {"111:group": blocker}
    class Stats:
        async def record(self, self_id, text):
            raise OSError("disk")
    plugin.send_stats = Stats()
    await plugin._enqueue_official_command("111:group", "@3889001741 坊市购买abc 1")
    self.assertEqual(["@3889001741 坊市购买abc 1"], plugin._send_queues["111:group"])
    blocker.cancel()
    with self.assertRaises(asyncio.CancelledError):
        await blocker
```

- [ ] **步骤 2：运行入队测试确认红灯**

运行：`py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_official_command_is_counted_when_queued_during_captcha_pause tests.test_state_machine_runtime.StateMachineRuntimeTests.test_stats_failure_does_not_remove_queued_command -v`

预期：FAIL，因为 `_enqueue_official_command` 尚未调用统计器。

- [ ] **步骤 3：初始化统计器并在入队后记录**

在 `main.py` 导入 `DailySendStats`，构造函数在 `JsonStore` 后执行 `self.send_stats = DailySendStats(self.store)`。`_enqueue_official_command` 先在原锁内追加队列并确保 worker 存在，离开锁后执行：

```python
try:
    await self.send_stats.record(self._self_id_from_key(key), text)
except Exception as e:
    logger.warning(f"[xiao_xiuxian_auto] 记录每日发送统计失败: key={key} err={e}")
```

不在 `_send_worker` 或 `_raw_send_by_key` 再次计数。

- [ ] **步骤 4：编写读取 API 失败测试**

```python
async def test_send_stats_page_api_returns_selected_account_snapshot(self):
    main = _import_main_with_astrbot_stubs()
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    async def validate(self_id):
        return self_id == "111"
    class Request:
        async def json(self, default=None):
            return {"self_id": "111"}
    class Stats:
        async def snapshot(self, self_id):
            return {"date": "2026-07-29", "counts": {"market_view": 2, "purchase": 3, "alchemy": 4}, "total": 9}
    plugin._page_validate_account = validate
    plugin.send_stats = Stats()
    with patch.object(main, "request", Request()):
        result = await plugin._page_load_send_stats()
    self.assertEqual("111", result["self_id"])
    self.assertEqual(9, result["total"])
```

- [ ] **步骤 5：注册并实现 `send_stats/load`**

注册 POST 路由 `/{PLUGIN_NAME}/send_stats/load`。处理器读取 `self_id`，复用 `_page_validate_account`，失败返回 400；成功合并 `await self.send_stats.snapshot(self_id)` 并返回 `self_id`。存储异常返回 500 和“加载发送统计失败”。

- [ ] **步骤 6：运行队列与 API 测试**

运行：`py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_official_command_is_counted_when_queued_during_captcha_pause tests.test_state_machine_runtime.StateMachineRuntimeTests.test_stats_failure_does_not_remove_queued_command tests.test_state_machine_runtime.StateMachineRuntimeTests.test_send_stats_page_api_returns_selected_account_snapshot -v`

预期：3 个测试 PASS。

- [ ] **步骤 7：提交任务 2**

```powershell
git add -- main.py tests/test_state_machine_runtime.py
git commit -m "feat: 统计官方命令入队次数（任务 2/4）"
```

### 任务 3：WebUI 发送统计页签

**文件：**
- 修改：`pages/config/app.js`
- 修改：`pages/config/style.css`
- 修改：`tests/test_core_regressions.py`

- [ ] **步骤 1：编写 WebUI 接入失败测试**

```python
def test_webui_has_daily_send_stats_tab_and_refresh_lifecycle(self):
    page_text = (ROOT / "pages" / "config" / "app.js").read_text(encoding="utf-8")
    style_text = (ROOT / "pages" / "config" / "style.css").read_text(encoding="utf-8")
    self.assertIn("__send_stats", page_text)
    self.assertIn("send_stats/load", page_text)
    self.assertIn("sendStatsRefreshTimer", page_text)
    self.assertIn("5000", page_text)
    self.assertIn("clearInterval", page_text)
    self.assertIn("send-stats-grid", page_text)
    self.assertIn(".send-stats-grid", style_text)
    self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", style_text)
```

- [ ] **步骤 2：运行页面测试确认红灯**

运行：`py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_webui_has_daily_send_stats_tab_and_refresh_lifecycle -v`

预期：FAIL，因为发送统计页签和样式不存在。

- [ ] **步骤 3：实现页签、加载和定时器生命周期**

向 `SPECIAL_TABS` 添加 `{ key: '__send_stats', label: '发送统计', icon: '📈' }`。新增全局 `sendStatsRefreshTimer`，以及 `stopSendStatsRefresh()`；每次 `renderTab` 先清理旧定时器，再路由到 `renderSendStatsTab()`。

`renderSendStatsTab()` 固定捕获 `currentAccount` 和 `accountLoadGeneration`，渲染日期、三项计数、合计和刷新按钮。内部 `loadStats(initial)` POST `send_stats/load`，仅在账号、generation 和当前页签仍匹配时更新文本；初次失败显示错误区，后续刷新失败保留现有数字并 toast。立即加载后用 `setInterval(..., 5000)` 自动刷新。

- [ ] **步骤 4：保持账号切换后的当前页签**

`renderTabs()` 在清空 tabs 前保存 `currentTab`，重建后若对应 `data-key` 仍存在则继续 `renderTab(currentTab)`，否则打开第一个页签。更新 mock bridge，为 `send_stats/load` 返回固定示例快照。

- [ ] **步骤 5：实现响应式统计布局**

`.send-stats-grid` 使用三列稳定网格；每项包含紧凑标签和数字。`.send-stats-total` 用全宽水平行显示合计。现有 `@media (max-width: 720px)` 将网格改为单列，并保证数字、日期和刷新按钮不溢出。

- [ ] **步骤 6：运行 WebUI 与现有账号路由测试**

运行：

```powershell
node --check pages/config/app.js
py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_webui_has_daily_send_stats_tab_and_refresh_lifecycle tests.test_core_regressions.CoreRegressionTests.test_custom_webui_routes_all_business_data_through_selected_account -v
```

预期：JS 语法通过，2 个 Python 测试 PASS。

- [ ] **步骤 7：提交任务 3**

```powershell
git add -- pages/config/app.js pages/config/style.css tests/test_core_regressions.py
git commit -m "feat: 在 WebUI 显示每日发送统计（任务 3/4）"
```

### 任务 4：集成验证与视觉验收

**文件：**
- 修改：仅在验证发现本功能缺陷时修改对应实现或测试文件。

- [ ] **步骤 1：运行功能测试矩阵**

运行：

```powershell
py -3 -m unittest tests.test_send_stats -v
py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_official_command_is_counted_when_queued_during_captcha_pause tests.test_state_machine_runtime.StateMachineRuntimeTests.test_stats_failure_does_not_remove_queued_command tests.test_state_machine_runtime.StateMachineRuntimeTests.test_send_stats_page_api_returns_selected_account_snapshot -v
py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_webui_has_daily_send_stats_tab_and_refresh_lifecycle tests.test_core_regressions.CoreRegressionTests.test_custom_webui_routes_all_business_data_through_selected_account -v
```

预期：全部 PASS。

- [ ] **步骤 2：运行完整回归与静态检查**

运行：

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q send_stats.py main.py tests
node --check pages/config/app.js
git diff --check master...HEAD
```

预期：本功能无新增失败；完整套件只保留已确认的验证码文案基线失败 `test_targeted_captcha_pauses_when_vision_is_unconfigured`。

- [ ] **步骤 3：桌面与窄屏视觉检查**

启动配置页本地预览，在 1440×900 和 390×844 检查独立“发送统计”页签：三项与合计层次清晰，账号切换保持页签，刷新按钮可用，窄屏无横向溢出。验证页面离开后不再发出 `send_stats/load` 周期请求。

- [ ] **步骤 4：提交验证修复**

若验证未产生修改，不创建空提交。若发现本功能缺陷，修复后只暂存相关文件并提交：

```powershell
git add -- send_stats.py main.py pages/config/app.js pages/config/style.css tests/test_send_stats.py tests/test_state_machine_runtime.py tests/test_core_regressions.py
git commit -m "fix: 完善每日发送统计（任务 4/4）"
```
