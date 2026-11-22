import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 集成进 StudyBuddy 后，playwright-core 来自 Python Playwright 自带的 driver，
// 不需要用户另外 npm install。外部独立运行时仍按原方式 import 'playwright-core'。
const pw = await import(process.env.SB_PW_ENTRY || 'playwright-core');
const { chromium } = pw;

const HERE = path.dirname(fileURLToPath(import.meta.url));
// 默认会话目录 = suite/ 的上一级（browser/）；换机器/换目录解压后依然可用。
const ROOT = process.env.BROWSER_ROOT || path.resolve(HERE, '..');
const IO = path.join(ROOT, 'io');
const SHOTS = path.join(ROOT, 'shots');
const PROFILE = path.join(ROOT, 'profile');
fs.mkdirSync(IO, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

// 浏览器内核：优先环境变量 CHROME_PATH，其次 ms-playwright 下最新的 chromium，最后系统 Chrome。
function findChrome() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const local = process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || '', 'AppData', 'Local');
  const pwBase = path.join(local, 'ms-playwright');
  if (fs.existsSync(pwBase)) {
    const versions = fs.readdirSync(pwBase)
      .filter((d) => /^chromium-\d+$/.test(d))
      .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]));
    for (const v of versions) {
      const exe = path.join(pwBase, v, 'chrome-win64', 'chrome.exe');
      if (fs.existsSync(exe)) return exe;
    }
  }
  const sys = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    // StudyBuddy 面向国内用户，绝大多数机器只有 Edge，这里作为内核兜底。
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    path.join(local, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
  ];
  return sys.find((p) => p && fs.existsSync(p));
}
const executablePath = findChrome();
if (!executablePath) {
  console.error('找不到 Chrome/Chromium。请安装浏览器，或用环境变量 CHROME_PATH 指定 chrome.exe 路径。');
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const writeJson = (file, obj) => fs.writeFileSync(path.join(IO, file), JSON.stringify(obj, null, 2));

const LAUNCH_ARGS = [
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-blink-features=AutomationControlled',
  '--window-position=60,40',
  '--window-size=1500,940',
  // 学习通会检测失焦/遮挡并暂停视频；关掉 Chrome 的后台节流，让后台标签也照常跑计时
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
  '--disable-features=CalculateNativeWinOcclusion',
];

const LAUNCH_OPTS = {
  headless: false,
  executablePath,
  viewport: { width: 1500, height: 940 },
  ignoreDefaultArgs: ['--enable-automation'],
  args: LAUNCH_ARGS,
};

// v1.8.0：Playwright 默认给 Chromium 加 --no-sandbox，Chrome 就会在窗口顶部
// 挂一条红字「您使用的是不受支持的命令行标记：--no-sandbox」——用户截图里那个
// 「启动浏览器会报错」就是它。打开 chromiumSandbox 后 Playwright 不再加这个参数，
// 警告条消失。个别以管理员身份运行的机器带沙箱起不来，那时才回退并如实提示。
let ctx;
try {
  ctx = await chromium.launchPersistentContext(PROFILE, { ...LAUNCH_OPTS, chromiumSandbox: true });
} catch (e) {
  console.error('沙箱模式启动失败，改用免沙箱模式（窗口顶部可能出现命令行标记提示）：' + (e && e.message ? e.message : e));
  ctx = await chromium.launchPersistentContext(PROFILE, { ...LAUNCH_OPTS, chromiumSandbox: false });
}

// 登录会话单独存一份 cookie 快照。
// Chromium 的 profile 只在浏览器**正常退出**时才把 cookie 写进 SQLite；
// 上一轮真机测试里，登录成功后命令行进程直接退出（管道断开）把浏览器一起带走了，
// 结果 profile 里只剩 v8 的游客 SESSIONID，扫描课程时又退回登录页——
// 明明刚登录成功却要再扫一次码。所以每成功登录一次就主动写快照，启动时补回去。
// 注意别叫 session.json：run.py 的「课程会话（courseUrl）」已经占用了这个文件名，
// 两个东西同名会互相覆盖——cookie 快照被课程会话盖掉，下次启动就当没登录过。
const SESSION = path.join(IO, 'auth.json');

let page = ctx.pages()[0] || (await ctx.newPage());
let quitting = false;
const pages = () => ctx.pages();
const pick = (i) => (Number.isInteger(i) && pages()[i] ? pages()[i] : page);

try {
  if (fs.existsSync(SESSION)) {
    const st = JSON.parse(fs.readFileSync(SESSION, 'utf8'));
    if (Array.isArray(st.cookies) && st.cookies.length) {
      await ctx.addCookies(st.cookies);
      console.log('SESSION RESTORED', st.cookies.length);
    }
  }
} catch (e) {
  console.log('SESSION RESTORE FAILED', String(e));
}

async function saveSession() {
  const st = await ctx.storageState();
  const cookies = (st.cookies || []).filter((c) => /chaoxing\.com$/.test(c.domain) || /chaoxing\.com$/.test(String(c.domain).replace(/^\./, '')));
  writeJson('auth.json', { at: new Date().toISOString(), cookies });
  return cookies.length;
}

async function cdpEval(target, expression) {
  const cdp = await ctx.newCDPSession(target);
  try {
    const r = await cdp.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: true,
    });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.text + ' :: ' + (r.exceptionDetails.exception?.description || ''));
    }
    return r.result.value;
  } finally {
    await cdp.detach();
  }
}

function findFrame(target, needle) {
  if (!needle) return target.mainFrame();
  const f = target.frames().find((x) => x.url().includes(needle));
  if (!f) throw new Error('frame not found: ' + needle + ' | ' + target.frames().map((x) => x.url()).join(' , '));
  return f;
}

async function handle(cmd) {
  const target = pick(cmd.pageIndex);
  switch (cmd.type) {
    case 'status':
      return {
        pages: pages().map((p, i) => ({ i, url: p.url(), title: '' })),
        activeUrl: target.url(),
        frames: target.frames().map((f) => f.url()),
      };
    case 'titles':
      return await Promise.all(pages().map(async (p, i) => ({ i, url: p.url(), title: await p.title().catch(() => '') })));
    case 'goto':
      await target.goto(cmd.url, { waitUntil: cmd.waitUntil || 'domcontentloaded', timeout: 60000 });
      await sleep(cmd.settle || 1500);
      return { url: target.url() };
    case 'reload':
      await target.reload({ waitUntil: 'domcontentloaded', timeout: 60000 });
      await sleep(cmd.settle || 2000);
      return { url: target.url() };
    case 'wait':
      await sleep(cmd.ms || 1000);
      return { url: target.url() };
    case 'shot': {
      const file = cmd.path || path.join(SHOTS, 'shot.png');
      fs.mkdirSync(path.dirname(file), { recursive: true });
      await target.screenshot({ path: file, fullPage: !!cmd.fullPage, type: 'png' });
      return { file, url: target.url() };
    }
    case 'eval': {
      if (cmd.frameUrl) {
        const f = findFrame(target, cmd.frameUrl);
        return await f.evaluate((src) => {
          // eslint-disable-next-line no-new-func
          return new Function(src)();
        }, cmd.code);
      }
      return await cdpEval(target, cmd.code);
    }
    case 'text': {
      const f = findFrame(target, cmd.frameUrl);
      return await f.evaluate(() => document.body.innerText.slice(0, 200000));
    }
    case 'html': {
      const f = findFrame(target, cmd.frameUrl);
      return await f.evaluate(() => document.documentElement.outerHTML.slice(0, 400000));
    }
    case 'frames':
      return target.frames().map((f) => ({ url: f.url(), name: f.name() }));
    case 'click': {
      const f = findFrame(target, cmd.frameUrl);
      const loc = cmd.text ? f.getByText(cmd.text, { exact: !!cmd.exact }).first() : f.locator(cmd.selector).first();
      await loc.click({ timeout: cmd.timeout || 15000, force: !!cmd.force });
      await sleep(cmd.settle || 1200);
      return { url: target.url(), frames: target.frames().map((x) => x.url()) };
    }
    case 'fill': {
      const f = findFrame(target, cmd.frameUrl);
      await f.locator(cmd.selector).first().fill(cmd.value, { timeout: 15000 });
      return { ok: true };
    }
    case 'scroll': {
      const f = findFrame(target, cmd.frameUrl);
      await f.evaluate((y) => window.scrollTo(0, y), cmd.y ?? 999999);
      await sleep(cmd.settle || 500);
      return { ok: true };
    }
    case 'bring':
      await target.bringToFront();
      return { ok: true };
    case 'focusemu': {
      // CDP 焦点模拟：让页面永远认为自己是 focused，不受真实窗口焦点影响
      const cdp = await ctx.newCDPSession(target);
      try {
        await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: cmd.enabled !== false });
      } finally {
        await cdp.detach().catch(() => {});
      }
      return { ok: true, enabled: cmd.enabled !== false };
    }
    case 'winstate': {
      // 最小化/还原窗口：用来实测「窗口失焦/被遮挡时视频还播不播」
      const cdp = await ctx.newCDPSession(target);
      try {
        const { windowId } = await cdp.send('Browser.getWindowForTarget');
        await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: cmd.state || 'normal' } });
        return { ok: true, windowId, state: cmd.state || 'normal' };
      } finally {
        await cdp.detach().catch(() => {});
      }
    }
    case 'mouse': {
      // 只移动不点击：给页面的 mousemove 监听器喂真实输入事件，破「鼠标移出窗口」检测
      await target.bringToFront();
      const x = Number.isFinite(cmd.x) ? cmd.x : 700;
      const y = Number.isFinite(cmd.y) ? cmd.y : 500;
      await target.mouse.move(x, y, { steps: 6 });
      return { ok: true, x, y };
    }
    case 'select':
      page = pick(cmd.pageIndex);
      return { url: page.url() };
    case 'newtab':
      page = await ctx.newPage();
      if (cmd.url) await page.goto(cmd.url, { waitUntil: 'domcontentloaded' });
      return { url: page.url() };
    case 'close':
      await target.close();
      page = pages()[0];
      return { ok: true };
    case 'save-state':
      return { count: await saveSession() };
    case 'quit':
      // StudyBuddy 退出时用：先回执，再优雅关浏览器，让 cookie 落盘。
      quitting = true;
      await saveSession().catch(() => {});
      return { ok: true };
    default:
      throw new Error('unknown cmd: ' + cmd.type);
  }
}

try {
  // 登录页本身（v8）在带着有效 cookie 时也不会跳走，URL 一直是 v8，
  // 所以有没有登录过不能看 v8，得直接去个人空间探一下。
  const hasAuth = fs.existsSync(SESSION);
  const startUrl = hasAuth ? 'https://i.chaoxing.com/base' : 'https://v8.chaoxing.com/';
  await page.goto(startUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 登录页默认标签会异步切换（扫码/账密），给足渲染时间再拍首帧，
  // 否则界面「最新画面」里会出现空白二维码框。
  await sleep(6000);
  const u = page.url();
  const stillLogin = /passport2\.chaoxing\.com|v8\.chaoxing\.com\/?($|\?)/i.test(u);
  if (stillLogin) {
    await page.screenshot({ path: path.join(SHOTS, '01-login.png'), type: 'png' });
  }
  writeJson('state.json', {
    stage: stillLogin ? 'qr-ready' : 'logged-in',
    url: u,
    at: new Date().toISOString(),
  });
  console.log(stillLogin ? 'QR READY' : 'LOGGED IN', u);
} catch (e) {
  writeJson('state.json', { stage: 'nav-error', error: String(e) });
  console.log('NAV ERROR', String(e));
}

const done = new Set();
// 轮询间隔 600ms → 80ms：翻页类任务每页一次指令，600ms 的取指令延迟等于
// 每页白等 0.6 秒（一门课几十页文档就多花十几分钟）。文件只有几百字节，
// 12 次/秒的读取开销可以忽略。任务空闲时也不会白跑：没新 id 就直接 continue。
const IDLE_POLL = 80;
while (true) {
  await sleep(IDLE_POLL);
  let cmd;
  try {
    cmd = JSON.parse(fs.readFileSync(path.join(IO, 'cmd.json'), 'utf8'));
  } catch {
    continue;
  }
  if (!cmd.id || done.has(cmd.id)) continue;
  done.add(cmd.id);
  const out = { id: cmd.id, ok: true, at: new Date().toISOString() };
  try {
    out.data = await handle(cmd);
  } catch (e) {
    out.ok = false;
    out.error = String(e && e.stack ? e.stack : e).slice(0, 4000);
  }
  writeJson('result.json', out);
  console.log(cmd.id, cmd.type, out.ok ? 'OK' : 'ERR', out.error || '');
  if (quitting) {
    await sleep(300);
    await ctx.close().catch(() => {});
    process.exit(0);
  }
}
