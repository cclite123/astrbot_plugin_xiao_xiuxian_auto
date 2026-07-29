# 药材价格品级分组实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将药材上限价页面改为九品至一品的可折叠分组编辑器，并以嵌套 YAML 持久化品级，同时保持炼丹购买使用的扁平价格查询不变。

**架构：** `AutoAlchemyOptimizer` 同时维护分组表示、待分类迁移项和现有扁平价格索引，并提供单一的加载/保存接口。Web API 传输分组对象；前端把无 DOM 依赖的数据规范化与校验逻辑放在独立 ES 模块中，`app.js` 只负责折叠面板和行控件交互。

**技术栈：** Python 3、`unittest`、PyYAML、原生 ES Modules、Node.js 内置测试运行器、HTML/CSS。

---

## 文件结构

- 修改 `auto_alchemy_optimizer.py`：定义固定品级，加载嵌套/旧扁平 YAML，验证并原子保存分组，同时生成运行时扁平索引。
- 修改 `main.py`：为账号优化器注入默认药材目录路径，并升级药材价格加载/保存 API 契约。
- 创建 `pages/config/herb_prices.mjs`：提供品级常量、载荷规范化、默认展开品级和保存校验纯函数。
- 修改 `pages/config/app.js`：渲染折叠品级、行内品级选择、移动、新增、删除、错误显示和保存请求。
- 修改 `pages/config/style.css`：提供桌面与窄屏稳定布局，替换未命中的旧药材行选择器。
- 创建 `tests/test_herb_price_groups.py`：覆盖分组加载、旧配置迁移、原子保存和验证失败。
- 修改 `tests/test_state_machine_runtime.py`：覆盖账号级 API 载荷与保存错误响应。
- 创建 `tests/herb_prices_ui.test.mjs`：覆盖前端纯函数和九品至一品顺序。
- 修改 `tests/test_core_regressions.py`：锁定新前端模块、分组标记和响应式样式接入。

### 任务 1：后端分组模型与嵌套 YAML

**文件：**
- 创建：`tests/test_herb_price_groups.py`
- 修改：`auto_alchemy_optimizer.py:163-325`

- [ ] **步骤 1：编写嵌套加载和旧扁平迁移的失败测试**

```python
class HerbPriceGroupTests(unittest.TestCase):
    def make_optimizer(self, temp_dir: str, account_data: dict, catalog_data: dict):
        account_path = Path(temp_dir) / "account.yaml"
        catalog_path = Path(temp_dir) / "catalog.yaml"
        account_path.write_text(yaml.safe_dump(account_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        catalog_path.write_text(yaml.safe_dump(catalog_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path=str(Path(temp_dir) / "recipes.txt"),
            config={
                "herb_max_prices_path": str(account_path),
                "herb_grade_catalog_path": str(catalog_path),
            },
        ), account_path

    def test_nested_yaml_preserves_groups_and_builds_flat_lookup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, _ = self.make_optimizer(
                temp_dir,
                {"九品药材": {"尘磊岩麟果": 960}, "一品药材": {"清灵草": 12}},
                {"九品药材": {"尘磊岩麟果": 1000}, "一品药材": {"清灵草": 10}},
            )
            payload = optimizer.get_herb_price_config()
            self.assertEqual(960.0, payload["groups"]["九品药材"]["尘磊岩麟果"])
            self.assertEqual(12.0, optimizer.herb_max_prices["清灵草"])
            self.assertEqual({}, payload["unclassified"])

    def test_flat_yaml_uses_catalog_and_keeps_unknown_items_unclassified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, _ = self.make_optimizer(
                temp_dir,
                {"尘磊岩麟果": 960, "自定义草": 25},
                {"九品药材": {"尘磊岩麟果": 1000}},
            )
            payload = optimizer.get_herb_price_config()
            self.assertEqual(960.0, payload["groups"]["九品药材"]["尘磊岩麟果"])
            self.assertEqual({"自定义草": 25.0}, payload["unclassified"])
```

- [ ] **步骤 2：运行测试验证正确失败**

运行：`py -3 -m unittest tests.test_herb_price_groups.HerbPriceGroupTests.test_nested_yaml_preserves_groups_and_builds_flat_lookup tests.test_herb_price_groups.HerbPriceGroupTests.test_flat_yaml_uses_catalog_and_keeps_unknown_items_unclassified -v`

预期：ERROR，提示 `AutoAlchemyOptimizer` 没有 `get_herb_price_config` 或没有分组属性。

- [ ] **步骤 3：实现固定品级与加载模型**

在 `AutoAlchemyOptimizer` 中加入以下接口，并让构造函数一次性初始化三种状态：

```python
HERB_GRADES = (
    "九品药材", "八品药材", "七品药材", "六品药材", "五品药材",
    "四品药材", "三品药材", "二品药材", "一品药材",
)

def get_herb_price_config(self) -> Dict[str, Any]:
    return {
        "groups": {grade: dict(self.herb_price_groups.get(grade, {})) for grade in self.HERB_GRADES},
        "unclassified": dict(self.unclassified_herb_prices),
        "prices": dict(self.herb_max_prices),
    }
```

`_load_herb_price_config()` 必须遵守：嵌套账号数据直接按合法品级读取；扁平账号数据只从 `herb_grade_catalog_path` 获取名称到品级的映射；账号文件中的价格始终优先；无法归类的名称进入 `unclassified_herb_prices`；所有正价格同时进入 `herb_max_prices`。

- [ ] **步骤 4：运行加载测试验证通过**

运行：`py -3 -m unittest tests.test_herb_price_groups.HerbPriceGroupTests.test_nested_yaml_preserves_groups_and_builds_flat_lookup tests.test_herb_price_groups.HerbPriceGroupTests.test_flat_yaml_uses_catalog_and_keeps_unknown_items_unclassified -v`

预期：2 个测试 PASS。

- [ ] **步骤 5：编写保存、验证和原子性失败测试**

```python
def test_save_writes_nested_yaml_and_updates_flat_lookup(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        optimizer, account_path = self.make_optimizer(temp_dir, {}, {})
        optimizer.set_herb_price_groups({
            "九品药材": {"尘磊岩麟果": 960},
            "一品药材": {"清灵草": 12},
        })
        saved = yaml.safe_load(account_path.read_text(encoding="utf-8"))
        self.assertEqual(960.0, saved["九品药材"]["尘磊岩麟果"])
        self.assertEqual({"尘磊岩麟果": 960.0, "清灵草": 12.0}, optimizer.herb_max_prices)

def test_invalid_save_does_not_change_memory_or_file(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        optimizer, account_path = self.make_optimizer(temp_dir, {"九品药材": {"旧药": 9}}, {})
        before_file = account_path.read_text(encoding="utf-8")
        before_prices = dict(optimizer.herb_max_prices)
        with self.assertRaisesRegex(ValueError, "重复"):
            optimizer.set_herb_price_groups({
                "九品药材": {"同名药": 10},
                "八品药材": {"同名药": 20},
            })
        self.assertEqual(before_file, account_path.read_text(encoding="utf-8"))
        self.assertEqual(before_prices, optimizer.herb_max_prices)
```

- [ ] **步骤 6：运行保存测试验证正确失败**

运行：`py -3 -m unittest tests.test_herb_price_groups.HerbPriceGroupTests.test_save_writes_nested_yaml_and_updates_flat_lookup tests.test_herb_price_groups.HerbPriceGroupTests.test_invalid_save_does_not_change_memory_or_file -v`

预期：ERROR，提示没有 `set_herb_price_groups`。

- [ ] **步骤 7：实现验证和原子保存**

实现 `set_herb_price_groups(groups)`：拒绝未知品级、空名称、非有限数值、非正价格和跨品级重复名称；先用同目录临时文件 `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)`，成功后 `os.replace`；只有替换成功后才更新 `herb_price_groups`、清空 `unclassified_herb_prices` 并替换 `herb_max_prices`。保留旧 `set_herb_max_prices` 作为兼容入口，但内部只允许通过目录映射归类后调用新接口。

- [ ] **步骤 8：运行后端分组测试**

运行：`py -3 -m unittest tests.test_herb_price_groups -v`

预期：全部 PASS。

- [ ] **步骤 9：提交任务 1**

```powershell
git add -- auto_alchemy_optimizer.py tests/test_herb_price_groups.py
git commit -m "feat: 保留药材价格品级结构"
```

### 任务 2：账号 Web API 分组契约

**文件：**
- 修改：`main.py:785-798,1032-1056`
- 修改：`tests/test_state_machine_runtime.py`

- [ ] **步骤 1：编写加载和非法保存的失败测试**

```python
async def test_herb_price_page_api_returns_groups_for_selected_account(self):
    main = _import_main_with_astrbot_stubs()
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    async def validate_account(self_id):
        return self_id == "111"
    class FakeRequest:
        async def json(self, default=None):
            return {"self_id": "111"}
    class FakeOptimizer:
        def get_herb_price_config(self):
            return {
                "groups": {"九品药材": {"尘磊岩麟果": 960.0}},
                "unclassified": {},
                "prices": {"尘磊岩麟果": 960.0},
            }
    plugin._page_validate_account = validate_account
    plugin._controller = lambda module, key: FakeOptimizer()
    with patch.object(main, "request", FakeRequest()):
        result = await plugin._page_load_herb_prices()
    self.assertEqual("111", result["self_id"])
    self.assertEqual(960.0, result["groups"]["九品药材"]["尘磊岩麟果"])

async def test_herb_price_page_api_rejects_invalid_groups(self):
    main = _import_main_with_astrbot_stubs()
    plugin = main.XiaoXiuxianAuto.__new__(main.XiaoXiuxianAuto)
    async def validate_account(self_id):
        return self_id == "111"
    class FakeRequest:
        async def json(self, default=None):
            return {"self_id": "111", "groups": {"九品药材": {"同名药": 10}}}
    class FakeOptimizer:
        def set_herb_price_groups(self, groups):
            raise ValueError("药材名重复")
    errors = []
    def capture_error(message, status_code=500):
        errors.append((message, status_code))
        return {"error": message, "status_code": status_code}
    plugin._page_validate_account = validate_account
    plugin._controller = lambda module, key: FakeOptimizer()
    with patch.object(main, "request", FakeRequest()), patch.object(main, "error_response", side_effect=capture_error):
        result = await plugin._page_save_herb_prices()
    self.assertEqual(400, result["status_code"])
    self.assertIn("药材名重复", result["error"])
    self.assertEqual([("药材名重复", 400)], errors)
```

- [ ] **步骤 2：运行 API 测试验证正确失败**

运行：`py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_returns_groups_for_selected_account tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_rejects_invalid_groups -v`

预期：FAIL，因为加载接口仍只读取 `herb_max_prices`，保存接口仍要求 `prices`。

- [ ] **步骤 3：实现 API 契约和目录注入**

在账号控制器构造中加入：

```python
auto_cfg["herb_max_prices_path"] = herb_prices_path
auto_cfg["herb_grade_catalog_path"] = os.path.join(self.data_dir, "herb_max_prices.yaml")
```

加载接口合并 `optimizer.get_herb_price_config()` 到响应。保存接口要求 `groups` 为对象，调用 `set_herb_price_groups(groups)`；`ValueError` 返回 400，文件或其他运行时错误返回 500。成功响应继续包含 `ok` 和 `self_id`。

- [ ] **步骤 4：运行 API 和账号隔离测试**

运行：`py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_returns_groups_for_selected_account tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_rejects_invalid_groups tests.test_state_machine_runtime.StateMachineRuntimeTests.test_account_business_controllers_use_isolated_config_and_files -v`

预期：3 个测试 PASS。

- [ ] **步骤 5：提交任务 2**

```powershell
git add -- main.py tests/test_state_machine_runtime.py
git commit -m "feat: 提供药材品级配置接口"
```

### 任务 3：前端品级数据纯函数

**文件：**
- 创建：`pages/config/herb_prices.mjs`
- 创建：`tests/herb_prices_ui.test.mjs`

- [ ] **步骤 1：编写九品顺序、载荷规范化和保存校验失败测试**

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  HERB_GRADES,
  firstNonEmptyGrade,
  normalizeHerbPayload,
  validateHerbRows,
} from '../pages/config/herb_prices.mjs';

test('品级固定按九品至一品排列', () => {
  assert.deepEqual(HERB_GRADES, ['九品药材','八品药材','七品药材','六品药材','五品药材','四品药材','三品药材','二品药材','一品药材']);
});

test('载荷补齐空品级并优先展开首个非空品级', () => {
  const model = normalizeHerbPayload({ groups: { '七品药材': { '凤血果': 370 } }, unclassified: {} });
  assert.deepEqual(model.groups['九品药材'], {});
  assert.equal(firstNonEmptyGrade(model.groups), '七品药材');
});

test('保存校验生成嵌套分组并拒绝重复名称', () => {
  const valid = validateHerbRows([
    { grade: '九品药材', name: '尘磊岩麟果', price: '960' },
    { grade: '一品药材', name: '清灵草', price: '12' },
  ]);
  assert.equal(valid.ok, true);
  assert.equal(valid.groups['九品药材']['尘磊岩麟果'], 960);
  const duplicate = validateHerbRows([
    { grade: '九品药材', name: '同名药', price: '10' },
    { grade: '八品药材', name: '同名药', price: '20' },
  ]);
  assert.equal(duplicate.ok, false);
  assert.equal(duplicate.errors[1].code, 'duplicate-name');
});
```

- [ ] **步骤 2：运行前端测试验证正确失败**

运行：`node --test tests/herb_prices_ui.test.mjs`

预期：FAIL，提示无法找到 `pages/config/herb_prices.mjs`。

- [ ] **步骤 3：实现纯函数模块**

`herb_prices.mjs` 导出以下完整纯函数：

```javascript
export const HERB_GRADES = Object.freeze([
  '九品药材', '八品药材', '七品药材', '六品药材', '五品药材',
  '四品药材', '三品药材', '二品药材', '一品药材',
]);

function emptyGroups() {
  return Object.fromEntries(HERB_GRADES.map(grade => [grade, {}]));
}

function normalizedName(value) {
  return String(value || '')
    .trim()
    .replace(/[\u200b-\u200f\ufeff\u2060-\u206f]/g, '')
    .replace(/\s+/g, '')
    .replaceAll('：', ':')
    .replaceAll('，', ',');
}

export function normalizeHerbPayload(payload = {}) {
  const groups = emptyGroups();
  for (const grade of HERB_GRADES) {
    const source = payload.groups && typeof payload.groups[grade] === 'object' ? payload.groups[grade] : {};
    groups[grade] = { ...source };
  }
  const unclassified = payload.unclassified && typeof payload.unclassified === 'object'
    ? { ...payload.unclassified }
    : {};
  return { groups, unclassified };
}

export function firstNonEmptyGrade(groups) {
  return HERB_GRADES.find(grade => Object.keys(groups[grade] || {}).length > 0) || HERB_GRADES[0];
}

export function validateHerbRows(rows) {
  const groups = emptyGroups();
  const errors = [];
  const seen = new Map();
  rows.forEach((row, index) => {
    const grade = String(row.grade || '');
    const name = normalizedName(row.name);
    const price = Number(row.price);
    let code = '';
    if (!HERB_GRADES.includes(grade)) code = 'invalid-grade';
    else if (!name) code = 'empty-name';
    else if (!Number.isFinite(price) || price <= 0) code = 'invalid-price';
    else if (seen.has(name)) code = 'duplicate-name';
    if (code) {
      errors.push({ index, code });
      return;
    }
    seen.set(name, index);
    groups[grade][name] = price;
  });
  return { ok: errors.length === 0, groups, errors };
}
```

验证错误码固定为 `invalid-grade`、`empty-name`、`invalid-price`、`duplicate-name`，错误对象包含原始行索引，便于 DOM 标记对应行。

- [ ] **步骤 4：运行前端纯函数测试**

运行：`node --test tests/herb_prices_ui.test.mjs`

预期：全部 PASS。

- [ ] **步骤 5：提交任务 3**

```powershell
git add -- pages/config/herb_prices.mjs tests/herb_prices_ui.test.mjs
git commit -m "feat: 添加药材品级前端模型"
```

### 任务 4：折叠分组编辑器与响应式样式

**文件：**
- 修改：`pages/config/app.js:1-35,272-330`
- 修改：`pages/config/style.css:282-293,330-338`
- 修改：`tests/test_core_regressions.py`

- [ ] **步骤 1：编写页面接入失败测试**

```python
def test_herb_price_page_uses_grade_groups_and_responsive_rows(self):
    page_text = (ROOT / "pages" / "config" / "app.js").read_text(encoding="utf-8")
    style_text = (ROOT / "pages" / "config" / "style.css").read_text(encoding="utf-8")
    self.assertIn("from './herb_prices.mjs'", page_text)
    self.assertIn("herb-grade-toggle", page_text)
    self.assertIn("herb-grade-select", page_text)
    self.assertIn("validateHerbRows", page_text)
    self.assertIn("groups", page_text)
    self.assertIn(".herb-row", style_text)
    self.assertIn(".herb-grade", style_text)
    self.assertIn("grid-template-columns", style_text)
    self.assertNotIn("#herb-list .field", style_text)
```

- [ ] **步骤 2：运行接入测试验证正确失败**

运行：`py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_herb_price_page_uses_grade_groups_and_responsive_rows -v`

预期：FAIL，因为页面尚未导入前端模型且 CSS 仍匹配错误的 `.field`。

- [ ] **步骤 3：实现折叠分组 DOM**

在 `app.js` 顶部导入 `HERB_GRADES`、`normalizeHerbPayload`、`firstNonEmptyGrade` 和 `validateHerbRows`。更新预览载荷为嵌套 `groups`。

`renderHerbTab()` 依次创建九个 `.herb-grade` 区块。标题按钮 `.herb-grade-toggle` 设置 `aria-expanded`；正文使用 `.herb-grade-body`。默认展开 `firstNonEmptyGrade(groups)`，但允许多个区块同时展开。

`makeHerbRow(grade, name, price)` 创建 `.herb-row`，其中品级 `<select class="herb-grade-select">` 包含九个选项；待分类行先放在展开的迁移区，选择合法品级后移动到目标 `.herb-grade-body` 并更新两侧计数。新增行采用最近展开品级，无记录时采用九品。删除和移动后都调用 `updateHerbGradeCounts()`。

- [ ] **步骤 4：实现保存校验和请求**

`saveHerbPrices()` 把 DOM 行转换成 `{ grade, name, price }` 列表并调用 `validateHerbRows`。错误行添加 `.invalid` 和 `aria-invalid="true"`，toast 显示第一条具体原因；验证通过时发送：

```javascript
bridge.apiPost('herb_prices/save', {
  self_id: currentAccount,
  groups: result.groups,
})
```

保存成功和失败时沿用现有按钮禁用、恢复及 toast 逻辑。

- [ ] **步骤 5：实现桌面与窄屏样式**

桌面 `.herb-row` 使用固定网格：`150px minmax(180px, 1fr) 140px 36px`。折叠标题为全宽按钮，药材数量与箭头保持稳定。删除按钮使用 `×`，设置 `title="删除药材"` 和 `aria-label="删除药材"`。

在现有 `@media (max-width: 720px)` 中把 `.herb-row` 改为两列网格，名称输入占满第二列，价格和删除按钮落到下一行，不允许输入框溢出容器。

- [ ] **步骤 6：运行前端与静态回归测试**

运行：

```powershell
node --test tests/herb_prices_ui.test.mjs
py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_herb_price_page_uses_grade_groups_and_responsive_rows tests.test_core_regressions.CoreRegressionTests.test_custom_webui_routes_all_business_data_through_selected_account -v
```

预期：Node 测试全部 PASS，2 个 Python 测试 PASS。

- [ ] **步骤 7：提交任务 4**

```powershell
git add -- pages/config/app.js pages/config/style.css tests/test_core_regressions.py
git commit -m "feat: 按品级折叠显示药材价格"
```

### 任务 5：集成验证与视觉验收

**文件：**
- 修改：仅在验证发现本功能缺陷时修改对应实现或测试文件。

- [ ] **步骤 1：运行本功能全部测试**

运行：

```powershell
py -3 -m unittest tests.test_herb_price_groups -v
py -3 -m unittest tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_returns_groups_for_selected_account tests.test_state_machine_runtime.StateMachineRuntimeTests.test_herb_price_page_api_rejects_invalid_groups tests.test_state_machine_runtime.StateMachineRuntimeTests.test_account_business_controllers_use_isolated_config_and_files -v
node --test tests/herb_prices_ui.test.mjs
py -3 -m unittest tests.test_core_regressions.CoreRegressionTests.test_herb_price_page_uses_grade_groups_and_responsive_rows tests.test_core_regressions.CoreRegressionTests.test_custom_webui_routes_all_business_data_through_selected_account -v
```

预期：全部 PASS，无警告或未处理异常。

- [ ] **步骤 2：运行完整 Python 回归并核对既有失败**

运行：`py -3 -m unittest discover -s tests -v`

预期：本功能相关测试全部 PASS；基线中已确认的 `test_targeted_captcha_pauses_when_vision_is_unconfigured` 仍因文案不含 `openai` 失败，除此之外没有新增失败。

- [ ] **步骤 3：运行代码与差异检查**

运行：

```powershell
py -3 -m compileall -q auto_alchemy_optimizer.py main.py tests
git diff --check master...HEAD
git status --short
```

预期：编译成功，`git diff --check` 无输出，工作区只包含计划允许的变更或保持干净。

- [ ] **步骤 4：启动配置页预览并检查桌面布局**

从 worktree 根目录启动静态服务器：`py -3 -m http.server 8765 --directory pages/config`，打开 `http://localhost:8765/`。在 1440×900 视口确认：九品至一品顺序正确、默认仅首个非空品级展开、计数正确、品级下拉能移动行、空品级可展开、删除按钮不改变网格宽度。

- [ ] **步骤 5：检查窄屏布局和交互**

在 390×844 视口确认：标题、下拉、名称、价格和删除按钮无重叠或横向溢出；新增行进入最近展开品级；重复名称和无效价格阻止保存并标记对应行。

- [ ] **步骤 6：提交验证期间的必要修复**

若步骤 1-5 没有产生修改，不创建空提交。若发现并修复本功能缺陷，只暂存相关文件并提交：

```powershell
git add -- auto_alchemy_optimizer.py main.py pages/config/app.js pages/config/style.css pages/config/herb_prices.mjs tests/test_herb_price_groups.py tests/test_state_machine_runtime.py tests/test_core_regressions.py tests/herb_prices_ui.test.mjs
git commit -m "fix: 完善药材品级分组交互"
```
