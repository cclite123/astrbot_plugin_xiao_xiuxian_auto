import {
  HERB_GRADES,
  firstNonEmptyGrade,
  normalizeHerbPayload,
  validateHerbRows,
} from './herb_prices.mjs';

const _realBridge = window.AstrBotPluginPage;
const PREVIEW_MODE = !_realBridge;

const MOCK_SCHEMA = {
  bounty: { description: '悬赏模块配置', type: 'object', hint: '控制悬赏策略、重试时间、每日启动时间和随机抖动。', items: {
    default_strategy: { description: '默认悬赏策略', type: 'string', hint: '修为：优先修为收益；价值：优先额外机缘价值；耗时：优先最短耗时。', options: ['修为','价值','耗时'], default: '价值' },
    retry_when_running_sec: { description: '运行中重试间隔（秒）', type: 'int', default: 30 },
    next_morning_hour: { description: '次日启动小时', type: 'int', default: 8 },
  }},
};
const MOCK_CONFIG = {
  bounty: { default_strategy: '价值', retry_when_running_sec: 30, next_morning_hour: 8 },
};
const MOCK_ACCOUNTS = [
  { self_id: '123456789', groups: ['10001', '10002'] },
  { self_id: '987654321', groups: ['20001'] },
];

const bridge = _realBridge || {
  ready: async () => ({ pluginName: 'preview', isDark: false }),
  getContext: () => ({ isDark: false }),
  onContext: () => {},
  apiGet: async (ep) => {
    if (ep === 'accounts') return { accounts: MOCK_ACCOUNTS };
    if (ep === 'status') return { bound_keys: 3 };
    return {};
  },
  apiPost: async (ep, body) => {
    if (ep === 'config/load') return { config: MOCK_CONFIG, schema: MOCK_SCHEMA };
    if (ep === 'alchemy_rules/load') return { whitelist_pill: ['培元丹','回元丹','养元丹'], blacklist_equip: ['龙渊剑','惊雷'], blacklist_artifact: ['两仪心经'] };
    if (ep === 'herb_prices/load') return {
      groups: {
        '九品药材': { '尘磊岩麟果': 1000, '离火梧桐芝': 1000 },
        '七品药材': { '凤血果': 370 },
        '三品药材': { '九叶芝': 320 },
      },
      unclassified: {},
    };
    console.log('[preview] save', ep, body);
    return { ok: true, reloaded: true };
  },
};

const tabsEl = document.getElementById('tabs');
const formEl = document.getElementById('form-area');
const searchEl = document.getElementById('search');
const saveBtn = document.getElementById('save-btn');
const statusLine = document.getElementById('status-line');
const accountSelectEl = document.getElementById('account-select');

let schema = {};
let config = {};
let currentTab = null;
let currentAccount = '';
let accountLoadGeneration = 0;
let lastExpandedHerbGrade = HERB_GRADES[0];

const SPECIAL_TABS = [
  { key: '__alchemy', label: '炼金名单', icon: '🧪' },
  { key: '__herb', label: '药材上限价', icon: '🌿' },
];

if (PREVIEW_MODE) {
  const badge = document.createElement('span');
  badge.className = 'badge-preview';
  badge.textContent = '预览模式（mock 数据）';
  document.querySelector('.actions').insertBefore(badge, searchEl);
}

function toast(msg, ok = true) {
  const t = document.createElement('div');
  t.className = 'toast ' + (ok ? 'ok' : 'err');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function fieldType(item) {
  if (!item || typeof item !== 'object') return 'text';
  if (item.type === 'bool') return 'bool';
  if (item.type === 'int' || item.type === 'number' || item.type === 'float') return 'number';
  if (item.type === 'list') return 'list';
  if (Array.isArray(item.options) && item.options.length) return 'select';
  if (item.type === 'object') return 'object';
  return 'text';
}

function setConfig(path, val) {
  let cur = config;
  for (let i = 0; i < path.length - 1; i++) {
    if (typeof cur[path[i]] !== 'object' || cur[path[i]] === null) cur[path[i]] = {};
    cur = cur[path[i]];
  }
  cur[path[path.length - 1]] = val;
}

// Page 容器中输入框失焦时不一定会触发 change；保存前从 DOM 再采集一次，避免提交旧配置。
function syncFormToConfig() {
  formEl.querySelectorAll('.field[data-path]').forEach(wrap => {
    // object 字段只是分组容器，真正的值由其子字段提供。
    if (wrap.querySelector('.field[data-path]')) return;
    const path = String(wrap.dataset.path || '').split('.').filter(Boolean);
    if (!path.length) return;
    const checkbox = wrap.querySelector('input[type="checkbox"]');
    if (checkbox) {
      setConfig(path, checkbox.checked);
      return;
    }
    const control = wrap.querySelector('select, textarea, input');
    if (!control) return;
    if (control.tagName === 'SELECT') {
      setConfig(path, control.value);
    } else if (control.tagName === 'TEXTAREA') {
      const values = control.value
        .split(/[\n,，、]/)
        .map(v => v.trim())
        .filter(Boolean);
      setConfig(path, values);
    } else if (control.type === 'number') {
      const value = Number(control.value);
      setConfig(path, Number.isFinite(value) ? value : 0);
    } else {
      setConfig(path, control.value);
    }
  });
}

function renderField(key, item, value, path) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.dataset.path = path.join('.');
  wrap.dataset.label = (item.description || key) + ' ' + (item.hint || '');
  const label = document.createElement('label');
  label.textContent = item.description || key;
  wrap.appendChild(label);
  if (item.hint) {
    const hint = document.createElement('div'); hint.className = 'hint'; hint.textContent = item.hint;
    wrap.appendChild(hint);
  }
  const t = fieldType(item);
  if (t === 'bool') {
    const row = document.createElement('div'); row.className = 'row';
    const toggle = document.createElement('label'); toggle.className = 'toggle';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!value;
    const slider = document.createElement('span'); slider.className = 'slider';
    toggle.appendChild(cb); toggle.appendChild(slider);
    const span = document.createElement('span'); span.className = 'hint'; span.textContent = cb.checked ? '已开启' : '已关闭';
    cb.addEventListener('change', () => { setConfig(path, cb.checked); span.textContent = cb.checked ? '已开启' : '已关闭'; });
    row.appendChild(toggle); row.appendChild(span); wrap.appendChild(row);
  } else if (t === 'select') {
    const sel = document.createElement('select');
    (item.options || []).forEach(opt => {
      const o = document.createElement('option'); o.value = opt; o.textContent = opt;
      if (String(value) === String(opt)) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => setConfig(path, sel.value));
    wrap.appendChild(sel);
  } else if (t === 'number') {
    const inp = document.createElement('input'); inp.type = 'number'; inp.value = value ?? 0;
    inp.addEventListener('change', () => setConfig(path, Number(inp.value)));
    wrap.appendChild(inp);
  } else if (t === 'list') {
    const input = document.createElement('textarea');
    input.rows = 4;
    input.placeholder = '每行一项，也可用逗号分隔';
    const entries = Array.isArray(value)
      ? value
      : (typeof value === 'string' ? value.split(/[\n,，、]/) : []);
    input.value = entries.map(v => String(v).trim()).filter(Boolean).join('\n');
    input.addEventListener('change', () => {
      const items = input.value
        .split(/[\n,，、]/)
        .map(v => v.trim())
        .filter(Boolean);
      setConfig(path, items);
    });
    wrap.appendChild(input);
  } else if (t === 'object') {
    const sub = document.createElement('div'); sub.className = 'subgroup';
    const items = item.items || {};
    for (const [k, subItem] of Object.entries(items)) {
      if (subItem && typeof subItem === 'object' && subItem.type) {
        sub.appendChild(renderField(k, subItem, (value || {})[k], [...path, k]));
      }
    }
    wrap.appendChild(sub);
  } else {
    const inp = document.createElement('input'); inp.type = 'text'; inp.value = value ?? '';
    inp.addEventListener('change', () => setConfig(path, inp.value));
    wrap.appendChild(inp);
  }
  return wrap;
}

function renderTab(modKey) {
  currentTab = modKey;
  if (modKey === '__alchemy') return renderAlchemyTab();
  if (modKey === '__herb') return renderHerbTab();
  saveBtn.style.display = '';
  searchEl.style.display = '';
  formEl.innerHTML = '';
  const item = schema[modKey];
  if (!item) return;
  const value = config[modKey];
  const title = document.createElement('div');
  title.className = 'section-title';
  title.textContent = item.description || modKey;
  formEl.appendChild(title);
  if (item.hint) {
    const h = document.createElement('div'); h.className = 'section-hint'; h.textContent = item.hint;
    formEl.appendChild(h);
  }
  const container = document.createElement('div');
  if (item.type === 'object' && item.items) {
    for (const [k, subItem] of Object.entries(item.items)) {
      if (subItem && typeof subItem === 'object' && subItem.type) {
        container.appendChild(renderField(k, subItem, (value || {})[k], [modKey, k]));
      }
    }
  } else {
    container.appendChild(renderField(modKey, item, value, [modKey]));
  }
  formEl.appendChild(container);
  [...tabsEl.children].forEach(b => b.classList.toggle('active', b.dataset.key === modKey));
  searchEl.value = '';
  [...formEl.querySelectorAll('.field')].forEach(f => f.style.display = '');
}

function renderAlchemyTab() {
  const selfId = currentAccount;
  const generation = accountLoadGeneration;
  saveBtn.style.display = 'none';
  searchEl.style.display = 'none';
  [...tabsEl.children].forEach(b => b.classList.toggle('active', b.dataset.key === '__alchemy'));
  formEl.innerHTML = '<div class="loading">加载炼金名单…</div>';
  bridge.apiPost('alchemy_rules/load', { self_id: selfId }).then(data => {
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    const wl = (data && data.whitelist_pill) || [];
    const be = (data && data.blacklist_equip) || [];
    const ba = (data && data.blacklist_artifact) || [];
    formEl.innerHTML = `
      <div class="section-title">🧪 炼金白/黑名单</div>
      <div class="section-hint">每行一个物品名。丹药白名单：仅名单内丹药参与一键炼金（空 = 不按白名单过滤）；装备/神物黑名单：名单内物品炼金时跳过。</div>
      <div class="field">
        <label>丹药白名单</label>
        <div class="hint">仅这些丹药会被炼金（空表示不按白名单过滤）</div>
        <textarea id="wl-pill" rows="6">${wl.join('\n')}</textarea>
      </div>
      <div class="field">
        <label>装备黑名单</label>
        <div class="hint">这些装备炼金时跳过</div>
        <textarea id="bl-equip" rows="6">${be.join('\n')}</textarea>
      </div>
      <div class="field">
        <label>神物黑名单</label>
        <div class="hint">这些神物炼金时跳过</div>
        <textarea id="bl-art" rows="6">${ba.join('\n')}</textarea>
      </div>
      <button id="save-rules-btn" class="primary">保存名单</button>
    `;
    document.getElementById('save-rules-btn').addEventListener('click', saveAlchemyRules);
  }).catch(e => {
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    formEl.innerHTML = '<div class="loading">加载名单失败：' + (e && e.message ? e.message : e) + '</div>';
  });
}

function saveAlchemyRules() {
  const get = id => document.getElementById(id).value.split('\n').map(s => s.trim()).filter(Boolean);
  const payload = { self_id: currentAccount, whitelist_pill: get('wl-pill'), blacklist_equip: get('bl-equip'), blacklist_artifact: get('bl-art') };
  const btn = document.getElementById('save-rules-btn');
  btn.disabled = true; btn.textContent = '保存中…';
  bridge.apiPost('alchemy_rules/save', payload)
    .then(() => toast('✅ 名单已保存并生效', true))
    .catch(e => toast('❌ 保存失败：' + (e && e.message ? e.message : e), false))
    .finally(() => { btn.disabled = false; btn.textContent = '保存名单'; });
}

function renderHerbTab() {
  const selfId = currentAccount;
  const generation = accountLoadGeneration;
  saveBtn.style.display = 'none';
  searchEl.style.display = 'none';
  [...tabsEl.children].forEach(b => b.classList.toggle('active', b.dataset.key === '__herb'));
  formEl.innerHTML = '<div class="loading">加载药材上限价…</div>';
  bridge.apiPost('herb_prices/load', { self_id: selfId }).then(data => {
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    const model = normalizeHerbPayload(data);
    lastExpandedHerbGrade = firstNonEmptyGrade(model.groups);
    formEl.innerHTML = `
      <div class="section-title">🌿 药材购买上限价</div>
      <div class="section-hint">炼丹购买每种药材时的最高可接受价格（单位：万灵石）。超过此价的药材不会购买。保存后下次购买即生效。</div>
      <div id="herb-list"></div>
      <div class="toolbar-row">
        <button id="add-herb-btn" type="button">+ 添加药材</button>
        <button id="save-herb-btn" class="primary">保存价格</button>
      </div>
    `;
    const listEl = document.getElementById('herb-list');
    HERB_GRADES.forEach(grade => {
      listEl.appendChild(makeHerbGradeSection(grade, grade === lastExpandedHerbGrade));
    });
    HERB_GRADES.forEach(grade => {
      Object.entries(model.groups[grade]).forEach(([name, price]) => {
        appendHerbRow(grade, name, price);
      });
    });
    if (Object.keys(model.unclassified).length > 0) {
      listEl.prepend(makeHerbGradeSection('', true));
      Object.entries(model.unclassified).forEach(([name, price]) => {
        appendHerbRow('', name, price);
      });
    }
    updateHerbGradeCounts();
    document.getElementById('add-herb-btn').addEventListener('click', () => {
      const row = appendHerbRow(lastExpandedHerbGrade, '', '');
      expandHerbGrade(lastExpandedHerbGrade);
      row.querySelector('.herb-name-input').focus();
    });
    document.getElementById('save-herb-btn').addEventListener('click', saveHerbPrices);
  }).catch(e => {
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    formEl.innerHTML = '<div class="loading">加载药材价格失败：' + (e && e.message ? e.message : e) + '</div>';
  });
}

function makeHerbGradeSection(grade, expanded) {
  const section = document.createElement('section');
  section.className = 'herb-grade' + (grade ? '' : ' herb-grade-unclassified');
  section.dataset.grade = grade;
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'herb-grade-toggle';
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  const label = document.createElement('span');
  label.className = 'herb-grade-label';
  label.textContent = grade || '待分类药材';
  const summary = document.createElement('span');
  summary.className = 'herb-grade-summary';
  const count = document.createElement('span');
  count.className = 'herb-grade-count';
  count.textContent = '0 种';
  const chevron = document.createElement('span');
  chevron.className = 'herb-grade-chevron';
  chevron.setAttribute('aria-hidden', 'true');
  chevron.textContent = expanded ? '⌃' : '⌄';
  summary.appendChild(count);
  summary.appendChild(chevron);
  toggle.appendChild(label);
  toggle.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'herb-grade-body';
  body.hidden = !expanded;
  toggle.addEventListener('click', () => {
    const nextExpanded = toggle.getAttribute('aria-expanded') !== 'true';
    setHerbGradeExpanded(section, nextExpanded);
    if (nextExpanded && grade) lastExpandedHerbGrade = grade;
  });
  section.appendChild(toggle);
  section.appendChild(body);
  return section;
}

function setHerbGradeExpanded(section, expanded) {
  const toggle = section.querySelector('.herb-grade-toggle');
  const body = section.querySelector('.herb-grade-body');
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  toggle.querySelector('.herb-grade-chevron').textContent = expanded ? '⌃' : '⌄';
  body.hidden = !expanded;
}

function findHerbGradeSection(grade) {
  return [...document.querySelectorAll('#herb-list .herb-grade')]
    .find(section => section.dataset.grade === grade);
}

function expandHerbGrade(grade) {
  const section = findHerbGradeSection(grade);
  if (!section) return;
  setHerbGradeExpanded(section, true);
  if (grade) lastExpandedHerbGrade = grade;
}

function updateHerbGradeCounts() {
  document.querySelectorAll('#herb-list .herb-grade').forEach(section => {
    const count = section.querySelectorAll(':scope > .herb-grade-body > .herb-row').length;
    section.querySelector('.herb-grade-count').textContent = `${count} 种`;
  });
}

function appendHerbRow(grade, name, price) {
  const section = findHerbGradeSection(grade);
  if (!section) throw new Error(`未找到药材品级：${grade || '待分类'}`);
  const row = makeHerbRow(grade, name, price);
  section.querySelector('.herb-grade-body').appendChild(row);
  updateHerbGradeCounts();
  return row;
}

function makeHerbRow(grade, name, price) {
  const row = document.createElement('div');
  row.className = 'herb-row';
  const gradeSelect = document.createElement('select');
  gradeSelect.className = 'herb-grade-select';
  gradeSelect.setAttribute('aria-label', `${name || '新药材'}的品级`);
  if (!grade) {
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '请选择品级';
    placeholder.selected = true;
    placeholder.disabled = true;
    gradeSelect.appendChild(placeholder);
  }
  HERB_GRADES.forEach(optionGrade => {
    const option = document.createElement('option');
    option.value = optionGrade;
    option.textContent = optionGrade;
    option.selected = optionGrade === grade;
    gradeSelect.appendChild(option);
  });
  const nameInp = document.createElement('input');
  nameInp.type = 'text';
  nameInp.className = 'herb-name-input';
  nameInp.value = name;
  nameInp.placeholder = '药材名';
  nameInp.setAttribute('aria-label', '药材名');
  const priceInp = document.createElement('input');
  priceInp.type = 'number';
  priceInp.className = 'herb-price-input';
  priceInp.step = '0.01';
  priceInp.value = price;
  priceInp.placeholder = '价格（万）';
  priceInp.setAttribute('aria-label', `${name || '新药材'}的最高价格（万灵石）`);
  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'herb-delete-button';
  delBtn.textContent = '×';
  delBtn.title = '删除药材';
  delBtn.setAttribute('aria-label', '删除药材');
  delBtn.addEventListener('click', () => {
    row.remove();
    updateHerbGradeCounts();
  });
  gradeSelect.addEventListener('change', () => {
    const targetSection = findHerbGradeSection(gradeSelect.value);
    if (!targetSection) return;
    targetSection.querySelector('.herb-grade-body').appendChild(row);
    expandHerbGrade(gradeSelect.value);
    clearHerbRowError(row);
    updateHerbGradeCounts();
  });
  row.appendChild(gradeSelect);
  row.appendChild(nameInp);
  row.appendChild(priceInp);
  row.appendChild(delBtn);
  return row;
}

function clearHerbRowError(row) {
  row.classList.remove('invalid');
  row.querySelectorAll('[aria-invalid="true"]').forEach(control => {
    control.removeAttribute('aria-invalid');
  });
}

function saveHerbPrices() {
  const rows = [...document.querySelectorAll('#herb-list .herb-row')];
  rows.forEach(clearHerbRowError);
  const result = validateHerbRows(rows.map(row => ({
    grade: row.querySelector('.herb-grade-select').value,
    name: row.querySelector('.herb-name-input').value,
    price: row.querySelector('.herb-price-input').value,
  })));
  if (!result.ok) {
    const messages = {
      'invalid-grade': '请选择一品至九品中的药材品级',
      'empty-name': '药材名不能为空',
      'invalid-price': '药材价格必须大于 0',
      'duplicate-name': '同一种药材只能配置一次',
    };
    result.errors.forEach(error => {
      const row = rows[error.index];
      if (!row) return;
      row.classList.add('invalid');
      const selector = error.code === 'invalid-grade'
        ? '.herb-grade-select'
        : error.code === 'empty-name' || error.code === 'duplicate-name'
          ? '.herb-name-input'
          : '.herb-price-input';
      row.querySelector(selector).setAttribute('aria-invalid', 'true');
    });
    toast('❌ 无法保存：' + messages[result.errors[0].code], false);
    return;
  }
  const btn = document.getElementById('save-herb-btn');
  btn.disabled = true;
  btn.textContent = '保存中…';
  bridge.apiPost('herb_prices/save', {
    self_id: currentAccount,
    groups: result.groups,
  })
    .then(() => toast('✅ 药材价格已保存并生效', true))
    .catch(e => toast('❌ 保存失败：' + (e && e.message ? e.message : e), false))
    .finally(() => { btn.disabled = false; btn.textContent = '保存价格'; });
}

function renderTabs() {
  tabsEl.innerHTML = '';
  SPECIAL_TABS.forEach(t => {
    const b = document.createElement('button');
    b.dataset.key = t.key;
    b.innerHTML = `<span class="tab-icon">${t.icon}</span><span>${t.label}</span>`;
    b.addEventListener('click', () => renderTab(t.key));
    tabsEl.appendChild(b);
  });
  for (const [key, item] of Object.entries(schema)) {
    if (!item || typeof item !== 'object') continue;
    const b = document.createElement('button');
    b.dataset.key = key;
    b.textContent = item.description || key;
    b.addEventListener('click', () => renderTab(key));
    tabsEl.appendChild(b);
  }
  if (tabsEl.children.length > 0) renderTab(tabsEl.children[0].dataset.key);
}

searchEl.addEventListener('input', () => {
  const q = searchEl.value.trim().toLowerCase();
  [...formEl.querySelectorAll('.field')].forEach(f => {
    const text = ((f.dataset.label || '') + ' ' + (f.dataset.path || '')).toLowerCase();
    f.style.display = (!q || text.includes(q)) ? '' : 'none';
  });
});

saveBtn.addEventListener('click', async () => {
  if (!currentAccount) return;
  const selfId = currentAccount;
  saveBtn.disabled = true;
  accountSelectEl.disabled = true;
  saveBtn.textContent = '保存中…';
  try {
    syncFormToConfig();
    const res = await bridge.apiPost('config/save', { self_id: selfId, config });
    if (!res || res.ok === false) throw new Error((res && res.error) || '服务未确认保存结果');
    const reloaded = !!res.reloaded;
    toast(reloaded ? '✅ 当前账号配置已保存并生效' : '✅ 当前账号配置已保存', true);
    statusLine.textContent = reloaded
      ? `账号 ${selfId} 的配置已保存并生效，其他账号未重载。`
      : `账号 ${selfId} 的配置已保存，等待下次热重载生效。`;
  } catch (e) {
    toast('❌ 保存失败：' + (e && e.message ? e.message : e), false);
  } finally {
    saveBtn.disabled = false;
    accountSelectEl.disabled = false;
    saveBtn.textContent = '保存当前账号';
  }
});

async function loadAccount(selfId) {
  const generation = ++accountLoadGeneration;
  currentAccount = selfId;
  saveBtn.disabled = true;
  formEl.innerHTML = '<div class="loading">加载账号配置…</div>';
  try {
    const data = await bridge.apiPost('config/load', { self_id: selfId });
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    schema = (data && data.schema) || {};
    config = (data && data.config) || {};
    renderTabs();
    statusLine.textContent = `当前账号 ${currentAccount}`;
  } catch (e) {
    if (generation !== accountLoadGeneration || selfId !== currentAccount) return;
    formEl.innerHTML = '<div class="loading">加载配置失败：' + (e && e.message ? e.message : e) + '</div>';
  } finally {
    if (generation === accountLoadGeneration && selfId === currentAccount) {
      saveBtn.disabled = !currentAccount;
    }
  }
}

async function init() {
  try {
    await bridge.ready();
  } catch (e) {
    formEl.innerHTML = '<div class="loading">bridge 初始化失败，请通过插件详情页打开本页面。</div>';
    return;
  }
  const applyTheme = () => {
    const ctx = bridge.getContext();
    document.documentElement.setAttribute('data-theme', ctx && ctx.isDark ? 'dark' : 'light');
  };
  applyTheme();
  bridge.onContext(applyTheme);
  try {
    const data = await bridge.apiGet('accounts');
    const accounts = (data && data.accounts) || [];
    accountSelectEl.innerHTML = '';
    accounts.forEach(account => {
      const option = document.createElement('option');
      option.value = account.self_id;
      const groups = Array.isArray(account.groups) && account.groups.length ? ` · ${account.groups.join('、')}` : '';
      option.textContent = `${account.self_id}${groups}`;
      accountSelectEl.appendChild(option);
    });
    if (!accounts.length) {
      accountSelectEl.disabled = true;
      saveBtn.disabled = true;
      formEl.innerHTML = '<div class="loading">暂无已配置或已绑定账号</div>';
      return;
    }
    accountSelectEl.addEventListener('change', () => loadAccount(accountSelectEl.value));
    await loadAccount(accounts[0].self_id);
  } catch (e) {
    formEl.innerHTML = '<div class="loading">加载账号失败：' + (e && e.message ? e.message : e) + '</div>';
    return;
  }
  try {
    const st = await bridge.apiGet('status');
    statusLine.textContent = `当前账号 ${currentAccount} · 绑定会话 ${st.bound_keys ?? 0}`;
  } catch (e) {
    statusLine.textContent = '';
  }
}

init();
