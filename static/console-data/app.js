/* Console Data Dashboard — widget grid + 3 widget types
 * Uses Gridstack 10.x. Persists layout in localStorage.
 * Auth: JWT (redirect to /login/ if missing).
 */
(() => {
  'use strict';

  // ─── Mock mode (?mock=1 ใน URL) ─────────────────────────────────────
  // ใช้สำหรับดู visual preview โดยไม่ต้องมี backend / login
  const IS_MOCK = new URLSearchParams(location.search).has('mock');

  // ─── Auth gate ─────────────────────────────────────────────────────
  const token = localStorage.getItem('jwt_token');
  if (!IS_MOCK && !token) {
    window.location.href = '/login/';
    return;
  }
  const AUTH_HEADER = token ? { Authorization: 'Bearer ' + token } : {};

  if (IS_MOCK) {
    document.getElementById('user-email').textContent = 'mock@preview.local';
    document.getElementById('avatar').textContent = 'M';
    document.body.classList.add('user-is-admin');
  } else {
    // Optimistic admin flag from localStorage — กัน flicker
    if (localStorage.getItem('is_admin') === 'true') {
      document.body.classList.add('user-is-admin');
    }

    // sync จาก server — update localStorage + body class + avatar
    fetch('/auth/me', { headers: AUTH_HEADER })
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (!u) return;
        document.getElementById('user-email').textContent = u.email || '';
        document.getElementById('avatar').textContent = (u.email || '?').charAt(0).toUpperCase();
        const isAdmin = Boolean(u.is_admin);
        localStorage.setItem('is_admin', String(isAdmin));
        document.body.classList.toggle('user-is-admin', isAdmin);
      })
      .catch(() => {});
  }

  document.getElementById('logout-btn')?.addEventListener('click', () => {
    localStorage.removeItem('jwt_token');
    localStorage.removeItem('is_admin');
    window.location.href = '/login/';
  });

  // ─── Constants ─────────────────────────────────────────────────────
  const LAYOUT_KEY = 'console_data_layout_v1';
  const FLAGS = {
    USD: '🇺🇸', EUR: '🇪🇺', GBP: '🇬🇧', JPY: '🇯🇵',
    CHF: '🇨🇭', CAD: '🇨🇦', AUD: '🇦🇺', NZD: '🇳🇿',
  };
  const WIDGET_DEFAULTS = {
    'currency-strength': { w: 4, h: 5, tf: 'H1' },
    'market-snapshot':   { w: 5, h: 5, symbols: 'EURUSD,XAUUSD,US500,BTCUSD' },
    'news-calendar':     { w: 5, h: 6, impact: 'all', hours: 24 },
    'pair-cluster':      { w: 5, h: 6, tf: 'H1', k: 3 },
    'trend-matrix':      { w: 4, h: 5 },
    'set100-template':   { w: 6, h: 6 },
  };

  // ─── Grid init ─────────────────────────────────────────────────────
  const grid = GridStack.init({
    column: 12,
    cellHeight: 70,
    margin: 8,
    float: true,
    handle: '.widget-header',
    resizable: { handles: 'se' },
    minRow: 1,
  });

  const emptyState = document.getElementById('empty-state');
  const gridEl = document.getElementById('widget-grid');

  function refreshEmptyState() {
    const hasWidgets = grid.engine.nodes.length > 0;
    emptyState.classList.toggle('hidden', hasWidgets);
    gridEl.classList.toggle('hidden', !hasWidgets);
  }

  // ─── Persistence (server-synced + localStorage cache) ──────────────
  const DASHBOARD_KEY = 'console-data';
  const SAVE_DEBOUNCE_MS = 800;
  let _saveTimer = null;
  let _bootLoading = true;  // ตอน load layout ครั้งแรก ห้าม save (กัน race)

  function _collectLayout() {
    return grid.engine.nodes.map(n => ({
      x: n.x, y: n.y, w: n.w, h: n.h,
      type: n.el.dataset.widgetType,
      config: JSON.parse(n.el.dataset.widgetConfig || '{}'),
    }));
  }

  function saveLayout() {
    if (_bootLoading) return;
    const nodes = _collectLayout();
    // local cache (instant)
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(nodes));
    // server sync (debounced)
    if (IS_MOCK) return;  // mock mode: skip server
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(async () => {
      try {
        await fetch(`/api/dashboards/${DASHBOARD_KEY}`, {
          method: 'PUT',
          headers: { ...AUTH_HEADER, 'Content-Type': 'application/json' },
          body: JSON.stringify({ layout: nodes }),
        });
      } catch (e) {
        console.warn('dashboard save failed:', e.message);
      }
    }, SAVE_DEBOUNCE_MS);
  }

  async function loadLayout() {
    _bootLoading = true;
    let saved = [];

    if (IS_MOCK) {
      // mock mode: localStorage only
      try { saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '[]'); } catch {}
    } else {
      // try server first
      try {
        const resp = await fetch(`/api/dashboards/${DASHBOARD_KEY}`, { headers: AUTH_HEADER });
        if (resp.ok) {
          const data = await resp.json();
          saved = data.layout || [];
        } else {
          throw new Error(`HTTP ${resp.status}`);
        }
      } catch (e) {
        // fallback: localStorage (offline / server down)
        console.warn('dashboard load failed, using localStorage cache:', e.message);
        try { saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '[]'); } catch {}
      }
    }

    saved.forEach(n => addWidget(n.type, n.config || {}, { x: n.x, y: n.y, w: n.w, h: n.h }));
    refreshEmptyState();
    _bootLoading = false;
  }

  grid.on('change', saveLayout);
  grid.on('added removed', () => { saveLayout(); refreshEmptyState(); });

  // ─── Widget factory ────────────────────────────────────────────────
  let widgetSeq = 0;

  function widgetTypeExists(type) {
    return gridEl.querySelector(`.grid-stack-item[data-widget-type="${type}"]`) !== null;
  }

  function addWidget(type, config = {}, pos = {}) {
    // กัน widget type ซ้ำ — มี 1 ตัวต่อ type
    if (widgetTypeExists(type)) {
      console.warn(`widget type "${type}" มีอยู่แล้ว — ข้าม`);
      return;
    }
    const defaults = WIDGET_DEFAULTS[type] || { w: 4, h: 4 };
    const cfg = { ...defaults, ...config };
    const id = `w-${++widgetSeq}-${Date.now()}`;

    const shell = document.getElementById('widget-shell').content.cloneNode(true);
    const card = shell.querySelector('.widget-card');
    card.dataset.widgetType = type;
    card.dataset.widgetConfig = JSON.stringify(cfg);
    card.dataset.widgetId = id;

    const wrapper = document.createElement('div');
    wrapper.className = 'grid-stack-item';
    wrapper.dataset.widgetType = type;
    wrapper.dataset.widgetConfig = JSON.stringify(cfg);
    wrapper.setAttribute('gs-w', String(pos.w ?? cfg.w));
    wrapper.setAttribute('gs-h', String(pos.h ?? cfg.h));
    if (pos.x !== undefined) wrapper.setAttribute('gs-x', String(pos.x));
    if (pos.y !== undefined) wrapper.setAttribute('gs-y', String(pos.y));
    if (pos.x === undefined) wrapper.setAttribute('gs-auto-position', 'true');

    const content = document.createElement('div');
    content.className = 'grid-stack-item-content';
    content.appendChild(card);
    wrapper.appendChild(content);

    // Gridstack v10: element must be child ของ .grid-stack ก่อนเรียก makeWidget
    gridEl.appendChild(wrapper);
    grid.makeWidget(wrapper);

    initWidget(card, type, cfg);
    bindMenu(card);
    refreshEmptyState();
  }

  function initWidget(card, type, cfg) {
    const header = card.querySelector('.widget-header');
    const title = card.querySelector('.widget-title');
    const icon = card.querySelector('.widget-icon');
    const toolbar = card.querySelector('.widget-toolbar');
    const body = card.querySelector('.widget-body');
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';

    if (type === 'currency-strength') {
      icon.textContent = '💪';
      title.textContent = 'Currency Strength';
      const sel = document.createElement('select');
      sel.innerHTML = '<option value="H1">H1</option><option value="D1">D1</option>';
      sel.value = cfg.tf || 'H1';
      sel.addEventListener('change', () => {
        cfg.tf = sel.value;
        persistConfig(card, cfg);
        loadCurrencyStrength(body, cfg);
      });
      toolbar.appendChild(sel);
      loadCurrencyStrength(body, cfg);
    }
    else if (type === 'market-snapshot') {
      icon.textContent = '📈';
      title.textContent = 'Market Snapshot';
      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'EURUSD,XAUUSD,...';
      input.value = cfg.symbols || '';
      input.style.width = '180px';
      let timer;
      input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          cfg.symbols = input.value;
          persistConfig(card, cfg);
          loadMarketSnapshot(body, cfg);
        }, 600);
      });
      toolbar.appendChild(input);
      loadMarketSnapshot(body, cfg);
      // auto-refresh
      const interval = setInterval(() => {
        if (!document.body.contains(card)) { clearInterval(interval); return; }
        loadMarketSnapshot(body, cfg);
      }, 30_000);
    }
    else if (type === 'pair-cluster') {
      icon.textContent = '🔗';
      title.textContent = 'Pair Cluster';
      const tfSel = document.createElement('select');
      tfSel.innerHTML = '<option value="H1">H1</option><option value="D1">D1</option>';
      tfSel.value = cfg.tf || 'H1';
      tfSel.addEventListener('change', () => {
        cfg.tf = tfSel.value;
        persistConfig(card, cfg);
        loadPairCluster(body, cfg);
      });
      const kSel = document.createElement('select');
      kSel.innerHTML = '<option value="2">k=2</option><option value="3">k=3</option><option value="4">k=4</option><option value="5">k=5</option>';
      kSel.value = String(cfg.k || 3);
      kSel.addEventListener('change', () => {
        cfg.k = parseInt(kSel.value, 10);
        persistConfig(card, cfg);
        loadPairCluster(body, cfg);
      });
      toolbar.appendChild(tfSel);
      toolbar.appendChild(kSel);
      loadPairCluster(body, cfg);
    }
    else if (type === 'trend-matrix') {
      icon.textContent = '🌡️';
      title.textContent = 'Trend Strength Matrix';
      loadTrendMatrix(body);
    }
    else if (type === 'set100-template') {
      icon.textContent = '🏆';
      title.textContent = 'SET100 Trend Template';
      loadSet100Template(body);
    }
    else if (type === 'news-calendar') {
      icon.textContent = '📰';
      title.textContent = 'Economic Calendar';
      const sel = document.createElement('select');
      sel.innerHTML = '<option value="all">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>';
      sel.value = cfg.impact || 'all';
      sel.addEventListener('change', () => {
        cfg.impact = sel.value;
        persistConfig(card, cfg);
        loadNewsCalendar(body, cfg);
      });
      toolbar.appendChild(sel);
      loadNewsCalendar(body, cfg);
    }
  }

  function persistConfig(card, cfg) {
    card.dataset.widgetConfig = JSON.stringify(cfg);
    // also update wrapper for save
    const wrapper = card.closest('.grid-stack-item');
    if (wrapper) wrapper.dataset.widgetConfig = JSON.stringify(cfg);
    saveLayout();
  }

  // ─── Data loaders ──────────────────────────────────────────────────
  async function apiGet(url) {
    if (IS_MOCK) {
      // simulate latency
      await new Promise(r => setTimeout(r, 250));
      return mockResponse(url);
    }
    const resp = await fetch(url, { headers: AUTH_HEADER });
    if (resp.status === 401) {
      localStorage.removeItem('jwt_token');
      window.location.href = '/login/';
      throw new Error('unauthorized');
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  // ─── Mock data per endpoint ────────────────────────────────────────
  function mockResponse(url) {
    const u = new URL(url, location.origin);
    const path = u.pathname;
    const now = new Date().toISOString();

    if (path.endsWith('/currency-strength')) {
      const tf = u.searchParams.get('tf') || 'H1';
      // Realistic snapshot — H1 vs D1 mock different distributions
      const mock = tf === 'D1'
        ? [
            { ccy: 'CHF', roc_pct: +1.842, samples: 1 },
            { ccy: 'EUR', roc_pct: +0.731, samples: 3 },
            { ccy: 'JPY', roc_pct: +0.412, samples: 4 },
            { ccy: 'GBP', roc_pct: +0.205, samples: 3 },
            { ccy: 'AUD', roc_pct: -0.118, samples: 2 },
            { ccy: 'NZD', roc_pct: -0.347, samples: 1 },
            { ccy: 'CAD', roc_pct: -0.890, samples: 1 },
            { ccy: 'USD', roc_pct: -1.836, samples: 7 },
          ]
        : [
            { ccy: 'CHF', roc_pct: +0.357, samples: 1 },
            { ccy: 'CAD', roc_pct: +0.320, samples: 1 },
            { ccy: 'NZD', roc_pct: +0.176, samples: 1 },
            { ccy: 'EUR', roc_pct: +0.097, samples: 3 },
            { ccy: 'GBP', roc_pct: +0.072, samples: 3 },
            { ccy: 'AUD', roc_pct: +0.056, samples: 2 },
            { ccy: 'JPY', roc_pct: -0.011, samples: 4 },
            { ccy: 'USD', roc_pct: -0.204, samples: 7 },
          ];
      return { tf, updated_at: now, currencies: mock };
    }

    if (path.endsWith('/market-snapshot')) {
      const syms = (u.searchParams.get('symbols') || '').split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
      const baseMap = {
        EURUSD: { last: 1.13754, atr: 0.00088, chg: -0.025 },
        GBPUSD: { last: 1.27412, atr: 0.00132, chg: +0.084 },
        USDJPY: { last: 152.870, atr: 0.247,   chg: -0.143 },
        XAUUSD: { last: 4024.59, atr: 18.45,   chg: +0.409 },
        US500:  { last: 6128.42, atr: 22.31,   chg: +0.211 },
        BTCUSD: { last: 60172.0, atr: 621.31,  chg: +0.521 },
        ETHUSD: { last: 3245.18, atr: 41.62,   chg: -0.178 },
      };
      const snapshots = syms.map(s => {
        const base = baseMap[s];
        if (!base) return { symbol: s, last: null, spread_pips: null, atr_14: null, change_pct: null, error: 'no_data' };
        return {
          symbol: s,
          last: base.last,
          spread_pips: +(base.atr / 10).toFixed(2),
          atr_14: base.atr,
          change_pct: base.chg,
        };
      });
      return { updated_at: now, snapshots };
    }

    if (path.endsWith('/set100-template')) {
      // generate a synthetic uptrend sparkline for mock
      const trendUp = (start, n, noise = 0.02, drift = 0.008) => {
        const arr = [start];
        for (let i = 1; i < n; i++) {
          arr.push(arr[i - 1] * (1 + drift + (Math.random() - 0.5) * noise));
        }
        return arr.map(v => +v.toFixed(3));
      };
      return {
        computed_at: now,
        total_scanned: 92,
        passing_count: 13,
        passing: [
          { symbol: 'GUNKUL', sparkline: trendUp(1.5, 60, 0.04, 0.018), details: { close: 4.26, pct_from_low_52w: 225.2, pct_from_high_52w: -0.9, rs_rank: 100.0 } },
          { symbol: 'HANA',   sparkline: trendUp(20, 60, 0.03, 0.011),  details: { close: 36.75, pct_from_low_52w: 143.4, pct_from_high_52w: -12.5, rs_rank: 96.7 } },
          { symbol: 'KKP',    sparkline: trendUp(60, 60, 0.02, 0.009),  details: { close: 99.50, pct_from_low_52w: 128.7, pct_from_high_52w: -1.5, rs_rank: 94.6 } },
          { symbol: 'BAY',    sparkline: trendUp(28, 60, 0.02, 0.006),  details: { close: 38.50, pct_from_low_52w: 86.0, pct_from_high_52w: -3.1, rs_rank: 93.5 } },
          { symbol: 'SCGP',   sparkline: trendUp(20, 60, 0.02, 0.006),  details: { close: 27.25, pct_from_low_52w: 82.9, pct_from_high_52w: -5.2, rs_rank: 91.3 } },
          { symbol: 'AOT',    sparkline: trendUp(35, 60, 0.025, 0.01),  details: { close: 62.25, pct_from_low_52w: 132.7, pct_from_high_52w: -0.4, rs_rank: 89.1 } },
          { symbol: 'WHA',    sparkline: trendUp(3.5, 60, 0.025, 0.006),details: { close: 5.05, pct_from_low_52w: 80.4, pct_from_high_52w: -3.8, rs_rank: 88.0 } },
          { symbol: 'BA',     sparkline: trendUp(13, 60, 0.025, 0.006), details: { close: 18.20, pct_from_low_52w: 71.7, pct_from_high_52w: -3.7, rs_rank: 84.8 } },
          { symbol: 'ERW',    sparkline: trendUp(2.2, 60, 0.03, 0.006), details: { close: 3.14, pct_from_low_52w: 75.4, pct_from_high_52w: -5.4, rs_rank: 83.7 } },
          { symbol: 'TOP',    sparkline: trendUp(35, 60, 0.025, 0.005), details: { close: 46.50, pct_from_low_52w: 78.8, pct_from_high_52w: -18.1, rs_rank: 78.3 } },
          { symbol: 'TCAP',   sparkline: trendUp(48, 60, 0.02, 0.005),  details: { close: 65.50, pct_from_low_52w: 48.0, pct_from_high_52w: -1.1, rs_rank: 76.1 } },
          { symbol: 'KBANK',  sparkline: trendUp(160, 60, 0.018, 0.005),details: { close: 214.0, pct_from_low_52w: 45.6, pct_from_high_52w: -0.5, rs_rank: 75.0 } },
          { symbol: 'CPN',    sparkline: trendUp(45, 60, 0.02, 0.006),  details: { close: 66.50, pct_from_low_52w: 62.2, pct_from_high_52w: -5.7, rs_rank: 71.7 } },
        ],
      };
    }

    if (path.endsWith('/trend-matrix')) {
      // per-pair SMA distance — H1 only
      const mock = [
        { sym: 'GBPJPY', s50: +0.129, r50: 4,  s200: -0.116, r200: 4 },
        { sym: 'EURUSD', s50: +0.224, r50: 1,  s200: -0.623, r200: 8 },
        { sym: 'GBPUSD', s50: +0.198, r50: 2,  s200: -0.381, r200: 7 },
        { sym: 'EURJPY', s50: +0.156, r50: 3,  s200: -0.357, r200: 6 },
        { sym: 'USDJPY', s50: -0.059, r50: 7,  s200: +0.273, r200: 3 },
        { sym: 'EURGBP', s50: +0.026, r50: 6,  s200: -0.242, r200: 5 },
        { sym: 'USDCHF', s50: -0.365, r50: 11, s200: +0.412, r200: 1 },
        { sym: 'USDCAD', s50: -0.174, r50: 10, s200: +0.376, r200: 2 },
        { sym: 'NZDUSD', s50: +0.051, r50: 5,  s200: -1.386, r200: 11 },
        { sym: 'AUDUSD', s50: -0.098, r50: 8,  s200: -1.316, r200: 10 },
        { sym: 'AUDJPY', s50: -0.161, r50: 9,  s200: -1.048, r200: 9 },
      ];
      return {
        tf: 'H1',
        method: 'SMA distance (per pair)',
        columns: ['50 SMA', '200 SMA'],
        computed_at: now,
        rows: mock.map(m => ({
          symbol: m.sym,
          cells: {
            '50 SMA':  { dist_pct: m.s50, rank: m.r50, sma: 1.0, last: 1.0 },
            '200 SMA': { dist_pct: m.s200, rank: m.r200, sma: 1.0, last: 1.0 },
          },
          avg_rank: (m.r50 + m.r200) / 2,
        })).sort((a, b) => a.avg_rank - b.avg_rank),
      };
    }

    if (path.endsWith('/pair-cluster')) {
      const tf = u.searchParams.get('tf') || 'H1';
      const k = parseInt(u.searchParams.get('k') || '3', 10);
      return {
        tf, k, bars_used: 100, total_pairs: 11, missing_symbols: [],
        clusters: [
          { id: 2, members: ['USDCAD', 'USDCHF', 'USDJPY'],
            size: 3, cum_return_pct: 0.520, vol_pct: 0.056, sharpe_approx: 0.92 },
          { id: 0, members: ['EURGBP'],
            size: 1, cum_return_pct: -0.155, vol_pct: 0.037, sharpe_approx: -0.42 },
          { id: 1, members: ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'EURJPY', 'GBPJPY', 'AUDJPY'],
            size: 7, cum_return_pct: -0.841, vol_pct: 0.075, sharpe_approx: -1.13 },
        ],
      };
    }

    if (path.endsWith('/news-calendar')) {
      const impact = u.searchParams.get('impact') || 'all';
      const inHours = (h) => new Date(Date.now() + h * 3600_000).toISOString();
      const all = [
        { time_utc: inHours(2),  currency: 'USD', title: 'Core CPI m/m',          impact: 'high',   forecast: '0.3%',  previous: '0.4%' },
        { time_utc: inHours(4),  currency: 'EUR', title: 'ECB President Speech',  impact: 'high',   forecast: null,    previous: null },
        { time_utc: inHours(6),  currency: 'GBP', title: 'Manufacturing PMI',     impact: 'medium', forecast: '49.8',  previous: '49.6' },
        { time_utc: inHours(9),  currency: 'JPY', title: 'BOJ Policy Statement',  impact: 'high',   forecast: null,    previous: null },
        { time_utc: inHours(11), currency: 'USD', title: 'Crude Oil Inventories', impact: 'medium', forecast: '-1.2M', previous: '+0.3M' },
        { time_utc: inHours(14), currency: 'AUD', title: 'Employment Change',     impact: 'high',   forecast: '15.2K', previous: '32.6K' },
        { time_utc: inHours(18), currency: 'CAD', title: 'Retail Sales m/m',      impact: 'medium', forecast: '0.4%',  previous: '0.7%' },
        { time_utc: inHours(22), currency: 'CHF', title: 'SNB Chairman Speech',   impact: 'low',    forecast: null,    previous: null },
      ];
      const filtered = impact === 'all' ? all : all.filter(e => e.impact === impact);
      return { events: filtered };
    }

    return { error: 'mock not implemented for ' + path };
  }

  function fmt(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    return Number(n).toFixed(digits);
  }

  function renderError(body, msg) {
    body.innerHTML = `<div class="text-danger text-xs p-2">${msg}</div>`;
  }

  // Currency Strength
  async function loadCurrencyStrength(body, cfg) {
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';
    try {
      const data = await apiGet(`/api/widgets/currency-strength?tf=${cfg.tf}`);
      const max = Math.max(...data.currencies.map(c => Math.abs(c.roc_pct)), 0.01);
      const html = data.currencies.map(c => {
        const pct = (Math.abs(c.roc_pct) / max) * 50;  // % half-width
        const sign = c.roc_pct >= 0 ? 'pos' : 'neg';
        return `
          <div class="cs-row">
            <div class="cs-flag-ccy">${FLAGS[c.ccy] || ''}<span>${c.ccy}</span></div>
            <div class="cs-bar-wrap">
              <div class="cs-bar-axis"></div>
              <div class="cs-bar ${sign}" style="width: ${pct.toFixed(1)}%"></div>
            </div>
            <div class="cs-val ${sign}">${c.roc_pct >= 0 ? '+' : ''}${fmt(c.roc_pct, 2)}%</div>
          </div>`;
      }).join('');
      const updated = new Date(data.updated_at).toLocaleTimeString();
      body.innerHTML = html + `<div class="text-fg-subtle text-[10px] mt-2">TF: ${data.tf} · Updated ${updated}</div>`;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  // Market Snapshot
  async function loadMarketSnapshot(body, cfg) {
    try {
      const data = await apiGet(`/api/widgets/market-snapshot?symbols=${encodeURIComponent(cfg.symbols)}`);
      const rows = data.snapshots.map(s => {
        const chgClass = (s.change_pct ?? 0) >= 0 ? 'pos' : 'neg';
        const chgSign = (s.change_pct ?? 0) >= 0 ? '+' : '';
        return `<tr>
          <td>${s.symbol}</td>
          <td>${fmt(s.last, 5)}</td>
          <td>${fmt(s.spread_pips, 2)}</td>
          <td>${fmt(s.atr_14, 5)}</td>
          <td class="${chgClass}">${chgSign}${fmt(s.change_pct, 2)}%</td>
        </tr>`;
      }).join('');
      const updated = new Date(data.updated_at).toLocaleTimeString();
      body.innerHTML = `
        <table class="ms-table">
          <thead><tr><th>Symbol</th><th>Last</th><th>Spread</th><th>ATR(14)</th><th>Chg%</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="text-fg-subtle text-[10px] mt-2">Updated ${updated}</div>`;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  // SET100 Trend Template (Minervini)
  function sparklineSVG(values, width = 80, height = 22) {
    if (!values || values.length < 2) return '';
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = (max - min) || 1;
    const stepX = width / (values.length - 1);
    const points = values.map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const up = values[values.length - 1] >= values[0];
    const stroke = up ? 'var(--accent, #10B981)' : 'var(--danger, #F87171)';
    const fill = up ? 'rgba(16,185,129,0.12)' : 'rgba(248,113,113,0.12)';
    const area = `0,${height} ${points} ${width},${height}`;
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" class="st-spark">
      <polygon points="${area}" fill="${fill}" stroke="none"/>
      <polyline points="${points}" fill="none" stroke="${stroke}" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>`;
  }

  async function loadSet100Template(body) {
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';
    try {
      const data = await apiGet('/api/widgets/set100-template');
      const passing = data.passing || [];

      const passRows = passing.map(p => {
        const d = p.details;
        const rs = d.rs_rank !== null ? fmt(d.rs_rank, 1) : '—';
        const lowPct = d.pct_from_low_52w;
        const highPct = d.pct_from_high_52w;
        return `<tr class="st-pass">
          <td class="st-sym">${p.symbol}</td>
          <td class="st-spark-cell">${sparklineSVG(p.sparkline)}</td>
          <td>${fmt(d.close, 2)}</td>
          <td class="${lowPct >= 0 ? 'pos' : 'neg'}">${lowPct >= 0 ? '+' : ''}${fmt(lowPct, 1)}%</td>
          <td class="${highPct >= 0 ? 'pos' : 'neg'}">${fmt(highPct, 1)}%</td>
          <td>${rs}</td>
        </tr>`;
      }).join('');

      const head = `<thead><tr>
        <th>Symbol</th><th>60d</th><th>Close</th><th>52wL</th><th>52wH</th><th>RS</th>
      </tr></thead>`;

      const updated = new Date(data.computed_at).toLocaleTimeString();
      body.innerHTML = `
        <div class="st-summary">
          <span><strong>${data.passing_count}</strong>/${data.total_scanned} ผ่าน Minervini</span>
          <span class="text-fg-subtle text-[10px]">${updated}</span>
        </div>
        ${passing.length ? `
          <table class="st-table">${head}<tbody>${passRows}</tbody></table>
        ` : '<div class="text-fg-subtle text-xs p-2">ยังไม่มีหุ้นผ่าน — รอ scheduler</div>'}
      `;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  // Trend Strength Matrix — 4-quadrant scatter (x=200 SMA, y=50 SMA)
  async function loadTrendMatrix(body) {
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';
    try {
      const data = await apiGet('/api/widgets/trend-matrix');
      const rows = data.rows || [];

      // หา max absolute สำหรับ scale axis (symmetric around 0)
      let maxAbs = 0.01;
      rows.forEach(r => {
        const c50 = r.cells['50 SMA'];
        const c200 = r.cells['200 SMA'];
        if (c50) maxAbs = Math.max(maxAbs, Math.abs(c50.dist_pct));
        if (c200) maxAbs = Math.max(maxAbs, Math.abs(c200.dist_pct));
      });
      const axisMax = maxAbs * 1.15;  // padding 15%

      // SVG geometry
      const W = 320, H = 280, PAD = 28;
      const innerW = W - PAD * 2, innerH = H - PAD * 2;
      const cx = PAD + innerW / 2, cy = PAD + innerH / 2;
      // x = 200 SMA (long-term), y = 50 SMA (short-term, inverted because SVG y-down)
      const x2px = v => cx + (v / axisMax) * (innerW / 2);
      const y2px = v => cy - (v / axisMax) * (innerH / 2);

      // 4 quadrant labels + bg color
      const quadBg = `
        <rect x="${cx}" y="${PAD}" width="${innerW / 2}" height="${innerH / 2}" fill="rgba(16,185,129,0.06)"/>
        <rect x="${PAD}" y="${PAD}" width="${innerW / 2}" height="${innerH / 2}" fill="rgba(249,168,38,0.05)"/>
        <rect x="${PAD}" y="${cy}" width="${innerW / 2}" height="${innerH / 2}" fill="rgba(248,113,113,0.06)"/>
        <rect x="${cx}" y="${cy}" width="${innerW / 2}" height="${innerH / 2}" fill="rgba(111,143,216,0.05)"/>`;

      const quadLabels = `
        <text x="${cx + innerW / 4}" y="${PAD + 10}" class="tm-quad-label tm-q-bull"   text-anchor="middle">Strong Bull</text>
        <text x="${cx - innerW / 4}" y="${PAD + 10}" class="tm-quad-label tm-q-rev"    text-anchor="middle">Reversal Up?</text>
        <text x="${cx - innerW / 4}" y="${H - PAD + 10}" class="tm-quad-label tm-q-bear" text-anchor="middle">Strong Bear</text>
        <text x="${cx + innerW / 4}" y="${H - PAD + 10}" class="tm-quad-label tm-q-pull" text-anchor="middle">Pullback in Bull</text>`;

      // axes
      const axes = `
        <line x1="${PAD}" y1="${cy}" x2="${W - PAD}" y2="${cy}" class="tm-axis"/>
        <line x1="${cx}" y1="${PAD}" x2="${cx}" y2="${H - PAD}" class="tm-axis"/>
        <text x="${W - PAD}" y="${cy - 4}" class="tm-axis-label" text-anchor="end">200 SMA →</text>
        <text x="${cx + 4}" y="${PAD + 8}" class="tm-axis-label">↑ 50 SMA</text>`;

      // dots + labels (per pair)
      const dots = rows.map(r => {
        const c50 = r.cells['50 SMA'];
        const c200 = r.cells['200 SMA'];
        if (!c50 || !c200) return '';
        const x = x2px(c200.dist_pct);
        const y = y2px(c50.dist_pct);
        const sym = r.symbol;
        const tooltip = `${sym}\n50 SMA: ${c50.dist_pct >= 0 ? '+' : ''}${fmt(c50.dist_pct, 2)}%\n200 SMA: ${c200.dist_pct >= 0 ? '+' : ''}${fmt(c200.dist_pct, 2)}%\nlast=${c50.last}  sma50=${c50.sma}  sma200=${c200.sma}`;
        return `<g class="tm-dot-group">
          <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="18" class="tm-dot-halo"/>
          <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4" class="tm-dot"/>
          <text x="${x.toFixed(1)}" y="${(y - 8).toFixed(1)}" class="tm-dot-label" text-anchor="middle">${sym}</text>
          <title>${tooltip}</title>
        </g>`;
      }).join('');

      // legend (compact) — top 3 most bullish
      const ranking = rows.slice(0, 3).map(r => `${r.symbol}(${fmt(r.avg_rank, 1)})`).join(' · ');

      const updated = new Date(data.computed_at).toLocaleTimeString();
      body.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" class="tm-svg" preserveAspectRatio="xMidYMid meet">
          ${quadBg}
          ${quadLabels}
          ${axes}
          ${dots}
        </svg>
        <div class="text-fg-subtle text-[10px] mt-1">
          Top: ${ranking} · ${data.method || 'SMA dist'} · TF ${data.tf || 'H1'} · ${updated}
        </div>`;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  // Pair Cluster (K-means)
  async function loadPairCluster(body, cfg) {
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';
    try {
      const tf = cfg.tf || 'H1';
      const k = cfg.k || 3;
      const data = await apiGet(`/api/widgets/pair-cluster?tf=${tf}&k=${k}`);
      if (data.error) { renderError(body, data.error); return; }

      const clusters = data.clusters || [];
      const palette = ['#10B981', '#F9A826', '#F87171', '#6F8FD8', '#E879F9'];

      const clusterCards = clusters.map((c, idx) => {
        const color = palette[idx % palette.length];
        const chips = c.members.map(s => `<span class="pc-chip" style="border-color:${color};color:${color}">${s}</span>`).join('');
        const arrow = c.cum_return_pct > 0 ? '▲' : c.cum_return_pct < 0 ? '▼' : '·';
        const dirClass = c.cum_return_pct > 0 ? 'pos' : c.cum_return_pct < 0 ? 'neg' : '';
        return `<div class="pc-cluster">
          <div class="pc-cluster-head">
            <span class="pc-cluster-dot" style="background:${color}"></span>
            <span class="pc-cluster-label">Cluster ${c.id} · ${c.size} pairs</span>
            <span class="pc-cluster-meta ${dirClass}">${arrow} ${c.cum_return_pct >= 0 ? '+' : ''}${fmt(c.cum_return_pct, 3)}%</span>
          </div>
          <div class="pc-stats">
            <span>vol ${fmt(c.vol_pct, 3)}%</span>
            <span>sharpe ${fmt(c.sharpe_approx, 2)}</span>
          </div>
          <div class="pc-chips">${chips}</div>
        </div>`;
      }).join('');

      const missing = (data.missing_symbols || []).length
        ? `<div class="text-warning text-[10px] mb-1">⚠ ขาดข้อมูล: ${data.missing_symbols.join(', ')}</div>`
        : '';

      const summary = `<div class="text-fg-subtle text-[10px] mb-2">
        K-means · k=${data.k} · bars=${data.bars_used} · TF=${data.tf} · ${data.total_pairs || 0} pairs
      </div>`;

      body.innerHTML = summary + missing + clusterCards;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  // News Calendar
  async function loadNewsCalendar(body, cfg) {
    body.innerHTML = '<div class="skeleton" style="height: 60%; margin: 8px;"></div>';
    try {
      const data = await apiGet(`/api/widgets/news-calendar?impact=${cfg.impact}&hours=${cfg.hours || 24}`);
      if (!data.events.length) {
        body.innerHTML = '<div class="text-fg-subtle text-xs p-3">ไม่มี event ในช่วง 24h ข้างหน้า</div>';
        return;
      }
      const rows = data.events.slice(0, 30).map(e => {
        const t = new Date(e.time_utc);
        const timeStr = t.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        return `<div class="news-row">
          <span class="news-time">${timeStr}</span>
          <span class="news-ccy">${e.currency}</span>
          <span class="news-title">${escapeHtml(e.title)}</span>
          <span class="impact-badge impact-${e.impact}">${e.impact}</span>
        </div>`;
      }).join('');
      const warn = data.warning ? `<div class="text-warning text-[10px] mb-2">⚠ ${data.warning}</div>` : '';
      body.innerHTML = warn + rows;
    } catch (e) {
      renderError(body, `โหลดล้มเหลว: ${e.message}`);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }

  // ─── "..." menu ────────────────────────────────────────────────────
  function bindMenu(card) {
    const btn = card.querySelector('.widget-menu');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      closeAllMenus();
      const pop = document.createElement('div');
      pop.className = 'widget-menu-pop';
      pop.innerHTML = `
        <button data-act="refresh">↻ Refresh</button>
        <button data-act="remove" class="danger">✕ Remove</button>`;
      document.body.appendChild(pop);
      const r = btn.getBoundingClientRect();
      pop.style.top = (r.bottom + 4) + 'px';
      pop.style.left = (r.right - 140) + 'px';
      pop.addEventListener('click', (ev) => {
        const act = ev.target.dataset.act;
        if (!act) return;
        if (act === 'remove') {
          const wrap = card.closest('.grid-stack-item');
          grid.removeWidget(wrap);
        } else if (act === 'refresh') {
          const type = card.dataset.widgetType;
          const cfg = JSON.parse(card.dataset.widgetConfig);
          const body = card.querySelector('.widget-body');
          if (type === 'currency-strength') loadCurrencyStrength(body, cfg);
          if (type === 'market-snapshot')   loadMarketSnapshot(body, cfg);
          if (type === 'news-calendar')     loadNewsCalendar(body, cfg);
          if (type === 'pair-cluster')      loadPairCluster(body, cfg);
          if (type === 'trend-matrix')      loadTrendMatrix(body);
          if (type === 'set100-template')   loadSet100Template(body);
        }
        closeAllMenus();
      });
    });
  }
  function closeAllMenus() {
    document.querySelectorAll('.widget-menu-pop').forEach(el => el.remove());
  }
  document.addEventListener('click', closeAllMenus);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeAllMenus();
      document.getElementById('modal-add-widget').classList.add('hidden');
    }
  });

  // ─── Modal ─────────────────────────────────────────────────────────
  const modal = document.getElementById('modal-add-widget');

  function refreshPickerState() {
    document.querySelectorAll('.widget-picker').forEach(b => {
      const exists = widgetTypeExists(b.dataset.type);
      b.disabled = exists;
      b.classList.toggle('opacity-40', exists);
      b.classList.toggle('cursor-not-allowed', exists);
      b.classList.toggle('hover:border-warning', !exists);
      // เพิ่ม/ลบ badge "เพิ่มแล้ว"
      let badge = b.querySelector('.picker-added-badge');
      if (exists && !badge) {
        badge = document.createElement('div');
        badge.className = 'picker-added-badge text-[10px] text-fg-subtle mt-2';
        badge.textContent = '✓ เพิ่มแล้ว';
        b.appendChild(badge);
      } else if (!exists && badge) {
        badge.remove();
      }
    });
  }

  const openModal = () => { refreshPickerState(); modal.classList.remove('hidden'); };
  const closeModal = () => modal.classList.add('hidden');

  document.getElementById('btn-add-widget').addEventListener('click', openModal);
  document.querySelectorAll('.add-widget-trigger').forEach(b => b.addEventListener('click', openModal));
  document.querySelectorAll('.modal-close').forEach(b => b.addEventListener('click', closeModal));
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

  document.querySelectorAll('.widget-picker').forEach(b => {
    b.addEventListener('click', () => {
      if (b.disabled) return;
      addWidget(b.dataset.type);
      closeModal();
    });
  });

  // ─── Reset ─────────────────────────────────────────────────────────
  document.getElementById('btn-reset').addEventListener('click', async () => {
    if (!confirm('ลบ widgets ทั้งหมด?')) return;
    grid.removeAll();
    localStorage.removeItem(LAYOUT_KEY);
    refreshEmptyState();
    // sync empty layout to server
    if (!IS_MOCK) {
      try {
        await fetch(`/api/dashboards/${DASHBOARD_KEY}`, {
          method: 'PUT',
          headers: { ...AUTH_HEADER, 'Content-Type': 'application/json' },
          body: JSON.stringify({ layout: [] }),
        });
      } catch (e) {
        console.warn('dashboard reset save failed:', e.message);
      }
    }
  });

  // ─── Boot ─────────────────────────────────────────────────────────
  loadLayout();
})();
