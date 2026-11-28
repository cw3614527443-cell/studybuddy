"""手机端页面（单文件，无外部依赖）。

放在单独的 .py 里而不是 assets/ 目录，是为了打包省事：字符串直接编译进 exe，
不用在 build.spec 里再挂一份 _MEIPASS 资源，也不会出现「路径对了但没打进去」。
图标是运行时从 assets/icon.png 现缩放的（见 core/remote.py 的 `_icon`）。

页面只做四件事：看状态、看日志、选课、下指令。手机上不该塞进桌面端那套
步骤条和设置项 —— 竖屏、单手、几个大按钮，够用就行。
"""

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#4C6FFF">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="StudyBuddy">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>StudyBuddy 遥控器</title>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; }
  body {
    background: #F4F6FB; color: #111827;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 15px; line-height: 1.5;
    padding: calc(env(safe-area-inset-top) + 10px) 14px calc(env(safe-area-inset-bottom) + 18px);
    max-width: 560px; margin: 0 auto;
  }
  .card {
    background: #fff; border-radius: 16px; padding: 14px;
    box-shadow: 0 1px 3px rgba(17,24,39,.06), 0 8px 24px rgba(17,24,39,.04);
    margin-bottom: 12px;
  }
  h1 { font-size: 17px; margin: 0 0 2px; letter-spacing: .2px; }
  .sub { color: #98A2B3; font-size: 12px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .grow { flex: 1; min-width: 0; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #C7CDD9; flex: none; }
  .dot.on { background: #0E9F6E; box-shadow: 0 0 0 4px rgba(14,159,110,.14); animation: pulse 2s infinite; }
  @keyframes pulse { 50% { box-shadow: 0 0 0 7px rgba(14,159,110,.05); } }
  .status-text { font-weight: 600; font-size: 15px; }
  .ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar { height: 8px; background: #EDF1FF; border-radius: 99px; overflow: hidden; margin-top: 10px; }
  .bar > i { display: block; height: 100%; width: 0; background: #4C6FFF; border-radius: 99px; transition: width .35s ease; }
  .bar.indet > i { width: 40%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -40%; } 100% { margin-left: 100%; } }
  .pct { color: #5B6478; font-size: 12px; margin-top: 6px; }
  .btns { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  button {
    font: inherit; border: 0; border-radius: 12px; padding: 13px 10px;
    background: #EDF1FF; color: #3B5BE0; font-weight: 600; cursor: pointer;
  }
  button:active { transform: scale(.985); }
  button:disabled { opacity: .45; }
  button.primary { background: #4C6FFF; color: #fff; }
  button.danger { background: #FDF1F1; color: #E5484D; }
  button.wide { grid-column: span 2; }
  .sect-title { font-size: 12px; color: #98A2B3; margin: 0 0 8px; letter-spacing: .4px; }
  .courses { max-height: 34vh; overflow: auto; margin: 0 -6px; }
  .course {
    display: flex; align-items: center; gap: 8px; padding: 10px 8px;
    border-radius: 10px; cursor: pointer;
  }
  .course + .course { border-top: 1px solid #F2F4F8; }
  .course.sel { background: #EDF1FF; }
  .course .nm { flex: 1; min-width: 0; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tag { font-size: 11px; padding: 2px 7px; border-radius: 99px; background: #F2F4F8; color: #5B6478; flex: none; }
  .tag.done { background: #DBEAFE; color: #1E3A8A; }
  .tag.ok { background: #E9F8F2; color: #0E9F6E; }
  .tag.unk { background: #FEF3C7; color: #B45309; }
  .cnt { font-size: 11px; color: #98A2B3; flex: none; }
  .logs { max-height: 38vh; overflow: auto; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11.5px; line-height: 1.65; }
  .logs div { padding: 1px 0; color: #374151; word-break: break-all; }
  .logs .t { color: #98A2B3; margin-right: 6px; }
  .logs .warn { color: #B45309; }
  .logs .error { color: #E5484D; }
  .logs .ok { color: #0E9F6E; }
  .shot { width: 100%; border-radius: 10px; border: 1px solid #E7EBF3; display: block; }
  #login { position: fixed; inset: 0; background: #F4F6FB; display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 40; }
  /* #login 用了 display:flex，会盖掉浏览器对 [hidden] 的默认 display:none —— 必须显式补一条，
     否则登录成功后登录层仍然固定盖在页面上（手机上表现为「连不上、一直让你输码」）。 */
  #login[hidden] { display: none; }
  #login .box { width: 100%; max-width: 340px; background: #fff; border-radius: 18px; padding: 22px; box-shadow: 0 12px 40px rgba(17,24,39,.10); }
  #login input {
    width: 100%; font: inherit; font-size: 26px; letter-spacing: 10px; text-align: center;
    padding: 12px 14px 12px 24px; border: 1px solid #DCE2EE; border-radius: 12px; outline: none;
    margin: 14px 0 10px; background: #FAFBFE;
  }
  #login input:focus { border-color: #4C6FFF; background: #fff; }
  #login button { width: 100%; padding: 13px; }
  .err { color: #E5484D; font-size: 13px; min-height: 18px; }
  .hint { color: #98A2B3; font-size: 12px; line-height: 1.6; }
  .toast {
    position: fixed; left: 50%; bottom: calc(env(safe-area-inset-bottom) + 22px); transform: translateX(-50%);
    background: rgba(17,24,39,.92); color: #fff; font-size: 13px; padding: 9px 16px;
    border-radius: 99px; opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 50;
    max-width: 86vw;
  }
  .toast.show { opacity: 1; }
  .foot { color: #98A2B3; font-size: 11px; text-align: center; margin-top: 4px; }
</style>
</head>
<body>

<div id="login">
  <div class="box">
    <h1>连接电脑</h1>
    <div class="hint">输入电脑上「手机远控」窗口里显示的 6 位配对码。</div>
    <input id="code" type="tel" inputmode="numeric" maxlength="6" placeholder="······" autocomplete="one-time-code">
    <div class="err" id="err"></div>
    <button class="primary" id="go">连 接</button>
  </div>
</div>

<div id="app" hidden>
  <div class="card">
    <div class="row">
      <span class="dot" id="dot"></span>
      <div class="grow">
        <div class="status-text ellipsis" id="status">空闲</div>
        <div class="sub" id="meta">—</div>
      </div>
    </div>
    <div class="bar" id="bar"><i id="barin"></i></div>
    <div class="pct" id="pct"></div>
  </div>

  <div class="card">
    <div class="sect-title">操作</div>
    <div class="btns">
      <button id="b-scan">扫描课程</button>
      <button id="b-read">读学时</button>
      <button class="primary wide" id="b-brush">开始刷选中的课</button>
      <button id="b-watch">刷学时 2 小时</button>
      <button class="danger" id="b-stop">停 止</button>
    </div>
  </div>

  <div class="card">
    <div class="sect-title">课程 <span id="cnum"></span></div>
    <div class="courses" id="courses"><div class="hint">还没有课程列表，点上面「扫描课程」。</div></div>
  </div>

  <div class="card">
    <div class="sect-title">当前画面</div>
    <img class="shot" id="shot" alt="" hidden>
    <div class="hint" id="shot-none">任务运行时这里会显示电脑上的截图。</div>
  </div>

  <div class="card">
    <div class="sect-title">日志</div>
    <div class="logs" id="logs"><div class="hint">暂无日志。</div></div>
  </div>

  <div class="foot" id="foot"></div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
let TOKEN = localStorage.getItem('sb_token') || '';
let SEL = '';
let lastLogKey = '';
let shotKey = '';
let timer = null;

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 1800);
}

function showLogin() {
  $('#login').hidden = false;
  $('#app').hidden = true;
  if (timer) { clearInterval(timer); timer = null; }
  localStorage.removeItem('sb_token');
  TOKEN = '';
}

function showApp() {
  $('#login').hidden = true;
  $('#app').hidden = false;
  if (!timer) timer = setInterval(poll, 1200);
  poll();
}

async function api(path, opt) {
  opt = opt || {};
  opt.headers = Object.assign({ 'Content-Type': 'application/json', 'X-SB-Token': TOKEN }, opt.headers || {});
  const r = await fetch(path, opt);
  if (r.status === 401) { showLogin(); throw new Error('未登录'); }
  const j = await r.json().catch(() => ({ ok: false, error: '电脑端没有正确响应' }));
  return j;
}

async function login() {
  const code = ($('#code').value || '').replace(/[^0-9]/g, '');
  if (code.length !== 6) { $('#err').textContent = '请输入 6 位数字'; return; }
  $('#go').disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code })
    });
    const j = await r.json();
    if (!j.ok) { $('#err').textContent = j.error || '连接失败'; return; }
    TOKEN = j.token;
    localStorage.setItem('sb_token', TOKEN);
    $('#err').textContent = '';
    showApp();
  } catch (e) {
    $('#err').textContent = '连不上电脑，确认在同一个 WiFi 下';
  } finally {
    $('#go').disabled = false;
  }
}

async function act(name, params) {
  try {
    const j = await api('/api/action', { method: 'POST', body: JSON.stringify({ name: name, params: params || {} }) });
    if (!j.ok) toast(j.error || '操作失败');
    else poll();
  } catch (e) { /* 401 已处理 */ }
}

const TAG_CLASS = { '刷完': 'done', '可刷': 'ok', '无课': '', '未确认': 'unk' };

function renderCourses(list) {
  const box = $('#courses');
  if (!list || !list.length) {
    box.innerHTML = '<div class="hint">还没有课程列表，点上面「扫描课程」。</div>';
    $('#cnum').textContent = '';
    return;
  }
  $('#cnum').textContent = '（' + list.length + '）';
  const frag = document.createDocumentFragment();
  list.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'course' + (c.name === SEL ? ' sel' : '');
    const nm = document.createElement('div');
    nm.className = 'nm';
    nm.textContent = c.name;
    const cnt = document.createElement('span');
    cnt.className = 'cnt';
    cnt.textContent = c.total ? (c.done + '/' + c.total) : '';
    const tag = document.createElement('span');
    tag.className = 'tag ' + (TAG_CLASS[c.note] || '');
    tag.textContent = c.note || '可刷';
    row.appendChild(nm); row.appendChild(cnt); row.appendChild(tag);
    row.onclick = () => { SEL = c.name; renderCourses(list); };
    frag.appendChild(row);
  });
  box.innerHTML = '';
  box.appendChild(frag);
}

function renderLogs(logs) {
  const box = $('#logs');
  if (!logs || !logs.length) {
    if (lastLogKey !== 'empty') { box.innerHTML = '<div class="hint">暂无日志。</div>'; lastLogKey = 'empty'; }
    return;
  }
  const last = logs[logs.length - 1];
  const key = logs.length + '|' + last.time + '|' + last.msg;
  if (key === lastLogKey) return;
  lastLogKey = key;
  const frag = document.createDocumentFragment();
  logs.forEach((l) => {
    const d = document.createElement('div');
    d.className = l.level || '';
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = l.time;
    d.appendChild(t);
    d.appendChild(document.createTextNode(l.msg));
    frag.appendChild(d);
  });
  box.innerHTML = '';
  box.appendChild(frag);
  box.scrollTop = box.scrollHeight;
}

function render(d) {
  const running = !!d.running;
  $('#dot').className = 'dot' + (running ? ' on' : '');
  $('#status').textContent = d.status || (running ? '运行中' : '空闲');
  $('#meta').textContent = [d.platform ? d.platform.toUpperCase() : '', 'v' + (d.version || '')]
    .filter(Boolean).join(' · ');

  const bar = $('#bar'), barin = $('#barin');
  if (d.progress === null || d.progress === undefined) {
    bar.classList.add('indet');
    barin.style.width = '40%';
    $('#pct').textContent = d.progress_text || (running ? '进行中…' : '');
  } else {
    bar.classList.remove('indet');
    barin.style.width = Math.max(0, Math.min(100, d.progress)) + '%';
    $('#pct').textContent = (d.progress_text ? d.progress_text + ' · ' : '') + d.progress + '%';
  }

  ['b-scan', 'b-read', 'b-brush', 'b-watch'].forEach((id) => { $('#' + id).disabled = running; });
  $('#b-stop').disabled = !running;

  renderCourses(d.courses);
  renderLogs(d.logs);

  const shot = $('#shot'), none = $('#shot-none');
  if (d.shot) {
    const k = d.shot + '|' + d.shot_ts;
    if (k !== shotKey) { shotKey = k; shot.src = '/api/shot?t=' + d.shot_ts; }
    shot.hidden = false; none.hidden = true;
  } else {
    shot.hidden = true; none.hidden = false;
  }
}

async function poll() {
  try {
    const j = await api('/api/state');
    if (j.ok) { render(j.data); $('#foot').textContent = '电脑 ' + j.data.ip + ':' + j.data.port; }
  } catch (e) { /* 网络抖动就等下一轮 */ }
}

$('#go').onclick = login;
$('#code').addEventListener('keydown', (e) => { if (e.key === 'Enter') login(); });
$('#b-scan').onclick = () => act('scan');
$('#b-read').onclick = () => act('read', { course: SEL });
$('#b-brush').onclick = () => {
  if (!SEL) { toast('先在下面选一门课'); return; }
  act('brush', { course: SEL });
};
$('#b-watch').onclick = () => act('watch', { hours: 2 });
$('#b-stop').onclick = () => act('stop');

// 扫码进来时地址里带着 #c=配对码：只预填、不自动登录
// （自动登录等于「拿到链接就能用」，截图外传就失守）
const hashCode = (location.hash || '').match(/c=([0-9]{6})/);
if (hashCode && !TOKEN) { $('#code').value = hashCode[1]; }

if (TOKEN) showApp(); else showLogin();

// PWA：registered only on secure context（局域网 http 下浏览器会拒绝，忽略即可）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}
</script>
</body>
</html>
"""

MANIFEST = {
    "name": "StudyBuddy 遥控器",
    "short_name": "StudyBuddy",
    "description": "在手机上查看并操作电脑端的 StudyBuddy",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#F4F6FB",
    "theme_color": "#4C6FFF",
    "lang": "zh-CN",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    ],
}

SW_JS = r"""const CACHE = 'sb-remote-v1';
const SHELL = ['/', '/icon-192.png', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (u.pathname.indexOf('/api/') === 0) return;   // 状态与动作永远走网络，不缓存
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""
