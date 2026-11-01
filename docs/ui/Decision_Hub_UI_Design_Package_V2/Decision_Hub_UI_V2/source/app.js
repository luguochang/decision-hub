/* Decision Hub V2 — offline, editable interaction prototype. No network or backend writes. */
'use strict';
const F = window.DH_FIXTURES, PAGES = window.DH_PAGES, I = window.DH_ICONS;
const VERSION = 'dh-ui-v2.1', DEFAULT_RUN = 'demo-r01';
const esc = (s = '') => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
function seed() { return { runs: clone(F.runs), draft: '', documents: { 'demo-doc': { id: 'demo-doc', title: '研究说明_演示', filename: '研究说明_演示.txt', market: 'BTC / 宏观', symbol: 'BTC', privacy: 'private', status: 'review', version: 1, decisions: {}, selected: 0, linked: null } }, positions: {}, idempotency: {}, seq: 0, graph: {}, scope: 'all' }; }
let S;
try {
    S = JSON.parse(sessionStorage.getItem(VERSION)) || seed();
}
catch {
    S = seed();
}
function save() { try {
    sessionStorage.setItem(VERSION, JSON.stringify(S));
}
catch { } }
let scene = 'home', params = new URLSearchParams(), lastHash = '', overlayKind = '', overlayBack = '', lastFocus = null, rendering = false, uploadFile = null, transitionTimers = [];
const routes = new Set(PAGES.map(p => p[0]));
function parseHash(hash = location.hash) { const raw = hash.replace(/^#\/?/, ''); const [p, q = ''] = raw.split('?'); return { scene: routes.has(p) ? p : 'home', params: new URLSearchParams(q) }; }
function safeBack(v, fallback = '#/home') { try {
    if (typeof v !== 'string' || !v.startsWith('#/'))
        return fallback;
    const name = v.slice(2).split('?')[0];
    return routes.has(name) ? v : fallback;
}
catch {
    return fallback;
} }
function currentRun() { return S.runs.find(r => r.id === (params.get('run') || DEFAULT_RUN)) || null; }
function currentDoc() { const id = params.get('doc') || 'demo-doc'; return S.documents[id] || null; }
function href(dest, extra = {}, remember = true) { const p = new URLSearchParams(); const run = extra.run ?? params.get('run') ?? DEFAULT_RUN; p.set('run', run); if (params.get('doc') && !Object.hasOwn(extra, 'doc'))
    p.set('doc', params.get('doc')); if (remember)
    p.set('back', location.hash || '#/home'); for (const [k, v] of Object.entries(extra)) {
    if (v !== null && v !== undefined && v !== '')
        p.set(k, String(v));
    else
        p.delete(k);
} return '#/' + dest + (p.size ? '?' + p.toString() : ''); }
function fallback() { const r = params.get('run') || DEFAULT_RUN; if (scene.startsWith('graph'))
    return '#/report?run=' + encodeURIComponent(r); if (scene.startsWith('dsh'))
    return '#/technical?run=' + encodeURIComponent(r); if (['technical', 'evidence'].includes(scene))
    return '#/report?run=' + encodeURIComponent(r); if (['report', 'report-incomplete', 'queued', 'researching', 'failed', 'submitting'].includes(scene))
    return '#/runs?run=' + encodeURIComponent(r); if (scene === 'event')
    return '#/calendar-all'; if (scene.startsWith('document'))
    return '#/library'; return '#/home'; }
function returnHref() { return safeBack(params.get('back'), fallback()); }
function rememberPosition() { if (!lastHash)
    return; S.positions[lastHash] = { y: window.scrollY, focus: document.activeElement?.id || null }; save(); }
function go(dest, extra = {}, remember = true) { rememberPosition(); location.hash = href(dest, extra, remember); }
function follow(hash) { rememberPosition(); location.hash = safeBack(hash); }
function updateQuery(k, v) {
    // Keep drill-down return URLs in sync even when filters update without rerendering.
    const previousHash = location.hash || '#/home';
    if (v === null || v === '')
        params.delete(k);
    else
        params.set(k, String(v));
    const h = '#/' + scene + (params.size ? '?' + params.toString() : '');
    history.replaceState(null, '', h);
    lastHash = h;
    document.querySelectorAll('a[href^="#/"]').forEach(link => {
        const target = parseHash(link.getAttribute('href'));
        if (target.params.get('back') === previousHash) {
            target.params.set('back', h);
            link.setAttribute('href', '#/' + target.scene + '?' + target.params.toString());
        }
    });
}
function formatDate(v, mode = 'date') { if (!v)
    return '—'; try {
    const opts = mode === 'time' ? { hour: '2-digit', minute: '2-digit', hour12: false } : mode === 'short' ? { month: '2-digit', day: '2-digit' } : mode === 'full' ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false } : { year: 'numeric', month: '2-digit', day: '2-digit' };
    return new Intl.DateTimeFormat('zh-CN', { ...opts, timeZone: 'Asia/Shanghai' }).format(new Date(v));
}
catch {
    return '—';
} }
const ic = (name, size = 18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name] || I.grid}</svg>`;
const tile = (name, c = '', size = '') => `<span class="tile ${c} ${size}">${ic(name, size === 'large' ? 29 : 22)}</span>`;
const badge = (text, c = '', icon = '') => `<span class="badge ${c}">${icon ? ic(icon, 12) : '<span class="dot"></span>'}${esc(text)}</span>`;
const btn = (text, action, c = '', icon = '', attr = '') => `<button type="button" class="btn ${c}" data-action="${action}" ${attr} ${!text && !attr.includes('aria-label') ? `aria-label="${action}"` : ''}>${icon ? ic(icon, 16) : ''}${text ? `<span>${esc(text)}</span>` : ''}</button>`;
const a = (text, dest, c = '', icon = '', extra = {}, remember = true, attr = '') => `<a class="btn ${c}" href="${esc(href(dest, extra, remember))}" ${attr}>${icon ? ic(icon, 16) : ''}<span>${esc(text)}</span></a>`;
const plainLink = (text, hash, c = '', icon = '', attr = '') => `<a class="${c}" href="${esc(hash)}" ${attr}>${icon ? ic(icon, 16) : ''}${esc(text)}</a>`;
const note = (title, text, c = '', icon = 'info') => `<div class="callout ${c}">${ic(icon, 18)}<div>${title ? `<strong>${esc(title)}</strong>` : ''}${text}</div></div>`;
const stateBadge = s => badge(({ completed: '已完成', researching: '研究中', queued: '已排队', submitting: '提交中', failed: '失败' }[s] || s), ({ completed: 'green', researching: 'blue', queued: 'purple', submitting: 'blue', failed: 'red' }[s] || ''));
function backline(label = '返回上一级') { return `<div class="backline">${plainLink(label, returnHref(), 'btn ghost sm', 'back', `id="page-back"`)}</div>`; }
function title(t, sub = '', actions = '') { return `<div class="page-heading"><div><h1>${t}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${actions ? `<div class="row wrap">${actions}</div>` : ''}</div>`; }
function hero(t, sub, eyebrow = 'Research Workspace') { return `<section class="hero"><div class="eyebrow"><span class="dot"></span>${eyebrow}</div><h1>${t}</h1><p>${sub}</p></section>`; }
function ring(p = 75, size = 58) { return `<div class="ring" style="width:${size}px;height:${size}px" role="img" aria-label="覆盖率 ${p}%"><svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="27" stroke="#e1edff" stroke-width="6" fill="none"/><circle cx="32" cy="32" r="27" stroke="#3289f8" stroke-width="6" stroke-linecap="round" fill="none" stroke-dasharray="${(p / 100 * 169.65).toFixed(2)} 169.65"/></svg><b>${p}%</b></div>`; }
function progress(p, c = '') { return `<div class="progress ${c}" style="--p:${p}%" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"><i></i></div>`; }
function bars(n = 5) { return `<span class="coverage-bars" aria-hidden="true">${Array.from({ length: 5 }, (_, i) => `<i style="height:${4 + i * 2}px;opacity:${i < n ? 1 : .22}"></i>`).join('')}</span>`; }
function health() { const unknown = scene === 'home-unknown' || scene === 'home-loading' || (scene === 'sources' && params.get('health') === 'unknown'); const degraded = scene === 'home-degraded'; return `<div class="service-strip"><span class="review-tag">离线评审 · 演示数据</span><span class="hide-mobile">Worker</span><a class="btn ghost sm" id="service-status" href="${esc(href('sources', { health: unknown ? 'unknown' : degraded ? 'degraded' : 'online' }))}">${badge(unknown ? '未确认在线' : '在线', unknown ? 'amber' : 'green')}</a><span class="hide-compact hide-mobile">${badge('research', 'blue')}</span><span class="hide-tablet hide-mobile">${badge(unknown ? '心跳未确认' : '心跳 13:42:10', unknown ? 'amber' : 'green', 'heart')}</span>${btn('', 'catalog', 'ghost icon', 'grid', 'aria-label="打开页面目录" title="页面目录"')}<span class="avatar">DH</span></div>`; }
function navGroup() { if (scene.startsWith('calendar') || scene === 'event')
    return 'calendar-all'; if (scene === 'runs')
    return 'runs'; if (scene === 'library' || scene === 'upload' || scene.startsWith('document'))
    return 'library'; return 'home'; }
function logo() { return `<span class="brand-logo"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><circle cx="16" cy="16" r="10" stroke="white" stroke-width="2.3"/><circle cx="16" cy="16" r="3.5" fill="white"/></svg></span>`; }
function shell(content) { const g = navGroup(), pageName = PAGES.find(p => p[0] === scene)?.[1] || 'Research'; return `<aside class="sidebar" aria-label="主导航"><a class="brand" href="#/home">${logo()}<div><strong>Decision Hub</strong><small>AI FOR BETTER DECISIONS</small></div></a><div class="nav-caption">研究空间</div><nav>${[['home', 'search', 'Research'], ['calendar-all', 'calendar', '宏观日历'], ['runs', 'layers', 'Research Runs'], ['library', 'file', '资料研究']].map(([r, i, t]) => `<a class="nav-item ${g === r ? 'active' : ''}" href="${esc(href(r, {}, false))}" ${g === r ? 'aria-current="page"' : ''}><span class="nav-ico">${ic(i, 20)}</span><span>${t}</span></a>`).join('')}</nav><section class="scope-card"><div class="row gap6">${ic('target', 16)}<b>BTC / 宏观研究</b></div><p>方向结论、证据质量<br>与执行权限分别呈现</p></section><div class="nav-bottom">${btn('页面目录', 'catalog', 'ghost w100', 'grid')}${a('组件与状态', 'states', 'ghost w100', 'layers', {}, false)}${btn('交付说明', 'about', 'ghost w100', 'book')}<div class="profile row"><span class="avatar">DH</span><div class="grow"><b style="font-size:12px">设计评审工作区</b><p class="small" style="font-size:10px">本地演示 · v2.1</p></div></div></div></aside><div class="app-body"><header class="topbar">${btn('', 'mobile-menu', 'ghost icon mobile-menu', 'menu', 'aria-label="打开导航"')}<nav class="breadcrumb" aria-label="面包屑"><a href="#/home" class="hide-mobile">${ic('grid', 14)} Decision Desk</a><span class="hide-mobile">${ic('chevron', 11)}</span><a href="#/home">Research</a>${scene === 'home' ? '' : `${ic('chevron', 11)}<b>${esc(pageName)}</b>`}</nav>${health()}</header><main class="main" id="main" tabindex="-1">${content}<footer class="bottom-note"><span>设计交互原型 · 所有动态计数、日程、进度均为演示，不是实时数据</span><span>Decision Hub / v2.1</span></footer></main></div>`; }
function empty(t, p, action = '', icon = 'folder', small = false) { return `<div class="empty ${small ? 'resource-empty' : ''}">${tile(icon, '', 'large')}<h2>${esc(t)}</h2><p>${p}</p>${action}</div>`; }
function ruleChart() { return `<svg class="rule-chart" viewBox="0 0 480 154" role="img" aria-label="规则变化的交互示意折线，不是价格行情"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#50a0ff" stop-opacity=".2"/><stop offset="1" stop-color="#50a0ff" stop-opacity="0"/></linearGradient></defs>${[25, 55, 85, 115].map(y => `<path d="M28 ${y}H468" stroke="#e9eff9"/><text x="0" y="${y + 4}" fill="#7385a3" font-size="9">${y === 25 ? '+1' : y === 85 ? '0' : y === 115 ? '−1' : ''}</text>`).join('')}<path d="M28 99L51 100L74 86L97 88L120 67L143 74L166 58L189 61L212 84L235 78L258 69L281 72L304 56L327 64L350 44L373 52L396 41L419 35L442 41L468 29V127H28Z" fill="url(#area)"/><path d="M28 99L51 100L74 86L97 88L120 67L143 74L166 58L189 61L212 84L235 78L258 69L281 72L304 56L327 64L350 44L373 52L396 41L419 35L442 41L468 29" fill="none" stroke="#2c77f2" stroke-width="2"/><path d="M28 78L97 79L166 80L235 86L304 81L373 85L468 88" fill="none" stroke="#9871d5" stroke-width="1.5" stroke-dasharray="4 4"/>${['T−6', 'T−5', 'T−4', 'T−3', 'T−2', 'T−1', 'T'].map((t, i) => `<text x="${27 + i * 72}" y="149" fill="#7385a3" font-size="10">${t}</text>`).join('')}</svg>`; }
function eventMini(e) { const d = new Date(e.date); return `<a class="event-mini" href="${esc(href('event', { event: e.id }))}"><div class="row"><div class="date-tile"><small>9月</small><b>${new Intl.DateTimeFormat('en-US', { day: '2-digit', timeZone: 'Asia/Shanghai' }).format(d)}</b></div><div class="grow"><div class="row between gap6"><span class="event-title">${esc(e.name)}</span>${badge(e.impact, 'amber')}</div><div class="small" style="font-size:10px;margin-top:3px">${formatDate(e.date, 'time')} · 示例时刻</div></div></div><div class="mini-values">${['前值', '预期', '实际', '意外'].map(t => `<span>${t}<b>—</b></span>`).join('')}</div></a>`; }
function runTarget(r) { if (r.status === 'completed')
    return r.kind === 'synthesis_incomplete' ? 'report-incomplete' : 'report'; return r.status === 'submitting' ? 'submitting' : r.status; }
function runMini(r) { return `<a class="run-mini ${r.id === (params.get('run') || DEFAULT_RUN) ? 'active' : ''}" href="${esc(href('home', { run: r.id }, false))}"><div class="row"><span class="dot" style="color:${r.status === 'failed' ? '#d44b64' : r.status === 'completed' ? '#15b88a' : '#6392ed'}"></span><span class="title grow">${esc(r.title)}</span>${ic('chevron', 13)}</div><div class="row wrap gap6">${badge(r.trigger, r.trigger === '自动触发' ? 'green' : '')}${stateBadge(r.status)}</div><div class="meta"><span>${formatDate(r.at, 'full')}</span><span>${bars(r.evidence / 2)} ${r.evidence}/${r.required}</span></div>${r.parent ? `<div class="small" style="font-size:9px;margin-top:5px">${ic('branch', 11)} 父任务 ${esc(r.parent)}</div>` : ''}</a>`; }
function home(mode = '') {
    const r = currentRun() || S.runs[0];
    const no = mode === 'empty';
    let top = hero('Research Command Center', '从研究问题到证据与结论，在一个工作台里完成。', 'Durable Agentic Research');
    if (mode === 'unknown')
        top += note('尚未确认 Research worker 在线', '正在等待可用心跳，不能据此判断服务健康。已有报告仍可阅读。', 'amber', 'clock');
    if (mode === 'degraded')
        top += note('1 个来源降级 · 不影响已保存报告', '交接稿记录过 BLS 来源 403。本页只演示降级状态，不代表当前服务检测结果。' + a('查看影响范围', 'sources', 'text', 'arrow', { health: 'degraded' }), 'amber', 'warning');
    top += `<form class="command-box mt12" id="home-form"><div class="command-entry">${ic('spark', 24)}<input id="home-focus" name="focus" aria-label="研究关注点（可留空）" autocomplete="off" value="${esc(S.draft)}" placeholder="补充研究关注点，或留空使用 BTC / 宏观模板…"/>${a('', 'library', 'ghost icon', 'paperclip', {}, true, 'aria-label="选择研究资料"')}<button type="submit" class="btn primary icon" aria-label="进入研究输入页">${ic('arrow', 21)}</button></div><div class="command-hints"><span>快捷关注点</span><button type="button" data-action="focus-chip" data-value="美元指数与 ETF 资金流">美元指数与 ETF 资金流</button><button type="button" data-action="focus-chip" data-value="关键证据缺口与反方限制">证据缺口与反方限制</button></div></form><section class="summary-grid"><a class="panel summary-card action" href="${esc(href('compose'))}" id="primary-research">${tile('bolt', '', 'large')}<div><strong>获取最新操作建议</strong><p>空输入也能开始，模板已就绪</p></div><span class="round-arrow">${ic('arrow', 18)}</span></a><div class="panel summary-card">${tile('bar')}<div><h3>研究范围</h3><strong>BTC / 宏观</strong><p>不展示未经接入的实时行情</p></div></div><a class="panel summary-card" href="${esc(href('technical'))}">${tile('file', 'purple')}<div class="grow"><h3>证据覆盖</h3><strong>${no ? '—' : '9 / 12'} <small>项要求</small></strong><p>${no ? '暂无研究任务' : '覆盖演示 · 3 项缺口'}</p></div>${no ? '' : ring(75)}</a><a class="panel summary-card" href="${esc(href('report'))}">${tile('lock', 'amber')}<div><h3>执行权限</h3><strong style="color:var(--amber)">未授权交易</strong><p>研究方向不等于执行许可</p></div></a></section>`;
    if (mode === 'loading')
        return top + `<section class="three-col">${[['Worker', '正在读取 /v1/health'], ['宏观日历', '正在读取 /v1/macro-events'], ['Research Runs', '正在读取 /v1/research/runs']].map(([t, s]) => `<div class="panel pad"><div class="row"><span class="spinner"></span><h3>${t}</h3></div><p class="small mt12">${s}</p><div class="skeleton"></div><div class="skeleton medium"></div><div class="skeleton block"></div></div>`).join('')}</section>`;
    if (no)
        return top + `<section class="panel">${empty('还没有研究任务', '任务列表为空。开始一次研究后，结论、证据与后续复查会出现在这里。', a('获取最新操作建议', 'compose', 'primary', 'bolt'), 'search')}</section>`;
    return top + `<section class="home-grid"><div class="panel calendar-home"><div class="panel-head"><h3 class="row gap6">${ic('calendar', 18)} 宏观事件日历</h3>${a('全部', 'calendar-all', 'text', 'arrow')}</div><div class="segmented">${['全部', '即将发生', '历史发布'].map((t, i) => `<a class="${!i ? 'active' : ''}" href="${esc(href(['calendar-all', 'calendar-upcoming', 'calendar-history'][i]))}">${t}${i === 2 ? ' (0)' : ''}</a>`).join('')}</div>${F.events.map(eventMini).join('')}<div class="panel-bottom">${a('查看完整宏观日历', 'calendar-all', '', 'arrow')}</div></div><div class="panel runs-home"><div class="panel-head"><h3 class="row gap6">${ic('layers', 18)} Research Runs</h3>${a('查看全部', 'runs', 'text')}</div><div class="segmented"><a class="active" href="${esc(href('runs'))}">全部 ${S.runs.length}</a><a href="${esc(href('runs', { trigger: '人工提交' }))}">人工提交</a></div>${S.runs.slice(0, 5).map(runMini).join('')}<div class="panel-bottom">${a('管理研究任务', 'runs', '', 'arrow')}</div></div><div class="panel report-preview"><div class="panel-head"><h3 class="row gap6">${esc(r.title)}</h3>${stateBadge(r.status)}</div><div class="tab-row"><a class="active" href="${esc(href(runTarget(r), { run: r.id }))}">用户结果</a><a href="${esc(href('technical', { run: r.id }))}">技术详情</a><a style="margin-left:auto" href="${esc(href('graph', { run: r.id }))}">${ic('expand', 13)} 分析链路</a></div><div class="preview-body">${r.status === 'completed' ? `<div class="verdict-card"><div class="row between"><span class="row gap6" style="font-size:12px;color:var(--amber)">${ic('chart', 17)} 最新研究结论</span>${badge(r.kind === 'synthesis_incomplete' ? '结论不完整' : '仅供研究', 'amber')}</div><h2>${r.kind === 'synthesis_incomplete' ? '研究完成，结论不完整' : '7 日价格规则基线：偏多'}</h2><p>30m、24h 暂不交易；72h 为保守 long 候选。<br>概率未校准，当前未授权交易。</p>${a('打开完整研究报告', runTarget(r), 'text', 'arrow', { run: r.id })}</div><div><div class="row gap6 mb12"><span style="color:var(--brand)">${ic('review', 16)}</span><h4 style="font-size:12px">证据与执行边界</h4></div><div class="stats-mini"><div class="stat-chip"><span class="row gap6">${tile('file', '', 'sm')}<strong>6</strong></span><small>可查看证据条目</small></div><div class="stat-chip"><span class="row gap6">${tile('target', 'purple', 'sm')}<strong>3</strong></span><small>覆盖缺口（演示）</small></div><div class="stat-chip"><span class="row gap6">${tile('lock', 'amber', 'sm')}<strong>未授权</strong></span><small>没有交易执行入口</small></div></div></div><div class="chart-card"><div class="row between"><h4>规则变化可视化</h4>${badge('交互示意', 'blue')}</div>${ruleChart()}<div class="chart-legend"><span><i></i>方向基线</span><span><i class="counter"></i>反方限制</span><span>不是历史价格</span></div></div>` : runStatusPanel(r, r.status, true)}${note('保持任务上下文', `当前 ${esc(r.id)}。查看证据、链路和返回不会创建新任务。`, 'neutral', 'link')}</div></div></section>`;
}
function runStatusPanel(r, status, compact = false) { const active = { submitting: 0, queued: 1, researching: 2, completed: 3, failed: 2 }[status] ?? 0; return `<div class="${compact ? '' : 'panel pad'}"><div class="row between mb16"><h3>研究任务状态</h3>${stateBadge(status)}</div><div class="timeline">${[['提交请求', '为同一研究意图使用幂等键'], ['已进入队列', 'Run 已登记，可以安全离开'], ['正在研究', '收集证据、覆盖检查与独立复核'], ['生成研究报告', '完成后打开同一 Run 的报告']].map(([t, d], i) => `<div class="timeline-row"><span class="step-dot ${i < active ? 'done' : i === active ? 'current' : ''}">${i < active ? ic('check', 15) : i + 1}</span><div class="step-copy"><b>${t}</b><p>${d}</p>${i === active && status === 'researching' ? `<div class="live-block"><div class="three-col"><span><small>当前轮次</small><strong>2</strong></span><span><small>工具调用</small><strong>7</strong></span><span><small>最近活动</small><strong style="font-size:13px">刚刚</strong></span></div><p class="mt8" style="color:var(--brand)">正在核对来源与证据的关联关系…</p></div>` : ''}</div></div>`).join('')}</div>${status === 'failed' ? note('研究任务未完成', '错误码 SOURCE_TIMEOUT；来源 source_reader；可以重试，原输入已保留。', 'red', 'warning') : ''}<div class="panel pad mt12" style="box-shadow:none"><div class="row">${tile('file', 'purple')}<div class="grow"><b style="font-size:12px">${esc(r.title)}</b><div class="mono small">${esc(r.id)}</div></div></div>${r.parent ? `<p class="small mt8">${ic('branch', 13)} 父任务 ${plainLink(r.parent, href('report', { run: r.parent }))}</p>` : ''}</div></div>`; }
function composePage(status = 'initial') { const r = currentRun() || S.runs[0], preview = params.has('preview'); const focus = status === 'initial' ? S.draft : (r.focus || S.draft || ''); const doc = params.get('doc') ? currentDoc() : null; const ro = ['submitting', 'queued', 'researching'].includes(status); const parent = params.get('parent') || r.parent; return backline(status === 'initial' ? '返回研究工作台' : '返回来源页面') + hero('获取最新操作建议', '可留空使用 BTC / 宏观研究模板，也可以只补充一个关注点。', 'Research Request') + `<div class="split wide-aside"><section class="stack"><div class="panel pad"><div class="row between mb16"><h3 class="row">${tile('search', '', 'sm')} 输入研究关注点</h3>${badge('选填', 'blue')}</div><form id="research-form"><div class="form-field"><label for="research-focus" class="sr-only">研究关注点（可留空）</label><textarea id="research-focus" name="focus" class="input-large" maxlength="5000" autocomplete="off" placeholder="例如：重点关注美元指数、ETF 资金流和当前证据缺口…" ${ro ? 'readonly' : ''}>${esc(focus)}</textarea><div class="char-count"><span id="char-count">${Array.from(focus).length}</span> / 5000</div></div>${!ro ? `<div class="small mb12">快捷关注点</div><div class="hint-chips">${['美元指数和 ETF 资金流', '关键证据与反方限制', '短周期 no_trade 的原因'].map(t => `<button type="button" data-action="focus-chip" data-value="${t}">${t}</button>`).join('')}</div>` : ''}<div class="form-footer">${a('选择研究资料', 'library', '', 'paperclip', { back: href('compose', { parent: params.get('parent') }, false) })}<span class="small row gap6">${ic('shield-check', 15)} 防重复提交始终开启</span></div>${doc ? note('已关联资料', `${esc(doc.title)} · v${doc.version} · ${doc.status === 'ready' ? '已通过质量门' : '尚未通过质量门，不能提交'}`, 'blue', 'file') : ''}${params.get('parent') ? note('将创建新的子任务', `关联父任务 ${esc(params.get('parent'))}，不覆盖原报告。`, 'neutral', 'branch') : ''}<div class="mt16">${status === 'initial' ? `<button type="submit" id="submit-research" class="btn primary large w100">${ic('play', 18)} 开始分析</button>` : status === 'submitting' ? `<button class="btn primary large w100" disabled><span class="spinner" style="border-top-color:white"></span> 正在提交…</button>` : status === 'failed' ? btn('保留原输入重试', 'retry-research', 'primary large w100', 'refresh') : a('返回 Research Runs', 'runs', 'primary large w100', 'layers')}</div></form></div>${note('提交行为', '空白输入提交“获取最新操作建议”；同一窗口、同一内容仅产生一个任务。不会要求填写内部能力名称。', 'green', 'shield-check')}${status === 'failed' ? note('重试不会覆盖旧记录', '会创建带 parent_run 关系的新 Run。诊断与旧报告仍然保留。', 'amber', 'branch') : note('离开页面不会取消研究', '本原型用本地状态模拟任务推进；真实的任务生命周期由服务端维护。', 'neutral', 'info')}</section><aside class="stack">${status === 'initial' ? `<div class="panel pad"><h3 class="row mb16">${tile('layers', 'purple', 'sm')} 这次研究会包含</h3><div class="timeline">${[['资料与证据', '核对来源、时效和必要证据要求'], ['方向与候选', '7 日基线与 30m / 24h / 72h 候选'], ['限制与复核', '反方论据、概率校准、执行权限']].map(([t, s], i) => `<div class="timeline-row"><span class="step-dot ${i === 0 ? 'current' : ''}">${i + 1}</span><div class="step-copy"><b>${t}</b><p>${s}</p></div></div>`).join('')}</div>${note('没有默认交易授权', '研究结果不会自动触发买入、卖出或权限变更。', 'amber', 'lock')}</div>` : runStatusPanel({ ...r, status }, status)}${status === 'researching' ? a('查看本次技术详情', 'technical', 'soft w100', 'code') : ''}${status === 'queued' || status === 'researching' ? `<p class="small">演示任务约数秒后完成；通过页面目录进入的固定状态不会自动跳转。</p>` : ''}</aside></div>`; }
function runsPage() { const q = params.get('q') || '', status = params.get('status') || 'all', trigger = params.get('trigger') || 'all'; return backline('返回研究工作台') + hero('Research Runs', '每次提交、定时复查与重试都有独立记录，父子关系可追踪。', 'Research Ledger') + `<div class="toolbar"><div class="search-field">${ic('search', 17)}<input id="run-search" name="runSearch" autocomplete="off" aria-label="搜索任务标题或 ID" placeholder="搜索任务标题、Run ID 或关注点…" value="${esc(q)}"/></div><label>任务状态<select id="run-status" name="runStatus">${[['all', '全部状态'], ['completed', '已完成'], ['researching', '研究中'], ['queued', '已排队'], ['failed', '失败']].map(([v, t]) => `<option value="${v}" ${v === status ? 'selected' : ''}>${t}</option>`).join('')}</select></label><label>触发方式<select id="run-trigger" name="runTrigger">${['all', '自动触发', '人工提交', '定时复查', '重试'].map(v => `<option value="${v}" ${v === trigger ? 'selected' : ''}>${v === 'all' ? '全部方式' : v}</option>`).join('')}</select></label>${btn('重置', 'reset-runs', 'ghost', 'refresh')}${a('发起研究', 'compose', 'primary', 'plus')}</div><div class="panel"><div class="table-scroll"><table class="data-table" style="min-width:840px"><thead><tr><th>研究任务</th><th>状态</th><th>触发 / 时间</th><th>证据条目</th><th>父任务</th><th>操作</th></tr></thead><tbody id="run-table">${S.runs.map(r => `<tr data-run="${r.id}" data-status="${r.status}" data-trigger="${r.trigger}" data-search="${esc((r.title + ' ' + r.id + ' ' + r.focus).toLowerCase())}"><td><a class="cell-link" id="open-${r.id}" href="${esc(href(runTarget(r), { run: r.id }))}">${tile('file', r.status === 'failed' ? 'red' : 'purple', 'sm')}<span>${esc(r.title)}<span class="mono small" style="display:block;font-size:10px;font-weight:400">${esc(r.id)}</span></span></a></td><td>${stateBadge(r.status)}${r.kind === 'synthesis_incomplete' ? `<div class="mt8">${badge('结论不完整', 'amber')}</div>` : ''}</td><td>${badge(r.trigger)}<div class="small mt8">${formatDate(r.at, 'full')}</div></td><td><div class="row">${bars(r.evidence / 2)}<span>${r.evidence} 条</span></div><div class="small" style="font-size:10px">要求 ${r.required} 项 · 演示</div></td><td>${r.parent ? plainLink(r.parent, href('report', { run: r.parent }), 'mono', 'branch') : '<span class="small">根任务</span>'}</td><td>${a('查看', runTarget(r), 'text', 'chevron', { run: r.id })}</td></tr>`).join('')}</tbody></table></div><div id="run-empty" hidden>${empty('没有匹配的任务', '调整关键词或清除筛选，不会删除任何任务。', btn('清除筛选', 'reset-runs', 'soft', 'refresh'), 'search', true)}</div><div class="table-footer"><span>显示 <b id="run-count">${S.runs.length}</b> 条 / 共 ${S.runs.length} 条</span><span>点击父任务可返回原报告</span></div></div>`; }
function calendarPage(scope = 'all') { const q = params.get('q') || '', market = params.get('market') || 'all', impact = params.get('impact') || 'all'; const ranges = [['all', '全部', 4], ['upcoming', '即将发生', 4], ['history', '历史发布', 0]]; return backline('返回研究工作台') + hero('宏观事件日历', '事件值、采集状态与历史修订分别记录；缺失的数据保持为空。', 'Macro Context') + `<div class="panel mb16"><div class="panel-head"><div class="segmented" style="margin:0">${ranges.map(([v, t, n]) => `<a href="${esc(href('calendar-' + v, { q, market, impact }, false))}" class="${scope === v ? 'active' : ''}" style="font-size:13px;padding:8px 18px">${t} (${n})</a>`).join('')}</div>${a('来源状态', 'sources', 'soft', 'activity', { health: 'degraded' })}</div></div><div class="toolbar"><label>市场<select id="event-market" name="market">${[['all', '全部市场'], ['美国', '美国'], ['欧元区', '欧元区'], ['中国', '中国']].map(([v, t]) => `<option value="${v}" ${market === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label><label>重要性<select id="event-impact" name="impact">${[['all', '全部'], ['高', '高'], ['中', '中']].map(([v, t]) => `<option value="${v}" ${impact === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label><div class="search-field">${ic('search', 17)}<input id="event-search" name="eventSearch" aria-label="搜索事件名称或来源" placeholder="搜索事件名称或来源…" autocomplete="off" value="${esc(q)}"/></div>${btn('重置', 'reset-events', 'ghost', 'refresh')}<span class="small">时区：Asia/Shanghai</span></div><div class="split"><section class="panel">${scope === 'history' ? empty('尚未采集历史发布', '当前历史账本为 0 条。不会把未来事件复制为历史，也不会将缺失的实际值显示为 0。', a('查看即将发生的事件', 'calendar-upcoming', 'primary', 'calendar'), 'file-clock') : `<div class="table-scroll"><table class="data-table calendar-table"><thead><tr><th>示例时间</th><th>事件名称</th><th>影响</th><th>前值</th><th>预期</th><th>实际</th><th>意外</th><th>采集状态</th></tr></thead><tbody id="event-table">${F.events.map(e => `<tr data-event="${e.id}" data-market="${e.region}" data-impact="${e.impact}" data-search="${esc((e.name + ' ' + e.source).toLowerCase())}"><td><strong>${formatDate(e.date, 'short')}</strong><div class="small">${formatDate(e.date, 'time')}</div></td><td><a class="cell-link" id="open-${e.id}" href="${esc(href('event', { event: e.id }))}"><span class="region-code">${e.code}</span><span>${esc(e.name)}<small style="display:block;font-weight:400;font-size:9px">${esc(e.source)}</small></span></a></td><td>${badge(e.impact, e.impact === '高' ? 'red' : 'amber', 'bar')}</td><td>—</td><td>—</td><td>—</td><td>—</td><td>${badge(e.state, e.state === '来源降级' ? 'amber' : '')}${a('详情', 'event', 'text', 'chevron', { event: e.id })}</td></tr>`).join('')}</tbody></table></div><div id="event-empty" hidden>${empty('没有匹配的事件', '清除筛选查看当前示例账本。', btn('清除筛选', 'reset-events', 'soft', 'refresh'), 'search', true)}</div><div class="table-footer"><span>显示 <b id="event-count">4</b> 条</span><span>日程为设计样例，非官方发布时间</span></div>`}${scope === 'history' ? `<div class="panel-body">${note('采集范围与时间', '历史发布：未采集；最后成功采集时间：未提供；暂无修订版本。', 'neutral', 'clock')}</div>` : ''}</section><aside class="stack"><div class="panel pad"><div class="row between mb16"><h3 class="row gap6">${ic('flag', 17)} 重点事件</h3>${badge('示例', 'blue')}</div>${F.events.slice(0, 2).map((e, i) => `<a class="focus-event ${i ? 'blue' : ''}" href="${esc(href('event', { event: e.id }))}"><div class="row">${tile(i ? 'file' : 'activity', i ? '' : 'red')}<div class="grow"><h4>${esc(e.name)}</h4><span class="small">${formatDate(e.date, 'full')}</span></div>${ic('chevron', 14)}</div><p class="small mt8">${i ? '关注采集时间与来源完整性' : '实际值未提供，不计算意外值'}</p></a>`).join('')}</div><div class="panel pad"><h3 class="row gap6 mb12">${ic('clock', 17)} 未来风险窗口</h3>${F.events.map(e => `<a class="risk-item" href="${esc(href('event', { event: e.id }))}"><span>${formatDate(e.date, 'short')} ${formatDate(e.date, 'time')}</span><b style="font-size:11px">${esc(e.name)}</b></a>`).join('')}</div><div class="panel pad"><h3 class="mb16">历史数据覆盖</h3><div class="row">${ring(0, 72)}<div><b>尚无已发布实际值</b><p class="small mt8">0 条历史发布 / 0 个修订版本</p></div></div><div class="mt16">${note('不伪造补齐', '来源降级与历史空态同时可见。', 'amber', 'info')}</div></div></aside></div>`; }
function eventBody() { const e = F.events.find(x => x.id === params.get('event')); if (!e)
    return empty('事件不存在', '这个事件 ID 没有对应的数据。请返回日历重新选择。', plainLink('返回日历', returnHref(), 'btn primary'), 'warning', true); return `<div class="row mb16">${tile('calendar')}<div><h2 style="font-size:19px">${esc(e.name)}</h2><p class="small">${esc(e.id)} · ${esc(e.region)}</p></div></div><div class="row wrap mb16">${badge('未发布')}${badge(e.state, e.state === '来源降级' ? 'amber' : '')}${badge('示例日程', 'blue')}</div><dl class="kv"><dt>计划发布时间</dt><dd>${formatDate(e.date)} ${formatDate(e.date, 'time')}（演示）</dd><dt>时区</dt><dd>Asia/Shanghai</dd><dt>前值</dt><dd>—</dd><dt>预期</dt><dd>—</dd><dt>实际</dt><dd>—</dd><dt>意外值</dt><dd>—（实际值与预期均未提供）</dd><dt>来源</dt><dd>${esc(e.source)}</dd><dt>来源链接</dt><dd>未提供可核验的单条事件 URL</dd><dt>观察时间</dt><dd>未采集</dd></dl><div class="section-heading"><h2>历史修订版本</h2>${badge('0 个版本')}</div>${note('尚无已发布版本', '后续的实际值与修订作为新版本入账，不覆盖旧值。', 'neutral', 'file-clock')}<div class="mt16">${note('设计样例，不是市场日程', '本页不提供实时经济数据。发布时刻和来源状态仅用于展示交互。', 'amber', 'info')}</div>`; }
function reportPage(incomplete = false) { const r = currentRun(); if (!r)
    return backline('返回研究任务') + empty('研究任务不存在', '该 Run ID 未找到。原型没有自动替换成另一份报告。', a('返回任务列表', 'runs', 'primary', 'layers', {}, false), 'warning'); if (r.status !== 'completed' && !params.has('preview'))
    return backline('返回研究任务') + title(esc(r.title), '任务尚未完成，不显示另一份 Run 的报告。') + runStatusPanel(r, r.status) + a('查看当前进度', runTarget(r), 'primary mt16', 'arrow', { run: r.id }); incomplete = incomplete || r.kind === 'synthesis_incomplete'; const evLinks = F.evidence.slice(0, 3).map(e => evidenceRow(e)).join(''); return backline('返回来源页面') + `<div class="hero" style="min-height:92px"><div class="page-heading" style="margin:0"><div><h1>${esc(r.title)}${r.id === DEFAULT_RUN ? ' · BTC 现在怎么看？' : ''}</h1><div class="meta-row mt12">${stateBadge('completed')}<span class="mono">${esc(r.id)}</span><span>${formatDate(r.at, 'full')}</span>${badge('BTC', 'blue')}${badge(r.trigger)}${r.parent ? plainLink('父任务 ' + r.parent, href('report', { run: r.parent }), '', 'branch') : ''}</div></div></div></div><div class="row between wrap mb16"><div class="row wrap">${a('用户结果', incomplete ? 'report-incomplete' : 'report', 'soft', 'file', { run: r.id }, false)}${a('技术详情', 'technical', '', 'code', { run: r.id })}</div><div class="row wrap">${btn('重新研究', 'rerun', '', 'refresh', `id="rerun"`)}${a('全屏分析链路', 'graph', '', 'expand', { run: r.id }, true, 'id="open-graph"')}${a('查看证据', 'evidence', 'primary', 'file', { evidence: 'EV-001' }, true, 'id="open-evidence"')}</div></div>${incomplete ? note('研究完成，但结论不完整', 'synthesis_incomplete：因果论证尚未完整生成。已有证据可查看；不能据此增加交易授权。', 'amber', 'warning') : note('仅供研究 · 交易权限未授权', '“偏多”是方向基线，不是已授权买入；no_trade 是行动结论，不是系统没有分析。', 'amber', 'lock')}${r.simulated ? `<p class="small mt8">此新任务为本地演示；结果结构复用交接稿样例，不是真实模型分析。</p>` : ''}<section class="report-hero mt16" id="report-summary"><div class="aside-art"></div><div class="main-copy"><div class="row">${tile('bar', 'green', 'large')}<div><h4 style="color:var(--green)">核心研究结论</h4><h2>${incomplete ? '方向已记录，因果论证待补齐' : '7 日价格规则基线：偏多'}</h2></div></div><p class="mt12">当前研究给出方向倾向，同时保留短周期不交易、概率未校准与执行未授权的边界。完整动作必须同时满足证据、触发条件和授权要求。</p></div><div class="report-stats"><div class="report-stat"><small>概率状态</small><b style="color:var(--amber)">未校准 · uncalibrated</b></div><div class="report-stat"><small>可查看证据</small><b>6 条 / 示例来源</b></div><div class="report-stat"><small>研究结果状态</small><b>${incomplete ? '论证不完整' : '可阅读研究结果'}</b></div><div class="report-stat"><small>交易执行 Gate</small><b style="color:var(--amber)">${ic('lock', 15)} 未授权</b></div></div></section><section class="section-anchor" id="candidates"><div class="section-heading"><h2 class="highlight-bar">各时间周期行动候选</h2><span class="small hide-mobile">方向、行动与权限不是同一个状态</span></div><div class="three-col">${[['30m', '短周期', 'no_trade', '当前不交易，分析结果已保留。', false], ['24h', '日内周期', 'no_trade', '当前不交易，等待条件与证据补齐。', false], ['72h', '中短周期', '保守 long 候选', '候选仅供研究，不构成交易授权。', true]].map(([p, label, action, text, featured]) => `<article class="candidate ${featured ? 'featured' : ''}"><div class="row between"><div class="row">${tile('activity', featured ? 'purple' : '')}<div><div class="period">${p}</div><small>${label}</small></div></div>${badge(featured ? '条件候选' : '暂不交易', featured ? 'purple' : '', featured ? 'chart' : 'minus')}</div><h3>${action}</h3><p>${text}</p><div class="candidate-footer"><span>${ic('info', 13)} 概率未校准</span>${plainLink('查看依据', href('evidence', { evidence: featured ? 'EV-003' : 'EV-002' }), '', 'chevron')}</div></article>`).join('')}</div></section><div class="three-col mt16" id="conditions"><section class="panel pad"><h3 class="row gap6">${tile('target', 'green', 'sm')} 触发条件</h3><ul class="condition-list"><li>检查本次 Run 返回的触发条件，不沿用旧版本阈值。</li><li>原始交接稿未提供完整数值，本原型不补造价格阈值。</li><li>有候选也不代表已有执行权限。</li></ul></section><section class="panel pad"><h3 class="row gap6">${tile('warning', 'red', 'sm')} 失效条件与反方限制</h3><ul class="condition-list red"><li>关键证据过期、来源缺失或出现新的反方证据。</li><li>方向规则变化时，需要新建复查任务。</li><li>具体失效条件应来自报告 contract。</li></ul></section><section class="panel pad"><h3 class="row gap6 mb16">${tile('lock', 'purple', 'sm')} 权限与校准</h3><dl class="kv" style="grid-template-columns:76px 1fr"><dt>概率</dt><dd>${badge('未校准', 'amber')}</dd><dt>研究结果</dt><dd>${badge(incomplete ? '论证待补齐' : '可阅读', incomplete ? 'amber' : 'blue')}</dd><dt>交易权限</dt><dd>${badge('未授权', 'amber', 'lock')}</dd><dt>执行操作</dt><dd>无买入 / 卖出入口</dd></dl></section></div><div class="split mt16"><section class="stack"><div class="panel pad section-anchor" id="report-evidence"><div class="row between mb16"><h3 class="row gap6">${ic('file', 18)} 主要依据</h3>${a('完整证据账本', 'technical', 'text', 'arrow', { section: 'evidence' })}</div><div class="evidence-rows">${evLinks}</div></div><div class="panel pad"><div class="row between"><h3>规则与反方变化示意</h3>${a('查看分析链路', 'graph', 'soft sm', 'graph', { run: r.id }, true, 'id="report-lower-graph"')}</div>${ruleChart()}<p class="small">示意线仅演示图表布局；接入后应使用有来源的时间序列。概率未校准时，不用装饰性百分比代替。</p></div></section><aside class="stack"><div class="panel pad"><h3 class="row gap6 mb16">${ic('branch', 18)} 本次任务关系</h3><dl class="kv" style="grid-template-columns:70px 1fr"><dt>当前 Run</dt><dd class="mono">${esc(r.id)}</dd><dt>父任务</dt><dd>${r.parent ? plainLink(r.parent, href('report', { run: r.parent })) : '根任务'}</dd><dt>关联资料</dt><dd>${r.doc ? plainLink(r.doc, href('document-linked', { doc: r.doc, run: r.id })) : '无关联资料'}</dd></dl><div class="anchor-list"><a href="#report-summary" data-scroll="report-summary">核心结论</a><a href="#candidates" data-scroll="candidates">分周期候选</a><a href="#conditions" data-scroll="conditions">触发与失效条件</a><a href="#report-evidence" data-scroll="report-evidence">主要依据</a></div></div>${note('重新研究保留原报告', '新建任务与本次 Run 建立父子关系。返回列表不会覆盖或重置报告。', 'neutral', 'branch')}</aside></div>`; }
function evidenceRow(e) { return `<a class="evidence-row" href="${esc(href('evidence', { evidence: e.id }))}" id="evidence-${e.id}">${tile('file', e.relation === '反方限制' ? 'red' : '', 'sm')}<div class="grow"><div class="row between"><h4>${esc(e.title)}</h4>${ic('chevron', 13)}</div><p>${esc(e.claim)}</p><div class="tiny">${esc(e.id)} · ${esc(e.source)} · ${esc(e.quality)}</div></div></a>`; }
function evidenceBody() { const e = F.evidence.find(x => x.id === (params.get('evidence') || 'EV-001')); if (!e)
    return empty('证据不存在', '当前证据 ID 不在本原型样例中，不替换成另一条证据。', plainLink('返回来源', returnHref(), 'btn primary'), 'warning', true); const r = currentRun(); return `<div class="row mb16">${tile('file', 'purple', 'large')}<div><h2 style="font-size:20px">${esc(e.title)}</h2><p class="mono small">${esc(e.id)} / ${esc(r?.id || params.get('run'))}</p></div></div><div class="row wrap mb16">${badge(e.relation, e.relation === '反方限制' ? 'red' : 'blue')}${badge(e.quality, e.origin === 'source' ? 'green' : 'amber')}</div>${note('证据摘要', esc(e.claim), 'neutral', 'file')}<h3 class="mt24 mb16">来源与质量</h3><dl class="kv"><dt>来源身份</dt><dd>${esc(e.source)}</dd><dt>权威性字段</dt><dd>${esc(e.authority)}</dd><dt>质量字段</dt><dd>${esc(e.quality)}</dd><dt>观察时间</dt><dd>${e.observed ? formatDate(e.observed, 'full') + '（演示元数据）' : '未提供'}</dd><dt>证据要求</dt><dd>${esc(e.type)}</dd><dt>来源 URL</dt><dd>未提供单条来源链接，不生成虚构地址</dd></dl><div class="mt24">${e.origin === 'source' ? note('原文位置', '上传的《Decision Hub 页面级使用、交互与截图交接稿》第 7 页。这里展示的是用户提供的产品样例，而非实时市场证据。', 'blue', 'book') : note('设计演示条目', '只用于检查 UI 状态、关系与限制，不应进入生产证据账本。', 'amber', 'info')}</div><h3 class="mt24 mb16">与本次研究的关联</h3><div class="stack">${plainLink('返回本次研究报告', href('report', { run: r?.id || DEFAULT_RUN }), 'btn soft', 'file')}${plainLink('在分析链路中定位', href('graph-node', { node: e.relation === '反方限制' ? 'counter' : 'evidence', run: r?.id || DEFAULT_RUN }), 'btn', 'target')}</div>`; }
function accordion(id, name, desc, icon, color, body, open = false) { return `<details class="audit-accordion" id="${id}" ${open ? 'open' : ''}><summary>${tile(icon, color + ' solid')}<div class="grow"><h3>${name}</h3><p>${desc}</p></div><span class="chev">${ic('down', 17)}</span></summary><div class="audit-content">${body}</div></details>`; }
function technicalPage() {
    const r = currentRun();
    if (!r)
        return backline() + empty('Run 不存在', '请选择已有研究任务。', a('任务列表', 'runs', 'primary'), 'warning');
    const table = `<div class="row between mb12"><h4>证据账本（6 条）</h4>${badge('不虚构来源 URL', 'blue')}</div><div class="table-scroll"><table class="data-table" style="min-width:670px"><thead><tr><th>证据</th><th>来源</th><th>权威性</th><th>质量</th><th>观察时间</th><th></th></tr></thead><tbody>${F.evidence.map(e => `<tr><td><strong>${esc(e.title)}</strong><div class="mono small">${esc(e.id)}</div></td><td>${esc(e.source)}</td><td>${esc(e.authority)}</td><td>${badge(e.quality, e.origin === 'source' ? 'green' : 'amber')}</td><td>${e.observed ? formatDate(e.observed, 'full') : '未提供'}</td><td>${a('打开', 'evidence', 'text', 'chevron', { evidence: e.id })}</td></tr>`).join('')}</tbody></table></div>`;
    const evidence = accordion('evidence', 'Evidence · 证据账本', '权威性、质量、观察时间、证据要求与来源', 'file', '', table, true);
    const coverage = accordion('coverage', 'Coverage · 覆盖与缺口', '必需证据要求的覆盖，不是模型置信度', 'target', 'green', `<div class="coverage-grid">${ring(75, 88)}<div><h3>9 / 12 项要求已覆盖（演示）</h3><p class="small mt8">口径：已满足的必需证据要求数 / 必需要求总数。不是成功概率。</p><div class="row wrap mt12">${badge('硬缺口 1', 'red')}${badge('软缺口 2', 'amber')}${badge('其余 9 项已覆盖', 'green')}</div></div></div>`, true);
    const counter = accordion('counter', 'Counter thesis · 反方链', '有可能推翻方向的证据与限制', 'warning', 'red', `<div class="two-col">${note('来源缺口', '未取得的关键资料保持缺失，不用模型推测补齐。', 'amber', 'file')}${note('执行边界', '概率未校准；短周期 no_trade；交易仍未授权。', 'red', 'lock')}</div>`, true);
    const review = accordion('review', 'Review · 独立复核', '复核状态与研究结束状态分离', 'users', 'purple', note('有保留的复核状态', '本原型未执行真实独立研究审查。仅展示复核字段与限制；不能用“运行成功”替代“已审查通过”。', 'neutral', 'review'), true);
    return backline('返回本次研究报告') + title('技术详情', '证据、覆盖、反方与复核的结构化摘要。不是模型私有逐字思维链。', a('用户结果', 'report', '', 'file', { run: r.id }) + a('全屏分析链路', 'graph', 'primary', 'graph', { run: r.id }, true, 'id="technical-graph"')) + `<div class="meta-row mb16">${stateBadge(r.status)}<span>${esc(r.title)}</span><span class="mono">${esc(r.id)}</span></div><div class="split"><section>${accordion('experts', '专家角色证据包', '分别记录观察、观点与引用来源', 'users', 'green', `<div class="three-col">${[['价格规则', 'EV-001'], ['执行边界', 'EV-002'], ['周期候选', 'EV-003']].map(([t, id]) => `<div class="panel pad"><h4>${t}</h4><p class="small mt8">角色观察与来源分开保留。</p>${a('查看关联证据', 'evidence', 'text', 'arrow', { evidence: id })}</div>`).join('')}</div>`)}${accordion('trace', 'Research trace · 过程摘要', '研究轮次、工具事件及运行结果', 'search', '', `<div class="stack">${note('公开过程摘要', '已登记研究请求、读取来源、形成证据账本、检查覆盖与复核状态。只展示结构化审计事件。', 'blue', 'activity')}${a('进入内部技术排查', 'dsh', '', 'code', { run: r.id }, true, 'id="open-dsh"')}</div>`)}${evidence}${coverage}${counter}${review}${accordion('forecast', 'Forecast audit · 预测复盘', '预测点、复盘时刻与校准状态', 'chart', 'purple', `<dl class="kv"><dt>校准状态</dt><dd>${badge('uncalibrated', 'amber')}</dd><dt>校准概率</dt><dd>未提供，不展示成功率百分比</dd><dt>复盘时间</dt><dd>待服务端返回 forecast-audit</dd></dl>`)}${accordion('commands', 'Owner command · 后续动作', '新建复查，保留父子 Run 关系', 'settings', '', `<div class="row wrap">${btn('基于本次结果重新研究', 'rerun', 'primary', 'refresh')}${a('返回研究任务列表', 'runs', '', 'layers')}</div><p class="small mt12">不提供无后端支持的交易授权按钮。</p>`)}</section><aside class="stack sticky-aside"><div class="panel pad"><h3 class="row gap6 mb16">${ic('activity', 18)} 本次运行</h3><div class="row">${tile('check-circle', 'green', 'large')}<div><h2 style="font-size:20px">${r.status === 'completed' ? '研究已结束' : '研究状态可追踪'}</h2><p class="small">完成不等于通过所有质量门</p></div></div><hr/><dl class="kv" style="grid-template-columns:72px 1fr"><dt>Run</dt><dd class="mono">${esc(r.id)}</dd><dt>结论状态</dt><dd>${badge(r.kind === 'synthesis_incomplete' ? '不完整' : '仅供研究', 'amber')}</dd><dt>概率校准</dt><dd>${badge('未校准', 'amber')}</dd><dt>交易执行</dt><dd>${badge('未授权', 'amber', 'lock')}</dd></dl></div><div class="panel pad"><h3>审计目录</h3><div class="anchor-list">${[['experts', '专家角色'], ['trace', '过程摘要'], ['evidence', '证据账本'], ['coverage', '覆盖与缺口'], ['counter', '反方链'], ['review', '独立复核'], ['forecast', '预测复盘'], ['commands', '后续动作']].map(([id, t]) => `<a href="#${id}" data-scroll="${id}">${t}</a>`).join('')}</div></div><div class="panel pad"><h3 class="row gap6">${ic('code', 17)} 内部技术排查</h3><p class="small mt8 mb12">DSH 仅排查当前 Run，不产生另一份用户交易结论。</p>${a('打开 DSH 技术轨迹', 'dsh', 'soft w100', 'external', { run: r.id })}</div></aside></div>`;
}
function flowStepper(stage) { return `<div class="flow-stepper">${[['上传资料', '保留原始文件'], ['解析处理', '提取结构'], ['语义修正', '对照修改'], ['审核确认', '版本与质量门'], ['进入研究', '关联新 Run']].map(([t, d], i) => `<div class="flow-step"><span class="step-dot ${i < stage ? 'done' : i === stage ? 'current' : ''}">${i < stage ? ic('check', 16) : i + 1}</span><div><strong>${t}</strong><small>${d}</small></div></div>`).join('')}</div>`; }
function libraryPage() { const docs = Object.values(S.documents).filter(d => !d.id.startsWith('preview-')); return backline('返回来源页面') + hero('资料研究入口', '从上传到审核，再到研究任务。原文、修正版本和关联关系完整保留。', 'Research Library') + flowStepper(0) + `<div class="split"><section class="panel"><div class="panel-head divider"><h3 class="row">${ic('book', 18)} 研究资料库 <span class="small">${docs.length} 份</span></h3>${a('上传资料', 'upload', 'primary', 'upload', {}, true, 'id="library-upload"')}</div><div class="panel-body"><div class="file-rows">${docs.map(d => `<div class="file-row">${tile('file', 'purple')}<div class="grow"><a href="${esc(href(docScene(d), { doc: d.id }))}" style="color:var(--ink);font-weight:600">${esc(d.title)}</a><p>v${d.version} · ${esc(d.market)} · ${d.privacy === 'private' ? '仅自己（演示）' : '组织内（演示）'}</p></div>${badge(docStatus(d), ['ready', 'linked'].includes(d.status) ? 'green' : d.status === 'failed' ? 'red' : 'amber')}${a('打开', docScene(d), 'text', 'chevron', { doc: d.id })}</div>`).join('')}</div><div class="mt16">${note('审核样例说明', '资料列表是本地演示记录；预览中的文字不是对所选文件实际解析得到的内容。', 'neutral', 'info')}</div></div></section><aside class="stack"><div class="panel pad"><h3 class="row gap6 mb16">${tile('shield-check', 'green', 'sm')} 原文与版本</h3><p class="small">原始文件、解析版本和确认后的修正版本分别记录。接受修改不覆盖原文，拒绝修改保留原文。</p><hr/><div class="stack"><div class="row">${ic('file', 17)}<span>原始资料 <small>不可静默覆盖</small></span></div><div class="row">${ic('diff', 17)}<span>修正版本 <small>逐条处理差异</small></span></div><div class="row">${ic('lock', 17)}<span>质量门 <small>未通过不能接入</small></span></div></div></div>${note('资料与任务双向可追溯', '资料进入新 Run 后，任务报告保留资料 ID / 版本；资料页可返回关联任务。', 'blue', 'link')}</aside></div>`; }
function docStatus(d) { return ({ review: '待审核', uploading: '上传中', parsing: '解析中', quality: '质量不足', confirmed: '复检中', ready: '可用于研究', linked: '已连接研究', failed: '解析失败' }[d.status] || '待处理'); }
function docScene(d) { return 'document-' + ({ review: 'review', uploading: 'uploading', parsing: 'parsing', quality: 'quality', confirmed: 'confirmed', ready: 'ready', linked: 'linked', failed: 'failed' }[d.status] || 'review'); }
function uploadPage() { return backline('返回资料库') + hero('上传研究资料', '填写资料信息，明确隐私范围，再进入解析与审核。', 'Research Library') + flowStepper(0) + `<form id="upload-form"><div class="two-col"><section class="panel pad"><div class="upload-area" id="dropzone">${tile('upload', '', 'large')}<h2 style="font-size:19px">选择或拖拽研究资料</h2><p>PDF、PNG、JPG、WEBP、TXT、MD、HTML</p><label class="btn primary" for="file-input">${ic('upload', 17)} 选择文件</label><input type="file" id="file-input" class="sr-only" name="researchFile" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.html"/><p id="file-name">尚未选择文件</p><div class="inline-error" id="file-error" role="status"></div></div><p class="file-limit mt12">50 MB 是本原型的演示校验上限；真实大小约束须与后端确认。原型只读取文件名、类型与大小，不读取文件内容或上传。</p>${btn('直接体验审核样例', 'sample-upload', 'soft mt16 w100', 'file')}</section><section class="panel pad"><h3 class="row mb16">${ic('file', 18)} 资料信息</h3><div class="stack"><div class="form-field"><label for="doc-title">资料标题 <span class="required">*</span></label><input id="doc-title" name="documentTitle" autocomplete="off" placeholder="例如：宏观研究资料…" maxlength="120"/><div class="inline-error" id="title-error" role="status"></div></div><div class="two-col"><div class="form-field"><label for="doc-market">市场 <span class="required">*</span></label><select id="doc-market" name="market"><option>BTC / 宏观</option><option>美国</option><option>中国</option><option>全球</option></select></div><div class="form-field"><label for="doc-symbol">标的（选填）</label><input id="doc-symbol" name="symbol" autocomplete="off" placeholder="例如：BTC…"/></div></div><div class="form-field"><label>隐私模式（演示映射）</label><div class="privacy-options"><label><input type="radio" name="privacy" value="private" checked/>仅自己</label><label><input type="radio" name="privacy" value="organization"/>组织内</label></div><p class="small">不提供含义混乱的“公开（团队可见）”。权限枚举与组织边界需接入时确认。</p></div>${note('不会绕过审核', '上传成功后仍需解析、语义核对、明确确认和质量复检。', 'green', 'shield-check')}</div></section></div><div class="review-actions"><p>仅运行本地流程演示，不访问真实上传接口。</p><div class="row">${plainLink('返回资料库', returnHref(), 'btn ghost')}<button id="upload-submit" class="btn primary" type="submit">${ic('play', 17)} 上传并解析（演示）</button></div></div></form>`; }
function docHeader(d, stage) { return backline('返回资料库 / 来源页面') + hero('资料研究 · ' + esc(d.title), '审核版本不会覆盖原文；质量门通过后才能用于新研究。', 'Research Library') + flowStepper(stage) + `<div class="meta-row mb16">${badge(docStatus(d), d.status === 'failed' ? 'red' : ['ready', 'linked'].includes(d.status) ? 'green' : 'blue')}<span class="mono">${esc(d.id)} / v${d.version}</span><span>${esc(d.filename)}</span><span>${d.privacy === 'private' ? '仅自己' : '组织内'} · 演示</span></div>`; }
function documentPage(kind) {
    const d = currentDoc();
    if (!d)
        return backline() + empty('资料不存在', '返回资料库重新选择。', a('资料库', 'library', 'primary'), 'warning');
    const statusMap = { uploading: 'uploading', parsing: 'parsing', quality: 'quality', confirmed: 'confirmed', ready: 'ready', linked: 'linked', failed: 'failed' };
    const sd = { ...d, status: statusMap[kind] || d.status };
    let content = docHeader(sd, ['uploading'].includes(kind) ? 0 : ['parsing', 'failed', 'quality'].includes(kind) ? 1 : kind === 'linked' ? 4 : 3);
    if (['uploading', 'parsing', 'confirmed'].includes(kind)) {
        const labels = { uploading: ['正在上传…', '上传记录模拟中', 35], parsing: ['正在解析资料…', '提取文本、表格关系与页码', 65], confirmed: ['人工审核已确认，正在质量复检…', '人工确认不代表质量门已经通过', 80] };
        const [t, s, p] = labels[kind];
        return content + `<div class="panel upload-progress"><span class="spinner"></span><div><h3>${t}</h3><p class="small">${s}</p></div>${progress(p)}<b>${p}%</b></div><div class="two-col mt16"><div class="panel pad"><h3>当前处理步骤</h3><div class="timeline mt16">${[['文件记录', '本原型仅记录元数据'], ['解析结构', '演示文本、表格和块关系'], ['质量检查', '检查完整性与敏感语义']].map(([t, s], i) => `<div class="timeline-row"><span class="step-dot ${i === 1 ? 'current' : i === 0 ? 'done' : ''}">${i === 0 ? ic('check', 14) : i + 1}</span><div class="step-copy"><b>${t}</b><p>${s}</p></div></div>`).join('')}</div></div><div class="stack">${note('原文保持不变', '原始文件、原始解析、修正稿分别保留。', 'green', 'shield-check')}${note('进度为固定演示', '通过目录进入时保留该状态；从上传操作进入时自动推进。真实解析进度须由服务端返回。', 'neutral', 'info')}${a('返回资料库', 'library', '', 'back', { doc: d.id })}</div></div>`;
    }
    if (kind === 'quality')
        return content + note('质量门未通过 · 1 个硬缺口', '未决块包含语义变化。必须处理差异并重新确认，不能直接加入研究。', 'red', 'shield') + `<div class="panel pad mt16"><div class="row between"><h3>未决块清单</h3>${badge('阻断接入', 'red')}</div><div class="evidence-row mt16">${tile('warning', 'red')}<div><h4>交易权限限定词发生变化</h4><p>“未授权”不能自动变成“已授权”。拒绝错误建议并保留原文。</p>${a('打开语义修正审核', 'document-review', 'primary mt12', 'diff', { doc: d.id })}</div></div><button class="btn mt16" disabled>结合此资料获取最新建议</button></div>`;
    if (kind === 'failed')
        return content + note('资料解析失败', '错误码 PARSE_TIMEOUT · 来源 document_parser · retryable: true。原始元数据已保留。', 'red', 'warning') + `<div class="panel pad mt16"><dl class="kv"><dt>资料</dt><dd>${esc(d.filename)}</dd><dt>原始版本</dt><dd>v${d.version}，未覆盖</dd><dt>失败阶段</dt><dd>结构提取（演示）</dd></dl><div class="row mt24">${btn('重试解析', 'retry-doc', 'primary', 'refresh')}${a('返回资料库', 'library', '', 'back')}</div></div>`;
    if (kind === 'ready')
        return content + `<div class="panel document-success">${tile('shield-check', 'green', 'large')}<h2>审核已完成，资料可用于研究</h2><p>已保留原文并建立确认版本。差异均已处理，质量门已通过。接下来将创建新的研究任务，并把本版本关联到任务上下文。</p><div class="row wrap">${badge('差异已处理', 'green', 'check')}${badge('原文保留', 'green', 'file')}${badge('质量门通过', 'green', 'shield-check')}</div><div class="row wrap">${btn('结合此资料获取最新建议', 'link-doc', 'primary large', 'bolt', 'id="link-document"')}${a('查看审核记录', 'document-review', '', 'diff', { doc: d.id })}</div></div>`;
    if (kind === 'linked') {
        const linked = d.linked || params.get('run') || DEFAULT_RUN;
        return content + `<div class="panel document-success">${tile('link', 'green', 'large')}<h2>资料已连接到研究任务</h2><p>${esc(d.title)} · v${d.version} 与 ${esc(linked)} 已建立关联。打开任务、返回资料页都保持该版本和同一 Run。</p><div class="row wrap">${a('查看关联研究', 'report', 'primary', 'file', { run: linked, doc: d.id }, true, 'id="linked-report"')}${a('回到资料库', 'library', '', 'book', { doc: d.id })}</div></div>`;
    }
    return content;
}
function reviewReady(d) { return F.diffs.every(x => ['accepted', 'rejected'].includes(d.decisions[x.id])) && d.decisions['blk-03'] === 'rejected'; }
function reviewPage() { const d = currentDoc(); if (!d)
    return backline() + empty('资料不存在', '请选择资料。', a('资料库', 'library', 'primary'), 'warning'); const selected = Math.max(0, Math.min(2, d.selected || 0)), diff = F.diffs[selected], done = Object.keys(d.decisions).filter(k => ['accepted', 'rejected'].includes(d.decisions[k])).length, tab = params.get('docTab') || 'correction'; const raw = F.diffs.map(x => esc(x.original)).join('\n\n'), proposed = F.diffs.map(x => { const dec = d.decisions[x.id]; let t = dec === 'rejected' ? x.original : x.proposed; let out = esc(t); if (dec !== 'rejected') {
    if (x.risk)
        out = out.replaceAll('已', '<mark class="danger-mark">已</mark>');
    else
        out = out.replace(esc(x.after), '<mark>' + esc(x.after) + '</mark>');
} return out; }).join('\n\n'); return docHeader(d, 2) + `<div class="row wrap mb16">${note('原文始终保留', '当前对照使用固定交接稿改写样例，不是对你所选文件进行真实解析的结果。', 'green', 'shield-check')}</div><div class="panel"><div class="tab-row">${[['raw', '原始解析'], ['tables', '表格'], ['images', '图片'], ['correction', '语义修正'], ['quality', '质量信号'], ['unresolved', '未决块']].map(([v, t]) => `<a class="${tab === v ? 'active' : ''}" href="${esc(href('document-review', { doc: d.id, docTab: v, back: params.get('back') }, false))}">${t}${v === 'unresolved' ? ' (' + (3 - done) + ')' : ''}</a>`).join('')}</div><div class="panel-body">${tab === 'correction' ? `<div class="review-layout"><section><div class="row between wrap"><h3 class="row gap6">${ic('diff', 20)} 原文与修正稿对照</h3><span class="small">已处理 ${done} / 3 个差异块</span></div><div class="diff-pair"><article class="diff-pane"><header class="diff-header"><h4>原文（只读）</h4>${badge('永久保留', 'red', 'lock')}</header><div class="diff-content" id="original-text">${raw}</div></article><article class="diff-pane"><header class="diff-header"><h4>修正建议</h4>${badge('需人工确认', 'amber')}</header><div class="diff-content" id="proposed-text">${proposed}</div></article></div>${F.diffs.map((x, i) => { const dec = d.decisions[x.id]; return `<article class="diff-item ${i === selected ? 'active' : ''}" id="diff-${x.id}"><header><h4 class="row gap6">${ic(x.risk ? 'warning' : 'diff', 16)} ${x.label}</h4>${badge(dec === 'accepted' ? '已接受' : dec === 'rejected' ? '已拒绝并保留原文' : '待处理', dec === 'accepted' ? 'green' : dec === 'rejected' ? 'blue' : x.risk ? 'red' : 'amber')}</header><p>${esc(x.note)}</p><div class="row between wrap"><span class="small">${esc(x.location)}</span><div class="row gap6">${btn('拒绝并保留原文', 'reject-diff', 'danger sm', 'close', `data-block="${x.id}" id="reject-${x.id}"`)}${btn(x.risk ? '风险修改不可接受' : '接受本条', 'accept-diff', 'soft sm', 'check', `data-block="${x.id}" id="accept-${x.id}" ${x.risk ? 'disabled aria-describedby="risk-help"' : ''}`)}</div></div></article>`; }).join('')}<p id="risk-help" class="small mt12">涉及事实、否定词、数字与权限的变更不能自动接受。实际编辑修正稿需要额外版本与审计接口。</p></section><aside class="stack"><div class="panel pad" style="box-shadow:none"><h3 class="row mb12">${ic('shield-check', 17)} 质量检查</h3>${[['事实限定词', 'blk-03', '原文“未校准、未授权”必须保留'], ['术语规范', 'blk-01', '只统一字形，不变更结论'], ['周期规范', 'blk-02', '不改变 30m / 24h 含义']].map(([t, k, n]) => `<div class="quality-item">${ic(d.decisions[k] ? 'check-circle' : 'warning', 18)}<div class="grow"><h4>${t}</h4><p>${n}</p></div>${badge(d.decisions[k] ? '已处理' : '待确认', d.decisions[k] ? 'green' : 'amber')}</div>`).join('')}</div>${note('质量门', '未决块全部处理、明确确认并通过复检后，才显示接入研究按钮。', 'amber', 'lock')}<div class="panel pad"><h3>当前版本</h3><p class="small mt8">原文 v1 永久保留。确认后生成新的审核版本，不改变原始数据。</p></div></aside></div>` : tab === 'raw' ? `<h3>原始解析文本（只读）</h3><pre class="log-code mt16">${raw}</pre>` : tab === 'tables' || tab === 'images' ? empty(tab === 'tables' ? '样例没有表格' : '样例没有图片', '当前固定审核样例只包含文本。没有解析出的内容时，不展示虚构表格或图片。', '', tab === 'tables' ? 'grid' : 'image', true) : tab === 'quality' ? note('质量状态与人工确认分离', `${done}/3 个差异已处理。${reviewReady(d) ? '可以发起明确确认，随后仍需质量复检。' : '必须先处理未决块。'}`, 'amber', 'shield') : `<h3>未决块 ${3 - done}</h3><div class="stack mt16">${F.diffs.filter(x => !d.decisions[x.id]).map(x => note(x.label, esc(x.note), x.risk ? 'red' : 'amber', 'warning')).join('') || note('所有差异已处理', '下一步明确确认审核范围。', 'green', 'check')}${a('返回对照审核', 'document-review', 'primary', 'diff', { doc: d.id, docTab: 'correction' })}</div>`}</div></div><div class="review-actions"><p><strong>${done} / 3</strong> 个差异已处理 · 拒绝建议不会丢失原文</p><div class="row wrap">${btn('保存审核草稿', 'save-doc', '', 'file')}${btn('确认修正稿', 'open-confirm', 'primary', 'check', `id="review-confirm" ${reviewReady(d) ? '' : 'disabled'}`)}</div></div>`; }
function confirmBody() { const d = currentDoc(); return `<div class="stack">${note('确认本次审核范围', '此操作生成新版本，保留原文件与原始解析。不会将“人工确认”直接当作质量门通过。', 'blue', 'review')}<dl class="kv"><dt>资料</dt><dd>${esc(d?.title)}</dd><dt>版本</dt><dd>原文 v1 → 审核版本 v${(d?.version || 1) + 1}</dd><dt>接受修改</dt><dd>${Object.values(d?.decisions || {}).filter(x => x === 'accepted').length} 条</dd><dt>拒绝建议</dt><dd>${Object.values(d?.decisions || {}).filter(x => x === 'rejected').length} 条，保留原文</dd><dt>未决块</dt><dd>${F.diffs.filter(x => !d?.decisions[x.id]).length} 条</dd></dl><label class="row" style="align-items:flex-start;font-size:13px"><input type="checkbox" id="review-ack" name="acknowledgement"/><span>我已核对所有差异，确认保留原文，并同意将本次审核结果提交质量复检。</span></label><p class="small">演示动作不写入真实文档，实际确认接口待后端对齐。</p></div>`; }
const NODES = [
    { id: 'trigger', x: 454, y: 15, type: 'trigger', icon: 'play', c: '', title: '研究触发', desc: 'BTC / 宏观模板', status: '已登记', evidence: [] },
    { id: 'round-1', x: 454, y: 126, type: 'round', icon: 'file', c: 'purple', title: '研究轮次 1', desc: '公开来源与价格规则', status: '已完成', evidence: ['EV-001'] },
    { id: 'tool-1', x: 35, y: 252, type: 'tool', icon: 'search', c: 'green', title: '工具调用 · 检索', desc: '公开来源收集', status: '部分来源缺失', evidence: ['EV-004'] },
    { id: 'tool-2', x: 255, y: 252, type: 'tool', icon: 'bar', c: 'green', title: '工具调用 · 规则', desc: '规则快照读取', status: '已完成', evidence: ['EV-001'] },
    { id: 'tool-3', x: 475, y: 252, type: 'tool', icon: 'code', c: 'green', title: '工具调用 · 核查', desc: '候选边界核查', status: '已完成', evidence: ['EV-002'] },
    { id: 'round-2', x: 865, y: 252, type: 'round', icon: 'file', c: 'purple', title: '研究轮次 2', desc: '主链与反方对照', status: '已完成', evidence: ['EV-003', 'EV-004'] },
    { id: 'evidence', x: 35, y: 387, type: 'evidence', icon: 'file', c: '', title: '证据 · 来源核查', desc: '来源与时间完整性', status: '有缺口', evidence: ['EV-004'] },
    { id: 'evidence-2', x: 255, y: 387, type: 'evidence', icon: 'file', c: '', title: '证据 · 规则基线', desc: '7 日规则方向', status: '可追溯', evidence: ['EV-001'] },
    { id: 'evidence-3', x: 475, y: 387, type: 'evidence', icon: 'file', c: '', title: '证据 · 执行边界', desc: 'no_trade / 未授权', status: '可追溯', evidence: ['EV-002', 'EV-003'] },
    { id: 'main-chain', x: 754, y: 387, type: 'main', icon: 'bar', c: 'green', title: '主链 · 方向论据', desc: '方向性研究依据', status: '仅供研究', evidence: ['EV-001'] },
    { id: 'counter', x: 959, y: 387, type: 'counter', icon: 'warning', c: 'red', title: '反方链 · 限制', desc: '来源缺口与执行限制', status: '限制保留', evidence: ['EV-004', 'EV-005'] },
    { id: 'coverage', x: 255, y: 532, type: 'gate', icon: 'shield', c: 'amber', title: '覆盖与质量门', desc: '9 / 12 项 · 3 项缺口', status: '未完全覆盖', evidence: ['EV-004'] },
    { id: 'review', x: 836, y: 532, type: 'review', icon: 'users', c: 'purple', title: '独立复核记录', desc: '复核字段与限制', status: '待真实复核', evidence: ['EV-006'] },
    { id: 'forecast', x: 949, y: 674, type: 'forecast', icon: 'chart', c: 'green', title: '预测与校准', desc: '概率未校准', status: 'uncalibrated', evidence: ['EV-003'] },
    { id: 'conclusion', x: 524, y: 674, type: 'conclusion', icon: 'file', c: '', title: '研究结果输出', desc: '偏多 ≠ 已授权买入', status: '仅供研究', evidence: ['EV-001', 'EV-002', 'EV-003'] }
];
const EDGES = [['trigger', 'round-1'], ['round-1', 'tool-1'], ['round-1', 'tool-2'], ['round-1', 'tool-3'], ['round-1', 'round-2'], ['tool-1', 'evidence'], ['tool-2', 'evidence-2'], ['tool-3', 'evidence-3'], ['evidence', 'coverage'], ['evidence-2', 'coverage'], ['evidence-3', 'coverage'], ['round-2', 'main-chain'], ['round-2', 'counter'], ['main-chain', 'review'], ['counter', 'review'], ['coverage', 'conclusion'], ['review', 'conclusion'], ['review', 'forecast']];
const TYPE_LABEL = { trigger: '研究触发', round: '研究轮次', tool: '工具调用', evidence: '证据', main: '主链', counter: '反方链', gate: '覆盖检查', review: '独立复核', forecast: '预测校准', conclusion: '结论输出' };
function graphData() { const k = params.get('run') || DEFAULT_RUN; return S.graph[k] || (S.graph[k] = { z: 0, x: 0, y: 0, groups: true, collapsed: false, filter: 'all', q: '', list: false }); }
function graphPage(withInspector = false) { const r = currentRun() || { id: params.get('run') || DEFAULT_RUN, title: '未找到该 Run' }, g = graphData(), selected = params.get('node') || 'round-2', node = NODES.find(n => n.id === selected); return `<main class="graph-shell" id="main" tabindex="-1"><header class="graph-topbar">${logo()}<div class="grow"><h1>${esc(r.title)} · 全屏分析链路</h1><p>${esc(r.id)} · 结构化审计图谱，不是模型私有思维链</p></div><div class="row hide-mobile"><span class="small">${NODES.length} 节点</span>${badge('6 条证据账本', 'blue')}${badge('未授权交易', 'amber', 'lock')}</div>${plainLink('退出全屏', returnHref(), 'btn soft', 'expand', 'id="exit-graph"')}</header><div class="graph-toolbar"><div class="search-field">${ic('search', 16)}<input id="graph-search" name="graphSearch" aria-label="搜索图谱节点" placeholder="搜索节点或证据…" autocomplete="off" value="${esc(g.q)}"/></div><select id="graph-filter" name="graphFilter" aria-label="节点类型">${[['all', '全部类型'], ['evidence', '证据'], ['tool', '工具调用'], ['counter', '反方链'], ['gate', '质量门']].map(([v, t]) => `<option value="${v}" ${g.filter === v ? 'selected' : ''}>${t}</option>`).join('')}</select><label class="hide-mobile"><input id="graph-groups" name="showGroups" type="checkbox" ${g.groups ? 'checked' : ''}/>显示分组</label>${btn(g.collapsed ? '展开工具组' : '折叠工具组', 'graph-collapse', 'ghost sm hide-mobile', 'layers')}${btn(g.list ? '图形视图' : '列表视图', 'graph-list', 'soft sm', 'layers', 'id="graph-view-toggle"')}<span class="grow hide-mobile"></span><span class="small hide-mobile">设计演示 · 节点与来源 ID 对应</span></div><div class="graph-workspace"><div id="graph-canvas" class="graph-canvas" tabindex="0" aria-label="分析图谱画布，方向键平移，加减缩放，F 适配"><div id="graph-stage" class="graph-stage"><div class="graph-group" data-group="tools" style="left:20px;top:232px;width:653px;height:247px;${!g.groups ? 'display:none' : ''}"><span>来源读取与证据账本</span></div><div class="graph-group" data-group="review" style="left:741px;top:361px;width:413px;height:265px;${!g.groups ? 'display:none' : ''}"><span>方向与反方交叉核对</span></div><svg class="graph-edges" width="1170" height="780" viewBox="0 0 1170 780"><defs><marker id="arrowhead" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#7f9bc2"/></marker></defs><g id="graph-edge-paths"></g></svg>${NODES.map((n, i) => `<button class="graph-node ${n.c} ${selected === n.id && withInspector ? 'selected' : ''}" id="node-${n.id}" style="left:${n.x}px;top:${n.y}px" data-action="select-node" data-node="${n.id}" data-type="${n.type}" aria-label="${n.title}，${n.status}">${tile(n.icon, n.c)}<span class="grow"><h3>${n.title}</h3><p>${n.desc}</p></span><span class="node-id">#${i + 1}</span></button>`).join('')}<div id="collapsed-tools" style="display:none;position:absolute;left:255px;top:252px;width:183px" class="panel pad"><b style="font-size:12px">3 个工具节点已折叠</b><p class="small">关联证据保持可见</p></div></div><div id="graph-empty" class="graph-empty" hidden><h3>没有匹配的节点</h3><p class="small mt8">清除关键词或节点类型筛选。</p>${btn('重置图谱筛选', 'graph-reset', 'soft mt12', 'refresh')}</div><div class="graph-legend"><b>节点类型</b><div>${[['研究', 'blue'], ['工具', 'green'], ['证据', 'blue'], ['反方', 'red'], ['质量门', 'amber'], ['复核', 'purple']].map(([t, c]) => `<span><i style="background:var(--${c === 'blue' ? 'brand' : c})"></i>${t}</span>`).join('')}</div></div><div class="graph-hint">${ic('hand', 14)} 拖动画布 · 滚轮缩放 · 点击节点 · F 适配</div><div class="graph-map" id="graph-minimap" role="button" tabindex="0" aria-label="小地图，点击定位画布"><svg viewBox="0 0 1170 780">${NODES.map(n => `<rect x="${n.x}" y="${n.y}" width="183" height="78" rx="8" fill="${n.c === 'red' ? '#f1a8b8' : n.c === 'green' ? '#9cdbc6' : n.c === 'purple' ? '#c5b2f4' : '#a5cdfc'}"/>`).join('')}<rect id="minimap-viewport" x="10" y="10" width="1150" height="760" stroke="#337af2" stroke-width="7" fill="#4a8eff08"/></svg></div><div class="graph-controls">${btn('', 'graph-zoom-in', 'icon', 'plus', 'aria-label="放大图谱"')}${btn('', 'graph-zoom-out', 'icon', 'minus', 'aria-label="缩小图谱"')}${btn('', 'graph-fit', 'icon', 'fit', 'aria-label="适配画布"')}<span id="zoom-label" class="small" style="text-align:center;font-size:10px">100%</span></div><div id="graph-list-panel" class="graph-list" ${g.list ? '' : 'hidden'}><h3 class="mb16">节点列表 · 无需操作画布即可查看</h3><div class="table-scroll"><table class="data-table"><thead><tr><th>节点</th><th>类型</th><th>状态</th><th>证据</th></tr></thead><tbody>${NODES.map(n => `<tr><td>${btn(n.title, 'select-node', 'text', '', `data-node="${n.id}"`)}</td><td>${TYPE_LABEL[n.type]}</td><td>${esc(n.status)}</td><td>${n.evidence.join(', ') || '—'}</td></tr>`).join('')}</tbody></table></div></div></div>${withInspector ? graphInspector(node) : ''}</div></main>`; }
function graphInspector(n) { if (!n)
    return `<aside class="graph-inspector"><div class="panel-head"><h3>节点不存在</h3>${btn('关闭', 'close-node', 'ghost sm', 'close')}</div><p class="panel-body small">所选节点 ID 无数据，请关闭后重新选择。</p></aside>`; return `<aside class="graph-inspector" aria-label="节点检查器"><div class="panel-head"><h3 class="row gap6">${ic('graph', 17)} 节点详情</h3>${btn('', 'close-node', 'ghost icon', 'close', 'aria-label="关闭节点详情"')}</div><div class="graph-inspector-body"><div class="panel pad"><div class="row">${tile(n.icon, n.c)}<div><h3>${n.title}</h3><p class="small">${n.id} · ${TYPE_LABEL[n.type]}</p></div></div><div class="mt12">${badge(n.status, n.c || 'blue')}</div><p class="small mt12">${n.desc}。节点内容用于可复核的结构化摘要，不展示模型私有逐字思维链。</p></div><h3 class="mt24 mb12" style="font-size:13px">关联证据（${n.evidence.length}）</h3><div class="evidence-rows">${n.evidence.map(id => F.evidence.find(e => e.id === id)).filter(Boolean).map(evidenceRow).join('') || note('无直接证据', '此节点记录执行触发，而非研究证据。', 'neutral', 'info')}</div><h3 class="mt24 mb12" style="font-size:13px">因果与过程关系</h3><div class="stack">${EDGES.filter(e => e.includes(n.id)).map(([from, to]) => `<div class="small">${esc(NODES.find(x => x.id === from).title)} ${ic('arrow', 12)} ${esc(NODES.find(x => x.id === to).title)}</div>`).join('')}</div></div><div class="drawer-footer">${btn('定位当前节点', 'locate-node', 'soft w100', 'target', `data-node="${n.id}"`)}</div></aside>`; }
function dshPage(tab = 'trace') { const r = currentRun() || S.runs[0], log = F.logs.find(l => l.id === (params.get('log') || 'log-03')) || F.logs[0], level = params.get('level') || 'all', component = params.get('component') || 'all', q = params.get('q') || ''; const success = F.logs.filter(l => l.status === '成功').length, fail = F.logs.filter(l => l.level === 'ERROR').length; return `<div class="dsh-notice"><span class="row gap6">${ic('warning', 18)} 内部技术排查 · DSH 不产生第二份用户交易结论</span>${plainLink('返回来源报告', returnHref(), 'btn sm', 'back', 'id="dsh-return"')}</div>` + hero('DSH Web · 技术轨迹', '在当前 Run 上检查 Session、工具事件和子任务；只展示脱敏后的结构化摘要。', 'Internal Console') + `<div class="summary-grid"><div class="panel summary-card">${tile('file')}<div><h3>当前 Run</h3><strong style="font-size:15px">${esc(r.id)}</strong><p>Crypto Macro Trader</p></div></div><div class="panel summary-card">${tile('activity', 'green')}<div><h3>示例事件</h3><strong>${F.logs.length} 条</strong><p>${success} 条明确成功</p></div></div><div class="panel summary-card">${tile('warning', 'red')}<div><h3>错误事件</h3><strong>${fail} 条</strong><p>不会把未知结果当成功</p></div></div><div class="panel summary-card">${tile('heart', 'purple')}<div><h3>最近活动（演示）</h3><strong>13:42:10</strong><p>不是实时探测结果</p></div></div></div><div class="panel mb16"><div class="tab-row">${[['conversation', '对话摘要'], ['research', '主动研究'], ['trace', '技术轨迹'], ['report', '原生报告']].map(([v, t]) => `<a class="${tab === v ? 'active' : ''}" href="${esc(href(v === 'trace' ? 'dsh' : 'dsh-' + v, { run: r.id, back: params.get('back') || href('technical', { run: r.id }, false) }, false))}">${t}</a>`).join('')}</div></div>${tab === 'trace' ? `<div class="toolbar"><div class="search-field">${ic('search', 17)}<input id="log-search" name="logSearch" aria-label="搜索技术轨迹" placeholder="搜索事件、工具或内容…" autocomplete="off" value="${esc(q)}"/></div><label>级别<select id="log-level" name="level">${['all', 'INFO', 'WARN', 'ERROR'].map(v => `<option value="${v}" ${v === level ? 'selected' : ''}>${v === 'all' ? '全部级别' : v}</option>`).join('')}</select></label><label>组件<select id="log-component" name="component">${['all', ...new Set(F.logs.map(l => l.component))].map(v => `<option value="${v}" ${v === component ? 'selected' : ''}>${v === 'all' ? '全部组件' : v}</option>`).join('')}</select></label>${btn('重置', 'reset-logs', 'ghost', 'refresh')}</div><div class="console-layout"><div class="panel"><div class="panel-head"><h3>运行轨迹列表 <span class="small" id="log-count">${F.logs.length} 条</span></h3>${badge('固定演示数据', 'blue')}</div><div class="table-scroll"><table class="data-table log-table"><thead><tr><th>时间</th><th>级别</th><th>事件 / 内容</th><th>组件</th><th>耗时</th><th>结果</th></tr></thead><tbody id="log-table">${F.logs.map(l => `<tr class="${l.id === log.id ? 'selected' : ''}" data-level="${l.level}" data-component="${l.component}" data-search="${esc((l.type + ' ' + l.summary + ' ' + l.component).toLowerCase())}"><td>${l.time}</td><td>${badge(l.level, l.level === 'ERROR' ? 'red' : l.level === 'WARN' ? 'amber' : 'blue')}</td><td><button data-action="select-log" data-log="${l.id}" id="select-${l.id}"><b>${esc(l.type)}</b><br/>${esc(l.summary)}</button></td><td>${badge(l.component, 'blue')}</td><td>${l.latency}</td><td>${badge(l.status, l.status === '成功' ? 'green' : l.status === '失败' ? 'red' : 'amber')}</td></tr>`).join('')}</tbody></table></div><div id="log-empty" hidden>${empty('没有匹配的事件', '调整筛选或重置后查看完整示例轨迹。', btn('重置筛选', 'reset-logs', 'soft'), 'search', true)}</div><div class="table-footer">结构化运行事件，不包含 Session JSONL 原文、API 密钥或模型私有逐字思维链。</div></div><aside class="panel log-inspector"><div class="row between mb16"><h3>事件详情</h3>${btn('复制摘要', 'copy-log', 'ghost sm', 'copy')}</div><div class="row wrap mb16">${badge(log.type, 'blue')}${badge(log.status, log.status === '成功' ? 'green' : log.status === '失败' ? 'red' : 'amber')}</div><p class="secondary" style="font-size:13px">${esc(log.summary)}</p><hr/><dl class="kv" style="grid-template-columns:76px 1fr"><dt>Run ID</dt><dd class="mono">${esc(r.id)}</dd><dt>Session</dt><dd class="mono">demo-session-${esc(r.id)}</dd><dt>级别</dt><dd>${esc(log.level)}</dd><dt>组件</dt><dd>${esc(log.component)}</dd><dt>耗时</dt><dd>${log.latency}</dd><dt>Tokens</dt><dd>${log.tokens}（未提供不填 0）</dd><dt>事件时间</dt><dd>${log.time}（演示）</dd></dl><h3 class="mt24 mb12" style="font-size:13px">脱敏结构化摘要</h3><pre class="log-code" id="log-summary">${esc(JSON.stringify({ event_id: log.id, run_id: r.id, type: log.type, status: log.status, summary: log.summary }, null, 2))}</pre><div class="mt16">${note('只读诊断边界', '不可将此记录直接当作 Hub Evidence。证据需经过研究账本规范化。', 'amber', 'lock')}</div></aside></div>` : tab === 'conversation' ? `<div class="panel pad"><h3>对话与运行摘要</h3><div class="stack mt16">${note('用户请求', esc(r.focus), 'blue', 'user')}${note('系统状态摘要', '任务已登记，按研究流程读取来源与证据；保留短周期行动限制和未授权状态。', 'neutral', 'activity')}${note('不展示私有逐字思维链', '这里只记录任务输入、工具结果摘要及公开执行状态。', 'amber', 'shield')}</div></div>` : tab === 'research' ? `<div class="panel"><div class="panel-head"><h3>子任务与研究步骤</h3>${badge('3 个演示步骤', 'blue')}</div><div class="table-scroll"><table class="data-table"><thead><tr><th>步骤</th><th>角色</th><th>结果</th><th>查看</th></tr></thead><tbody>${[['来源读取', 'source_reader', '部分失败', 'log-03'], ['证据关联', 'researcher', '成功', 'log-04'], ['独立复核', 'reviewer', '待核验', 'log-05']].map(([t, owner, status, id]) => `<tr><td>${t}</td><td>${owner}</td><td>${badge(status, status === '成功' ? 'green' : 'amber')}</td><td>${a('查看事件', 'dsh', 'text', 'arrow', { log: id, back: params.get('back') }, false)}</td></tr>`).join('')}</tbody></table></div></div>` : `<div class="panel pad"><h3>DSH 原生报告摘要</h3><p class="secondary mt12">技术原生记录不代替 Hub 用户报告，也不产生第二份交易方向或授权。</p><div class="mt16">${note('当前运行摘要', '输出已归档至 Hub 查询视图；研究仅供参考，执行未授权。', 'neutral', 'file')}</div><div class="row mt24">${a('返回 Hub 用户报告', 'report', 'primary', 'file', { run: r.id })}${a('查看 Hub 审计详情', 'technical', '', 'review', { run: r.id })}</div></div>`}`; }
function sourcesPage() { const h = params.get('health') || 'online'; return backline('返回来源页面') + title('服务与数据源', '服务是否在线、来源是否可用和报告是否完整是三个不同状态。') + `<div class="three-col"><div class="panel pad"><h3 class="row mb16">${tile('activity', '', 'sm')} Research worker</h3>${badge(h === 'unknown' ? '未确认在线' : '在线', h === 'unknown' ? 'amber' : 'green')}<p class="small mt12">${h === 'unknown' ? '没有可验证的心跳，不能渲染为绿色健康。' : '演示心跳 13:42:10；非当前真实服务探测。'}</p></div><div class="panel pad"><h3 class="row mb16">${tile('globe', 'amber', 'sm')} 来源健康</h3>${badge('BLS 来源降级样例', 'amber')}<p class="small mt12">交接稿第 13 页记录过 403；这是历史交接快照，不代表现在仍失败。</p></div><div class="panel pad"><h3 class="row mb16">${tile('file', 'purple', 'sm')} 研究结果</h3>${badge('仅供研究', 'blue')}<p class="small mt12">结果完整性与权限需要单独读取报告字段，不能由 worker 在线推断。</p></div></div><div class="panel mt16"><div class="table-scroll"><table class="data-table"><thead><tr><th>资源</th><th>状态</th><th>影响范围</th><th>最后成功</th><th>下一次检查</th></tr></thead><tbody><tr><td>Research worker</td><td>${badge(h === 'unknown' ? 'unknown' : 'online', h === 'unknown' ? 'amber' : 'green')}</td><td>新任务调度与状态刷新</td><td>${h === 'unknown' ? '未确认' : '13:42:10（演示）'}</td><td>30 秒后（演示）</td></tr><tr><td>BLS 来源（交接快照）</td><td>${badge('degraded', 'amber')}</td><td>历史发布与来源覆盖，不填补实际值</td><td>未提供</td><td>未提供</td></tr><tr><td>宏观事件历史账本</td><td>${badge('empty')}</td><td>尚无已发布历史数据</td><td>未提供</td><td>等待采集</td></tr></tbody></table></div></div><div class="mt16">${note('错误反馈要求', '真实故障需显示 error code、origin、retryability、影响范围与最后更新时间。未提供的时间不填“刚刚”。', 'blue', 'info')}</div>`; }
function statePage() { const colors = [['品牌', '#2563eb'], ['背景', '#f3f8ff'], ['主文字', '#122247'], ['次文字', '#516585'], ['成功', '#087a55'], ['警告', '#9c5907'], ['错误', '#bc3049'], ['审计', '#703cd2']]; return backline('返回研究工作台') + hero('Design System · 设计系统', '对齐最新蓝白视觉，保留可实现的组件、状态与业务边界。', 'Decision Hub / v2.1') + `<div class="panel pad mb16"><h2 class="mb16">颜色与语义</h2><div class="swatches">${colors.map(([t, c]) => `<div><div class="swatch" style="background:${c};border:1px solid #dbe4f2"></div><b style="font-size:11px">${t}</b><small style="display:block">${c}</small></div>`).join('')}</div></div><div class="two-col mb16"><div class="panel pad"><h2 class="mb16">字体与层级</h2><h1 style="font-size:28px">研究结论先于过程</h1><h2 class="mt12">19 px / 区块标题</h2><h3 class="mt12">15 px / 模块标题</h3><p class="mt12">14 px / 正文；12 px / 辅助信息</p><p class="small mt8">使用系统中文字体，不打包或分发字体文件。</p></div><div class="panel pad"><h2 class="mb16">控件与反馈</h2><div class="row wrap mb16">${a('开始分析', 'compose', 'primary', 'play')}${a('查看证据', 'evidence', '', 'file', { evidence: 'EV-001' })}${btn('危险操作示例', 'danger-demo', 'danger', 'warning')}<button class="btn" disabled>条件未满足</button></div><label class="form-field">输入框示例<input name="example" aria-label="设计系统输入示例" autocomplete="off" placeholder="输入研究关注点…"/></label><div class="row wrap mt16">${stateBadge('completed')}${stateBadge('queued')}${stateBadge('researching')}${stateBadge('failed')}${badge('unknown', 'amber')}${badge('未校准', 'amber')}${badge('未授权', 'amber', 'lock')}</div></div></div><div class="panel pad mb16"><h2 class="mb16">可区分的业务状态</h2><div class="state-grid">${[['loading', '读取中', '指出正在读取的资源，而非整页空白。', 'blue', 'clock'], ['research_only', '仅供研究', '方向结论不是交易执行授权。', 'amber', 'lock'], ['no_trade', '暂不交易', '已经分析；当前不满足行动条件。', '', 'minus'], ['uncalibrated', '概率未校准', '不显示伪造置信度或成功率。', 'amber', 'info'], ['degraded', '来源降级', '指出来源、影响范围、重试性。', 'amber', 'warning'], ['synthesis_incomplete', '结论不完整', '研究结束不等于论证完整。', 'red', 'file']].map(([code, t, d, c, i]) => `<div class="state-card">${badge(code, c, i)}<h3>${t}</h3><p class="small">${d}</p></div>`).join('')}</div></div><div class="panel pad"><div class="row between mb16"><h2>64 枚线性图标</h2><span class="small">24 px 网格 / 1.7 px 描边 / SVG 路径</span></div><div class="icon-grid">${Object.keys(I).map(k => `<div class="icon-sample">${ic(k, 24)}<small>${k}</small></div>`).join('')}</div></div>`; }
function aboutBody() { return `<div class="stack">${note('交付内容', '可点击 HTML、可编辑 CSS / JavaScript、逐页 PNG、评审 PDF、图标与装饰素材、业务状态与接口说明。', 'blue', 'file')}<p>视觉基于本会话最新蓝白设计图；业务语义以原始 13 页交接稿为依据。</p><p class="small">本原型没有连接真实 API，不执行上传、解析、交易或权限变更。图片中的 62% 置信度、绿色校准正常、任意报价和日期没有作为事实沿用。</p><p class="small">图标为可编辑 SVG 几何路径；装饰背景从已提供设计图裁切；SVG 不是把 PNG 包进 SVG 文件。布局和文字由真实 HTML / CSS 构建。</p>${note('验收范围', '脚本测试导航、焦点、状态、上下文与响应式；没有把浏览器原型测试等同于生产上线验收。', 'amber', 'review')}</div>`; }
function catalogBody() { return `<p class="small mb16">设计评审目录，不属于业务主导航。38 个桌面页面与状态可直接进入；预览状态不会自动推进。</p><div class="catalog-grid">${PAGES.map(([id, t, desc], i) => `<a class="catalog-link" href="#/${id}?preview=1&run=${id === 'report-incomplete' ? 'demo-r05' : 'demo-r01'}${id === 'event' ? '&event=event-01' : ''}${id === 'graph-node' ? '&node=round-2' : ''}${id === 'evidence' ? '&evidence=EV-001' : ''}"><span class="catalog-num">${String(i + 1).padStart(2, '0')}</span><span>${t}<small style="display:block;font-size:10px">${esc(desc)}</small></span></a>`).join('')}</div>`; }
function modal(title, body, { drawer = false, wide = false, footer = '', back = '', kind = 'ephemeral' } = {}) { lastFocus = document.activeElement; overlayKind = kind; overlayBack = back; document.querySelector('#app').inert = true; document.body.style.overflow = 'hidden'; const close = kind === 'route' ? plainLink('关闭', back || returnHref(), 'btn ghost sm', 'close', 'id="overlay-close"') : btn('关闭', 'close-overlay', 'ghost sm', 'close', 'id="overlay-close"'); document.querySelector('#overlay').innerHTML = `<div class="overlay-backdrop" data-action="close-overlay"></div><section class="${drawer ? 'drawer' : 'modal' + (wide ? ' wide' : '')}" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><header class="${drawer ? 'drawer-header' : 'modal-header'}"><h2 id="dialog-title">${esc(title)}</h2>${close}</header><div class="${drawer ? 'drawer-content' : 'modal-content'}">${body}</div><footer class="${drawer ? 'drawer-footer' : 'modal-footer'}">${footer || `<span class="small">保持当前 Run 与来源页面</span>${close.replace('id="overlay-close"', '')}`}</footer></section>`; document.querySelector('#overlay button,#overlay a,#overlay input')?.focus({ preventScroll: true }); }
function clearOverlay() { document.querySelector('#overlay').innerHTML = ''; document.querySelector('#app').inert = false; document.body.style.overflow = ''; overlayKind = ''; overlayBack = ''; }
function closeOverlay() { const k = overlayKind, b = overlayBack, f = lastFocus; clearOverlay(); if (k === 'route')
    follow(b || returnHref());
else if (f?.isConnected)
    f.focus({ preventScroll: true }); }
function toast(t) { const el = document.querySelector('#toast'); el.textContent = t; el.hidden = false; clearTimeout(window.toastTimeout); window.toastTimeout = setTimeout(() => el.hidden = true, 4200); }
function applyPreview() { if (!params.has('preview'))
    return; const docScenes = scene.startsWith('document'); if (docScenes) {
    const id = 'preview-doc';
    const map = { 'document-uploading': 'uploading', 'document-parsing': 'parsing', 'document-quality': 'quality', 'document-review': 'review', 'document-confirm': 'review', 'document-confirmed': 'confirmed', 'document-ready': 'ready', 'document-linked': 'linked', 'document-failed': 'failed' };
    const decided = ['document-confirm', 'document-confirmed', 'document-ready', 'document-linked'].includes(scene);
    S.documents[id] = { ...clone(S.documents['demo-doc']), id, status: map[scene], version: scene === 'document-linked' || scene === 'document-ready' ? 2 : 1, decisions: decided ? { 'blk-01': 'accepted', 'blk-02': 'accepted', 'blk-03': 'rejected' } : {}, selected: 0, linked: scene === 'document-linked' ? DEFAULT_RUN : null };
    params.set('doc', id);
} if (scene.startsWith('graph')) {
    delete S.graph[params.get('run') || DEFAULT_RUN];
} }
function render({ preserve = false, previewInit = false } = {}) {
    if (rendering)
        return;
    rendering = true;
    const y = window.scrollY, focusID = document.activeElement?.id;
    clearOverlay();
    const parsed = parseHash();
    scene = parsed.scene;
    params = parsed.params;
    if (previewInit) {
        applyPreview();
        if (params.has('preview') && scene.startsWith('document'))
            history.replaceState(null, '', '#/' + scene + '?' + params.toString());
    }
    lastHash = location.hash || '#/home';
    let content = '', overlay = null;
    switch (scene) {
        case 'home':
            content = home();
            break;
        case 'home-loading':
            content = home('loading');
            break;
        case 'home-empty':
            content = home('empty');
            break;
        case 'home-unknown':
            content = home('unknown');
            break;
        case 'home-degraded':
            content = home('degraded');
            break;
        case 'compose':
            content = composePage();
            break;
        case 'submitting':
        case 'queued':
        case 'researching':
        case 'failed':
            content = composePage(scene);
            break;
        case 'runs':
            content = runsPage();
            break;
        case 'calendar-all':
            content = calendarPage();
            break;
        case 'calendar-upcoming':
            content = calendarPage('upcoming');
            break;
        case 'calendar-history':
            content = calendarPage('history');
            break;
        case 'event': {
            const old = params;
            const base = parseHash(returnHref());
            params = base.params;
            content = calendarPage(base.scene === 'calendar-history' ? 'history' : base.scene === 'calendar-upcoming' ? 'upcoming' : 'all');
            params = old;
            overlay = { title: '宏观事件详情', body: eventBody(), drawer: true, back: returnHref(), kind: 'route' };
            break;
        }
        case 'report':
            content = reportPage();
            break;
        case 'report-incomplete':
            content = reportPage(true);
            break;
        case 'technical':
            content = technicalPage();
            break;
        case 'evidence': {
            const base = parseHash(returnHref());
            content = base.scene.startsWith('graph') ? null : base.scene === 'technical' ? technicalPage() : reportPage();
            overlay = { title: '证据详情与来源', body: evidenceBody(), drawer: true, back: returnHref(), kind: 'route' };
            if (content === null) {
                const oldParams = params;
                params = base.params;
                document.querySelector('#app').innerHTML = graphPage(base.scene === 'graph-node');
                initGraph();
                params = oldParams;
            }
            break;
        }
        case 'graph':
        case 'graph-node':
            document.querySelector('#app').innerHTML = graphPage(scene === 'graph-node');
            content = null;
            break;
        case 'library':
            content = libraryPage();
            break;
        case 'upload':
            content = uploadPage();
            break;
        case 'document-review':
            content = reviewPage();
            break;
        case 'document-confirm':
            content = reviewPage();
            overlay = { title: '确认审核范围', body: confirmBody(), kind: 'route', back: params.get('back') || href('document-review', { doc: currentDoc()?.id }, false), footer: `${btn('继续核对', 'close-overlay', 'ghost')}<button id="confirm-final" class="btn primary" data-action="confirm-doc" disabled>确认并提交质量复检</button>` };
            break;
        case 'document-uploading':
        case 'document-parsing':
        case 'document-quality':
        case 'document-confirmed':
        case 'document-ready':
        case 'document-linked':
        case 'document-failed':
            content = documentPage(scene.replace('document-', ''));
            break;
        case 'sources':
            content = sourcesPage();
            break;
        case 'states':
            content = statePage();
            break;
        case 'dsh':
            content = dshPage();
            break;
        case 'dsh-conversation':
            content = dshPage('conversation');
            break;
        case 'dsh-research':
            content = dshPage('research');
            break;
        case 'dsh-report':
            content = dshPage('report');
            break;
        default: content = home();
    }
    if (content !== null)
        document.querySelector('#app').innerHTML = shell(content);
    document.title = (PAGES.find(p => p[0] === scene)?.[1] || 'Decision Hub') + ' · V2 交互原型';
    applyTableFilters();
    if (scene.startsWith('graph'))
        initGraph();
    if (overlay)
        modal(overlay.title, overlay.body, overlay);
    else {
        const pos = S.positions[lastHash];
        window.scrollTo({ top: preserve ? y : pos?.y || 0, behavior: 'instant' });
        const id = preserve ? focusID : pos?.focus;
        if (id)
            document.getElementById(id)?.focus({ preventScroll: true });
    }
    if (params.get('section')) {
        const el = document.getElementById(params.get('section'));
        if (el) {
            if (el.tagName === 'DETAILS')
                el.open = true;
            el.scrollIntoView({ block: 'start' });
        }
    }
    rendering = false;
    save();
}
function onRoute() { rememberPosition(); render({ previewInit: true }); }
function applyTableFilters() {
    if (scene === 'runs') {
        const q = (params.get('q') || '').toLowerCase(), st = params.get('status') || 'all', tr = params.get('trigger') || 'all';
        let n = 0;
        document.querySelectorAll('#run-table tr').forEach(el => { const yes = el.dataset.search.includes(q) && (st === 'all' || el.dataset.status === st) && (tr === 'all' || el.dataset.trigger === tr); el.hidden = !yes; if (yes)
            n++; });
        const count = document.getElementById('run-count'), emptyEl = document.getElementById('run-empty');
        if (count)
            count.textContent = n;
        if (emptyEl)
            emptyEl.hidden = n > 0;
    }
    if (scene.startsWith('calendar')) {
        const q = (params.get('q') || '').toLowerCase(), market = params.get('market') || 'all', impact = params.get('impact') || 'all';
        let n = 0;
        document.querySelectorAll('#event-table tr').forEach(el => { const yes = el.dataset.search.includes(q) && (market === 'all' || el.dataset.market === market) && (impact === 'all' || el.dataset.impact === impact); el.hidden = !yes; if (yes)
            n++; });
        const count = document.getElementById('event-count'), emptyEl = document.getElementById('event-empty');
        if (count)
            count.textContent = n;
        if (emptyEl)
            emptyEl.hidden = n > 0;
    }
    if (scene === 'dsh') {
        const q = (params.get('q') || '').toLowerCase(), level = params.get('level') || 'all', component = params.get('component') || 'all';
        let n = 0;
        document.querySelectorAll('#log-table tr').forEach(el => { const yes = el.dataset.search.includes(q) && (level === 'all' || el.dataset.level === level) && (component === 'all' || el.dataset.component === component); el.hidden = !yes; if (yes)
            n++; });
        const count = document.getElementById('log-count'), emptyEl = document.getElementById('log-empty');
        if (count)
            count.textContent = n + ' 条';
        if (emptyEl)
            emptyEl.hidden = n > 0;
    }
}
function fitGraph() { const c = document.getElementById('graph-canvas'); if (!c)
    return; const g = graphData(); g.z = Math.max(.16, Math.min((c.clientWidth - 48) / 1170, (c.clientHeight - 105) / 780, 1.2)); g.x = (c.clientWidth - 1170 * g.z) / 2; g.y = Math.max(18, (c.clientHeight - 780 * g.z) / 2 - 15); updateGraphTransform(); }
function updateGraphTransform() { const c = document.getElementById('graph-canvas'), el = document.getElementById('graph-stage'); if (!c || !el)
    return; const g = graphData(); el.style.transform = `translate(${g.x}px,${g.y}px) scale(${g.z})`; const z = document.getElementById('zoom-label'); if (z)
    z.textContent = Math.round(g.z * 100) + '%'; const map = document.getElementById('minimap-viewport'); if (map) {
    map.setAttribute('x', Math.max(0, -g.x / g.z));
    map.setAttribute('y', Math.max(0, -g.y / g.z));
    map.setAttribute('width', Math.min(1170, c.clientWidth / g.z));
    map.setAttribute('height', Math.min(780, c.clientHeight / g.z));
} save(); }
function zoomGraph(f, px = null, py = null) { const c = document.getElementById('graph-canvas'); if (!c)
    return; const g = graphData(), old = g.z; g.z = Math.max(.16, Math.min(2.4, g.z * f)); const x = px ?? c.clientWidth / 2, y = py ?? c.clientHeight / 2; g.x = x - (x - g.x) * g.z / old; g.y = y - (y - g.y) * g.z / old; updateGraphTransform(); }
function updateGraphVisibility() { const g = graphData(), visible = new Set(); NODES.forEach(n => { const yes = (g.filter === 'all' || n.type === g.filter) && (!g.collapsed || n.type !== 'tool') && (n.title + ' ' + n.desc + ' ' + n.evidence.join(' ')).toLowerCase().includes(g.q.toLowerCase()); const el = document.getElementById('node-' + n.id); if (el)
    el.classList.toggle('hidden-node', !yes); if (yes)
    visible.add(n.id); }); document.querySelectorAll('[data-group]').forEach(el => el.hidden = !g.groups); const collapsed = g.collapsed && g.filter === 'all' && !g.q; const ce = document.getElementById('collapsed-tools'); if (ce)
    ce.style.display = collapsed ? '' : 'none'; let edges = EDGES.filter(([a, b]) => visible.has(a) && visible.has(b)); const nodes = new Map(NODES.map(n => [n.id, n])); if (collapsed) {
    nodes.set('collapsed-tools', { x: 255, y: 252 });
    edges.push(['round-1', 'collapsed-tools'], ['collapsed-tools', 'evidence'], ['collapsed-tools', 'evidence-2'], ['collapsed-tools', 'evidence-3']);
} const paths = edges.map(([from, to]) => { const a = nodes.get(from), b = nodes.get(to); const x1 = a.x + 91.5, y1 = a.y + 78, x2 = b.x + 91.5, y2 = b.y, m = (y1 + y2) / 2; return `<path d="M${x1} ${y1}V${m}H${x2}V${y2}" fill="none" stroke="${from === 'counter' || to === 'counter' ? '#d58097' : '#849ec3'}" stroke-width="1.6" ${from === 'counter' || to === 'counter' ? 'stroke-dasharray="5 4"' : ''} marker-end="url(#arrowhead)"/>`; }).join(''); const p = document.getElementById('graph-edge-paths'); if (p)
    p.innerHTML = paths; const emptyEl = document.getElementById('graph-empty'); if (emptyEl)
    emptyEl.hidden = visible.size > 0; save(); }
function locateNode(id) { const n = NODES.find(n => n.id === id), c = document.getElementById('graph-canvas'); if (!n || !c)
    return; const g = graphData(); g.z = Math.max(g.z, .8); g.x = c.clientWidth / 2 - (n.x + 91.5) * g.z; g.y = c.clientHeight / 2 - (n.y + 39) * g.z; updateGraphTransform(); }
function initGraph() { const c = document.getElementById('graph-canvas'); if (!c)
    return; const g = graphData(); if (params.has('filter'))
    g.filter = params.get('filter'); if (params.has('q'))
    g.q = params.get('q'); if (!g.z)
    fitGraph();
else
    updateGraphTransform(); updateGraphVisibility(); let drag = null; c.addEventListener('pointerdown', e => { if (e.target.closest('button,a,input,select,.graph-map,.graph-list'))
    return; drag = { x: e.clientX, y: e.clientY, gx: g.x, gy: g.y }; c.classList.add('dragging'); c.setPointerCapture(e.pointerId); }); c.addEventListener('pointermove', e => { if (!drag)
    return; g.x = drag.gx + e.clientX - drag.x; g.y = drag.gy + e.clientY - drag.y; updateGraphTransform(); }); c.addEventListener('pointerup', () => { drag = null; c.classList.remove('dragging'); }); c.addEventListener('pointercancel', () => { drag = null; c.classList.remove('dragging'); }); c.addEventListener('wheel', e => { if (e.target.closest('.graph-list'))
    return; e.preventDefault(); const box = c.getBoundingClientRect(); zoomGraph(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - box.left, e.clientY - box.top); }, { passive: false }); c.addEventListener('keydown', e => { if (e.target.closest('input,select'))
    return; if (e.key === '+' || e.key === '=') {
    e.preventDefault();
    zoomGraph(1.15);
}
else if (e.key === '-') {
    e.preventDefault();
    zoomGraph(1 / 1.15);
}
else if (e.key.toLowerCase() === 'f') {
    e.preventDefault();
    fitGraph();
}
else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
    e.preventDefault();
    g.x += e.key === 'ArrowRight' ? 32 : e.key === 'ArrowLeft' ? -32 : 0;
    g.y += e.key === 'ArrowDown' ? 32 : e.key === 'ArrowUp' ? -32 : 0;
    updateGraphTransform();
} }); const m = document.getElementById('graph-minimap'); if (m) {
    m.addEventListener('click', e => { const b = m.getBoundingClientRect(); g.x = c.clientWidth / 2 - ((e.clientX - b.left) / b.width * 1170) * g.z; g.y = c.clientHeight / 2 - ((e.clientY - b.top) / b.height * 780) * g.z; updateGraphTransform(); });
    m.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fitGraph();
    } });
} ; }
function submitResearch({ retry = false, docID = null } = {}) {
    if (scene === 'submitting')
        return null;
    const d = docID ? S.documents[docID] : params.get('doc') ? currentDoc() : null;
    if (d && !['ready', 'linked'].includes(d.status)) {
        toast('资料尚未通过质量门，不能接入研究。');
        return null;
    }
    const from = currentRun();
    const focus = (retry ? (S.draft || from?.focus || '') : S.draft).trim() || '获取最新操作建议';
    const parent = retry ? from?.id : params.get('parent') || null;
    const key = [focus, d?.id || '', d?.version || '', parent || '', Math.floor(Date.now() / 60000)].join('|');
    const exists = S.idempotency[key];
    if (exists) {
        const existing = S.runs.find(r => r.id === exists);
        if (existing) {
            toast('同一研究意图已创建，打开已有任务。');
            go(runTarget(existing), { run: existing.id, doc: d?.id || null }, false);
            return existing.id;
        }
    }
    const id = 'local-r' + String(++S.seq).padStart(3, '0'), r = { id, title: focus === '获取最新操作建议' ? 'BTC 最新操作建议' : focus.length > 22 ? focus.slice(0, 22) + '…' : focus, focus, status: 'submitting', trigger: retry ? '重试' : '人工提交', at: new Date().toISOString(), evidence: 0, required: 12, sources: 0, parent, kind: 'research_only', doc: d?.id || null, simulated: true };
    S.runs.unshift(r);
    S.idempotency[key] = id;
    if (d) {
        d.linked = id;
        d.status = 'linked';
    }
    save();
    go('submitting', { run: id, doc: d?.id || null, back: docID ? href('document-linked', { doc: docID, run: id }, false) : href('runs', {}, false) }, false);
    const advance = (status, ms) => transitionTimers.push(setTimeout(() => { const x = S.runs.find(t => t.id === id); if (!x)
        return; x.status = status; if (status === 'researching')
        x.evidence = 3; if (status === 'completed') {
        x.evidence = 6;
        x.sources = 2;
    } save(); if (currentRun()?.id === id && ['submitting', 'queued', 'researching'].includes(scene) && !params.has('preview')) {
        go(status === 'completed' ? 'report' : status, { run: id, doc: d?.id || null, back: params.get('back') || '#/runs' }, false);
    } }, ms));
    advance('queued', 750);
    advance('researching', 2200);
    advance('completed', 7000);
    return id;
}
function beginDoc({ sample = false } = {}) { const titleEl = document.getElementById('doc-title'); if (!sample) {
    let err = false;
    if (!uploadFile) {
        document.getElementById('file-error').textContent = '请选择受支持的文件。';
        document.getElementById('file-input').focus();
        err = true;
    }
    if (!titleEl?.value.trim()) {
        document.getElementById('title-error').textContent = '请输入资料标题。';
        if (!err)
            titleEl?.focus();
        err = true;
    }
    if (err)
        return;
} const id = 'local-doc' + String(++S.seq).padStart(3, '0'); const titleText = sample ? '研究说明_演示' : titleEl.value.trim(); const d = { id, title: titleText, filename: sample ? '研究说明_演示.txt' : uploadFile.name, market: sample ? 'BTC / 宏观' : document.getElementById('doc-market').value, symbol: sample ? 'BTC' : document.getElementById('doc-symbol').value, privacy: sample ? 'private' : document.querySelector('input[name=privacy]:checked').value, status: 'uploading', version: 1, decisions: {}, selected: 0, linked: null }; S.documents[id] = d; save(); go('document-uploading', { doc: id, back: href('library', {}, false) }, false); transitionTimers.push(setTimeout(() => { d.status = 'parsing'; save(); if (currentDoc()?.id === id && scene === 'document-uploading')
    go('document-parsing', { doc: id, back: params.get('back') }, false); }, 1000)); transitionTimers.push(setTimeout(() => { d.status = 'review'; save(); if (currentDoc()?.id === id && ['document-uploading', 'document-parsing'].includes(scene))
    go('document-review', { doc: id, back: params.get('back') }, false); }, 3000)); }
function selectFile(file) { const error = document.getElementById('file-error'); if (!file)
    return; const ext = file.name.split('.').pop().toLowerCase(); if (!['pdf', 'png', 'jpg', 'jpeg', 'webp', 'txt', 'md', 'html'].includes(ext)) {
    uploadFile = null;
    error.textContent = '不支持此文件格式。请使用 PDF、图片或文本资料。';
    return;
} if (file.size > 50 * 1024 * 1024) {
    uploadFile = null;
    error.textContent = '超过本原型 50 MB 的演示限制。';
    return;
} uploadFile = { name: file.name, size: file.size }; error.textContent = ''; document.getElementById('file-name').textContent = file.name; const t = document.getElementById('doc-title'); if (t && !t.value)
    t.value = file.name.replace(/\.[^.]+$/, ''); }
async function copyText(text) { try {
    await navigator.clipboard.writeText(text);
    toast('已复制脱敏摘要。');
}
catch {
    modal('复制摘要', `<label class="form-field">当前浏览器不允许自动复制，请手动复制：<textarea rows="10" readonly>${esc(text)}</textarea></label>`);
} }
function act(action, el) { switch (action) {
    case 'catalog':
        modal('页面目录', catalogBody(), { wide: true });
        break;
    case 'about':
        modal('交付范围与数据说明', aboutBody(), { wide: true });
        break;
    case 'mobile-menu':
        document.querySelector('.sidebar')?.classList.toggle('open');
        break;
    case 'close-overlay':
        closeOverlay();
        break;
    case 'focus-chip': {
        const v = el.dataset.value || '';
        S.draft = S.draft ? S.draft + '；' + v : '重点关注' + v;
        save();
        const input = document.getElementById('research-focus') || document.getElementById('home-focus');
        if (input)
            input.value = S.draft;
        const c = document.getElementById('char-count');
        if (c)
            c.textContent = Array.from(S.draft).length;
        break;
    }
    case 'retry-research':
        submitResearch({ retry: true });
        break;
    case 'rerun': {
        const r = currentRun();
        if (!r)
            return;
        S.draft = r.focus || '';
        save();
        go('compose', { parent: r.id, run: r.id, doc: r.doc || null });
        break;
    }
    case 'reset-runs':
        ['q', 'status', 'trigger'].forEach(k => params.delete(k));
        history.replaceState(null, '', '#/runs?' + params.toString());
        render({ preserve: true });
        break;
    case 'reset-events':
        ['q', 'market', 'impact'].forEach(k => params.delete(k));
        history.replaceState(null, '', '#/' + scene + '?' + params.toString());
        render({ preserve: true });
        break;
    case 'reset-logs':
        ['q', 'level', 'component'].forEach(k => params.delete(k));
        history.replaceState(null, '', '#/dsh?' + params.toString());
        render({ preserve: true });
        break;
    case 'select-log':
        updateQuery('log', el.dataset.log);
        render({ preserve: true });
        break;
    case 'copy-log':
        copyText(document.getElementById('log-summary')?.textContent || '');
        break;
    case 'sample-upload':
        beginDoc({ sample: true });
        break;
    case 'save-doc':
        save();
        toast('已在本地保存审核草稿，未写入业务后端。');
        break;
    case 'accept-diff':
    case 'reject-diff': {
        const d = currentDoc(), id = el.dataset.block, x = F.diffs.find(t => t.id === id);
        if (!d || !x)
            return;
        if (action === 'accept-diff' && x.risk) {
            toast('拒绝改变事实与执行权限的高风险建议。');
            return;
        }
        d.decisions[id] = action === 'accept-diff' ? 'accepted' : 'rejected';
        d.selected = F.diffs.findIndex(t => t.id === id);
        d.status = 'review';
        save();
        render({ preserve: true });
        break;
    }
    case 'open-confirm': {
        const d = currentDoc();
        if (!d || !reviewReady(d)) {
            toast('请先处理全部差异。');
            return;
        }
        go('document-confirm', { doc: d.id });
        break;
    }
    case 'confirm-doc': {
        const d = currentDoc();
        if (!d || !reviewReady(d) || !document.getElementById('review-ack')?.checked)
            return;
        d.version++;
        d.status = 'confirmed';
        save();
        clearOverlay();
        go('document-confirmed', { doc: d.id, back: href('library', {}, false) }, false);
        transitionTimers.push(setTimeout(() => { d.status = 'ready'; save(); if (currentDoc()?.id === d.id && scene === 'document-confirmed')
            go('document-ready', { doc: d.id, back: params.get('back') }, false); }, 1500));
        break;
    }
    case 'retry-doc': {
        const d = currentDoc();
        if (!d)
            return;
        d.status = 'parsing';
        save();
        go('document-parsing', { doc: d.id });
        transitionTimers.push(setTimeout(() => { d.status = 'review'; save(); if (currentDoc()?.id === d.id && scene === 'document-parsing')
            go('document-review', { doc: d.id, back: href('library', {}, false) }, false); }, 1500));
        break;
    }
    case 'link-doc': {
        const d = currentDoc();
        if (!d || !['ready', 'linked'].includes(d.status)) {
            toast('资料尚未通过质量门。');
            return;
        }
        S.draft = '';
        submitResearch({ docID: d.id });
        break;
    }
    case 'graph-zoom-in':
        zoomGraph(1.2);
        break;
    case 'graph-zoom-out':
        zoomGraph(1 / 1.2);
        break;
    case 'graph-fit':
        fitGraph();
        break;
    case 'graph-reset': {
        const g = graphData();
        g.filter = 'all';
        g.q = '';
        updateQuery('q', null);
        updateQuery('filter', null);
        document.getElementById('graph-search').value = '';
        document.getElementById('graph-filter').value = 'all';
        updateGraphVisibility();
        break;
    }
    case 'graph-collapse': {
        const g = graphData();
        g.collapsed = !g.collapsed;
        updateGraphVisibility();
        el.querySelector('span').textContent = g.collapsed ? '展开工具组' : '折叠工具组';
        break;
    }
    case 'graph-list': {
        const g = graphData();
        g.list = !g.list;
        document.getElementById('graph-list-panel').hidden = !g.list;
        el.querySelector('span').textContent = g.list ? '图形视图' : '列表视图';
        save();
        break;
    }
    case 'select-node':
        go('graph-node', { node: el.dataset.node, back: params.get('back') || href('report', {}, false) }, false);
        break;
    case 'close-node':
        go('graph', { back: params.get('back') || href('report', {}, false) }, false);
        break;
    case 'locate-node':
        locateNode(el.dataset.node);
        break;
    case 'danger-demo':
        modal('危险操作必须确认', note('这是控件演示', '生产中的删除或覆盖操作必须确认，或者提供可撤销窗口。本原型不会删除任何业务资料。', 'red', 'warning'), { footer: btn('取消', 'close-overlay', 'ghost') + btn('确认演示', 'close-overlay', 'danger') });
        break;
    default: toast('该控件没有已定义的演示操作。');
} }
document.addEventListener('click', e => { const scrollLink = e.target.closest('[data-scroll]'); if (scrollLink) {
    e.preventDefault();
    const target = document.getElementById(scrollLink.dataset.scroll);
    if (target) {
        if (target.tagName === 'DETAILS')
            target.open = true;
        target.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion:reduce)').matches ? 'instant' : 'smooth' });
    }
    return;
} const el = e.target.closest('[data-action]'); if (el && !el.disabled) {
    e.preventDefault();
    act(el.dataset.action, el);
    return;
} const link = e.target.closest('a[href]'); if (link && link.getAttribute('href')?.startsWith('#/')) {
    rememberPosition();
    if (overlayKind === 'ephemeral')
        clearOverlay();
    document.querySelector('.sidebar')?.classList.remove('open');
    if (link.getAttribute('href') === location.hash) {
        e.preventDefault();
        clearOverlay();
        render({ preserve: true });
    }
} });
document.addEventListener('submit', e => { if (e.target.id === 'research-form') {
    e.preventDefault();
    if (['queued', 'researching'].includes(scene))
        return;
    S.draft = document.getElementById('research-focus').value;
    save();
    submitResearch();
}
else if (e.target.id === 'home-form') {
    e.preventDefault();
    S.draft = document.getElementById('home-focus').value;
    save();
    go('compose');
}
else if (e.target.id === 'upload-form') {
    e.preventDefault();
    beginDoc();
} });
document.addEventListener('input', e => { const id = e.target.id; if (id === 'research-focus' || id === 'home-focus') {
    S.draft = e.target.value;
    save();
    const c = document.getElementById('char-count');
    if (c)
        c.textContent = Array.from(S.draft).length;
}
else if (['run-search', 'event-search', 'log-search'].includes(id)) {
    updateQuery('q', e.target.value);
    applyTableFilters();
}
else if (id === 'graph-search') {
    graphData().q = e.target.value;
    updateQuery('q', e.target.value);
    updateGraphVisibility();
} });
document.addEventListener('change', e => { const id = e.target.id; if (id === 'run-status')
    updateQuery('status', e.target.value); if (id === 'run-trigger')
    updateQuery('trigger', e.target.value); if (id === 'event-market')
    updateQuery('market', e.target.value); if (id === 'event-impact')
    updateQuery('impact', e.target.value); if (id === 'log-level')
    updateQuery('level', e.target.value); if (id === 'log-component')
    updateQuery('component', e.target.value); if (['run-status', 'run-trigger', 'event-market', 'event-impact', 'log-level', 'log-component'].includes(id))
    applyTableFilters(); if (id === 'file-input')
    selectFile(e.target.files[0]); if (id === 'review-ack') {
    const b = document.getElementById('confirm-final');
    if (b)
        b.disabled = !(e.target.checked && reviewReady(currentDoc()));
} if (id === 'graph-filter') {
    graphData().filter = e.target.value;
    updateQuery('filter', e.target.value);
    updateGraphVisibility();
} if (id === 'graph-groups') {
    graphData().groups = e.target.checked;
    updateGraphVisibility();
} });
document.addEventListener('dragover', e => { const d = e.target.closest('#dropzone'); if (d) {
    e.preventDefault();
    d.classList.add('dragging');
} });
document.addEventListener('dragleave', e => e.target.closest('#dropzone')?.classList.remove('dragging'));
document.addEventListener('drop', e => { const d = e.target.closest('#dropzone'); if (d) {
    e.preventDefault();
    d.classList.remove('dragging');
    selectFile(e.dataTransfer.files[0]);
} });
document.addEventListener('keydown', e => { const dialog = document.querySelector('#overlay [role=dialog]'); if (e.key === 'Escape') {
    if (dialog)
        closeOverlay();
    else if (scene === 'graph-node')
        act('close-node', {});
    else if (scene === 'graph')
        follow(returnHref());
    else
        document.querySelector('.sidebar')?.classList.remove('open');
} if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    modal('页面目录', catalogBody(), { wide: true });
} if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && scene === 'compose') {
    e.preventDefault();
    submitResearch();
} if (dialog && e.key === 'Tab') {
    const list = [...dialog.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),textarea,select,[tabindex="0"]')].filter(el => el.getClientRects().length);
    const first = list[0], last = list.at(-1);
    if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
    }
    else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
    }
} });
window.addEventListener('hashchange', () => render({ previewInit: true }));
window.addEventListener('resize', () => { if (scene.startsWith('graph'))
    fitGraph(); });
let scrollTimer;
window.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(() => { if (lastHash && !overlayKind) {
    S.positions[lastHash] = { y: window.scrollY, focus: document.activeElement?.id || null };
    save();
} }, 80); }, { passive: true });
window.addEventListener('beforeunload', save);
/* Test hooks expose local state only, never a backend or permissions bypass. */
window.DH_TEST = { getState: () => clone(S), getScene: () => scene, getParams: () => Object.fromEntries(params), reset: () => { transitionTimers.forEach(clearTimeout); transitionTimers = []; S = seed(); save(); location.hash = '#/home'; render(); }, setRunStatus: (id, status) => { const r = S.runs.find(r => r.id === id); if (r) {
        r.status = status;
        save();
    } }, getNodes: () => clone(NODES), getGraph: () => clone(graphData()), render: () => render({ preserve: true }) };
render({ previewInit: true });
