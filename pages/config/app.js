const _realBridge = window.AstrBotPluginPage;
const PREVIEW_MODE = !_realBridge;

const MOCK_SCHEMA = {
  official_bot_qq: { description: '官方机器人 QQ 号', type: 'string', hint: '所有需要 @小小 的指令都会使用这里的号码。', default: '3889001741' },
  test_mode: { description: '测试模式', type: 'bool', hint: 'false 为真实发送指令，true 通常用于调试时避免部分真实操作。', default: false },
  bounty: { description: '悬赏模块配置', type: 'object', hint: '控制悬赏策略、重试时间、每日启动时间和随机抖动。', items: {
    default_strategy: { description: '默认悬赏策略', type: 'string', hint: '修为：优先修为收益；价值：优先额外机缘价值；耗时：优先最短耗时。', options: ['修为','价值','耗时'], default: '价值' },
    retry_when_running_sec: { description: '运行中重试间隔（秒）', type: 'int', default: 30 },
    next_morning_hour: { description: '次日启动小时', type: 'int', default: 8 },
  }},
};
const MOCK_CONFIG = {
  official_bot_qq: '3889001741',
  test_mode: false,
  bounty: { default_strategy: '价值', retry_when_running_sec: 30, next_morning_hour: 8 },
};

const bridge = _realBridge || {
  ready: async () => ({ pluginName: 'preview', isDark: false }),
  getContext: () => ({ isDark: false }),
  onContext: () => {},
  apiGet: async (ep) => {
    if (ep === 'config') return { config: MOCK_CONFIG, schema: MOCK_SCHEMA };
    if (ep === 'status') return { bound_keys: 3, test_mode: false, market_price_enabled: true };
    if (ep === 'alchemy_rules') return { whitelist_pill: ['培元丹','回元丹','养元丹'], blacklist_equip: ['龙渊剑','惊雷'], blacklist_artifact: ['两仪心经'] };
    if (ep === 'herb_prices') return { prices: { '罗犀草': 100, '何首乌': 500, '九叶芝': 2000, '地心火芝': 8000 } };
    return {};
  },
  apiPost: async (ep, body) => { console.log('[preview] save', ep, body); return { ok: true, reloaded: true }; },
};

const tabsEl = document.getElementById('tabs');
const formEl = document.getElementById('form-area');
const searchEl = document.getElementById('search');
const saveBtn = document.getElementById('save-btn');
const statusLine = document.getElementById('status-line');

let schema = {};
let config = {};
let currentTab = null;

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
  saveBtn.style.display = 'none';
  searchEl.style.display = 'none';
  [...tabsEl.children].forEach(b => b.classList.toggle('active', b.dataset.key === '__alchemy'));
  formEl.innerHTML = '<div class="loading">加载炼金名单…</div>';
  bridge.apiGet('alchemy_rules').then(data => {
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
    formEl.innerHTML = '<div class="loading">加载名单失败：' + (e && e.message ? e.message : e) + '</div>';
  });
}

function saveAlchemyRules() {
  const get = id => document.getElementById(id).value.split('\n').map(s => s.trim()).filter(Boolean);
  const payload = { whitelist_pill: get('wl-pill'), blacklist_equip: get('bl-equip'), blacklist_artifact: get('bl-art') };
  const btn = document.getElementById('save-rules-btn');
  btn.disabled = true; btn.textContent = '保存中…';
  bridge.apiPost('alchemy_rules/save', payload)
    .then(() => toast('✅ 名单已保存并生效', true))
    .catch(e => toast('❌ 保存失败：' + (e && e.message ? e.message : e), false))
    .finally(() => { btn.disabled = false; btn.textContent = '保存名单'; });
}

function renderHerbTab() {
  saveBtn.style.display = 'none';
  searchEl.style.display = 'none';
  [...tabsEl.children].forEach(b => b.classList.toggle('active', b.dataset.key === '__herb'));
  formEl.innerHTML = '<div class="loading">加载药材上限价…</div>';
  bridge.apiGet('herb_prices').then(data => {
    const prices = (data && data.prices) || {};
    formEl.innerHTML = `
      <div class="section-title">🌿 药材购买上限价</div>
      <div class="section-hint">自动炼丹购买每种药材时的最高可接受价格（单位：万灵石）。超过此价的药材不会自动购买。保存后下次购买即生效。</div>
      <div id="herb-list"></div>
      <div class="toolbar-row">
        <button id="add-herb-btn">+ 添加药材</button>
        <button id="save-herb-btn" class="primary">保存价格</button>
      </div>
    `;
    const listEl = document.getElementById('herb-list');
    const entries = Object.entries(prices);
    if (entries.length === 0) entries.push(['', '']);
    entries.forEach(([name, price]) => listEl.appendChild(makeHerbRow(name, price)));
    document.getElementById('add-herb-btn').addEventListener('click', () => listEl.appendChild(makeHerbRow('', '')));
    document.getElementById('save-herb-btn').addEventListener('click', saveHerbPrices);
  }).catch(e => {
    formEl.innerHTML = '<div class="loading">加载药材价格失败：' + (e && e.message ? e.message : e) + '</div>';
  });
}

function makeHerbRow(name, price) {
  const row = document.createElement('div');
  row.className = 'herb-row';
  const nameInp = document.createElement('input'); nameInp.type = 'text'; nameInp.value = name; nameInp.placeholder = '药材名';
  nameInp.style.maxWidth = '260px';
  const priceInp = document.createElement('input'); priceInp.type = 'number'; priceInp.step = '0.01'; priceInp.value = price; priceInp.placeholder = '价格(万)';
  priceInp.style.maxWidth = '140px';
  const delBtn = document.createElement('button'); delBtn.textContent = '删除';
  delBtn.addEventListener('click', () => row.remove());
  row.appendChild(nameInp); row.appendChild(priceInp); row.appendChild(delBtn);
  return row;
}

function saveHerbPrices() {
  const rows = document.querySelectorAll('#herb-list .herb-row');
  const prices = {};
  rows.forEach(row => {
    const inputs = row.querySelectorAll('input');
    const name = inputs[0].value.trim();
    const price = parseFloat(inputs[1].value);
    if (name && !isNaN(price) && price > 0) prices[name] = price;
  });
  const btn = document.getElementById('save-herb-btn');
  btn.disabled = true; btn.textContent = '保存中…';
  bridge.apiPost('herb_prices/save', { prices })
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
  if (!confirm('保存后将热重载全部模块，进行中的操作会被中断。确认保存？')) return;
  saveBtn.disabled = true;
  saveBtn.textContent = '保存中…';
  try {
    const res = await bridge.apiPost('config/save', { config });
    if (res && res.reloaded) {
      toast('✅ 已保存并热重载', true);
      statusLine.textContent = '配置已生效。进行中的操作已被中断并重新加载。';
    } else {
      toast('⚠️ 已保存，但热重载未完成', false);
      statusLine.textContent = '配置已写入，请手动重载插件使其完全生效。';
    }
  } catch (e) {
    toast('❌ 保存失败：' + (e && e.message ? e.message : e), false);
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = '保存并热重载';
  }
});

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
    const data = await bridge.apiGet('config');
    schema = (data && data.schema) || {};
    config = (data && data.config) || {};
    renderTabs();
  } catch (e) {
    formEl.innerHTML = '<div class="loading">加载配置失败：' + (e && e.message ? e.message : e) + '</div>';
    return;
  }
  try {
    const st = await bridge.apiGet('status');
    statusLine.textContent = `运行中 · 绑定会话 ${st.bound_keys ?? 0} · 测试模式 ${st.test_mode ? '开' : '关'} · 坊市价格 ${st.market_price_enabled ? '开' : '关'}`;
  } catch (e) {
    statusLine.textContent = '';
  }
}

init();
