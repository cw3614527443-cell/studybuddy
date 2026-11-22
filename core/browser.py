"""浏览器控制层：启动/连接独立 Edge 进程，提供截图、等待、弹窗处理等通用操作。

设计要点（v1.0.1 重构）
--------------------------------
- **Edge 由 subprocess 独立启动**（带 --remote-debugging-port），不再作为 Playwright 的
  子进程。原因：Playwright 同步 API 有线程亲和性，`sync_playwright().start()` 建立的连接
  只能在创建它的线程里用；而本程序每个任务都跑在新线程里，于是第二个任务复用旧连接时
  必然抛 `TargetClosedError: Playwright connection closed`。
  改为独立进程后，每个任务线程各建各的 CDP 连接，任务结束就断开，互不影响。
- 登录态靠固定 `user-data-dir` 持久化，只要 Edge 不关就一直有效，无需重复扫码。
- 所有点击/读取都通过 evaluate 在不改变页面焦点的情况下完成，避免打断视频播放。
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from config import USERDATA_DIR, SHOTS_DIR, CDP_PORT

CDP_HTTP = f"http://127.0.0.1:{CDP_PORT}"
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# 本地回环必须绕过系统代理，否则探测/连接会被代理劫持
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _sync_playwright():
    """延迟导入 playwright：GUI 启动不依赖它，只有真正操作浏览器时才需要。"""
    from playwright.sync_api import sync_playwright
    return sync_playwright


def _find_edge():
    """定位 msedge.exe。"""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _port_alive(timeout=0.8):
    """调试端口是否已就绪（同时也证明是 Chromium 系浏览器）。"""
    try:
        with _opener.open(f"{CDP_HTTP}/json/version", timeout=timeout) as r:
            json.loads(r.read().decode("utf-8", "ignore"))
            return True
    except Exception:
        return False


def _pid_on_port(port=CDP_PORT):
    """从 netstat 里找出监听该端口的进程 PID（用于没有记录 pid 时兜底关闭）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=8,
            encoding="mbcs", errors="ignore",
        ).stdout
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
    except Exception:
        pass
    return None


class EdgeProc:
    """Edge 进程管理：跨线程共享，只负责启动 / 探活 / 关闭，不持有 Playwright 对象。"""

    _pid = None
    _started = False   # 本进程是否亲手拉起过 Edge（决定 kill 时该不该动手）
    _lock = threading.Lock()

    @classmethod
    def is_running(cls):
        return _port_alive()

    @classmethod
    def start(cls, headless=False, start_url=None, wait=40, log=None):
        """启动独立 Edge（带调试端口）。已在运行则直接复用，并记为「非本进程启动」。"""
        with cls._lock:
            if _port_alive():
                cls._started = False
                if log:
                    log("检测到已有受控浏览器，直接复用")
                return False
            edge = _find_edge()
            if not edge:
                raise RuntimeError("未找到 Microsoft Edge，请先安装 Edge 后再试")
            args = [
                edge,
                f"--remote-debugging-port={CDP_PORT}",
                "--remote-allow-origins=*",
                f"--user-data-dir={USERDATA_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=Translate",
            ]
            if headless:
                args.append("--headless=new")
            else:
                args.extend(["--start-maximized", "--new-window"])
            if start_url:
                args.append(start_url)

            kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL)
            if os.name == "nt":
                kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
            cls._pid = subprocess.Popen(args, **kwargs).pid
            cls._started = True
            if log:
                log(f"Edge 已拉起（PID {cls._pid}），等待调试端口就绪…")

            deadline = time.time() + wait
            while time.time() < deadline:
                if _port_alive():
                    return True
                time.sleep(0.5)
            raise RuntimeError(
                "Edge 调试端口未就绪。常见原因：本程序目录下的 userdata 正被另一个 "
                "Edge 窗口占用——请先关闭所有 Edge 窗口再重试。"
            )

    @classmethod
    def kill(cls, grace=6, force_any=False):
        """关闭受控 Edge（整棵进程树）。

        安全阀（v1.0.3）：如果这个 Edge 不是本进程拉起来的（比如用户已经开着一个、
        我们只是 attach 上去复用），默认**不动它**，避免把用户正在看视频的窗口误杀。
        只有用户明确点「关闭浏览器」时才传 force_any=True。

        关闭方式：先发温和关闭信号（taskkill 不带 /F → WM_CLOSE），给 Edge 时间把
        cookie / 登录态写盘；grace 秒后仍未退出才强杀。直接 /F 会导致下次还要重新扫码。
        """
        if not force_any and not cls._started and cls._pid is None:
            return
        pid = cls._pid or _pid_on_port()
        if not pid:
            cls._pid = None
            cls._started = False
            return
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        deadline = time.time() + grace
        while time.time() < deadline:
            if not _port_alive(timeout=0.3):
                break
            time.sleep(0.4)
        if _port_alive(timeout=0.3):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            except Exception:
                pass
        cls._pid = None
        cls._started = False


# ---------------------------------------------------------------------------
# 页面内「看门狗」：静音 + 续播弹窗自动关闭
# ---------------------------------------------------------------------------
# 为什么放进页面里跑，而不是用 Python 轮询？
#   1. Playwright 同步 API 有线程亲和性，Python 侧没法再开一个看门狗线程；
#   2. 弹窗和「平台把 muted 改回 false」都是随时发生的，Python 只在某几步检查，
#      中间几分钟的视频播放时间完全没人管 —— 实测就是这样漏掉的；
#   3. 页面内的 setInterval 不受 Python 忙闲影响，0.5 秒一轮，反应快且零成本。
# 脚本必须做到「幂等」：重复注入只更新开关，不重复装监听。
_GUARD_JS = r"""
(() => {
  try {
    if (window.__sbGuard) { window.__sbGuard.muteOn = true; return; }
  } catch (e) { return; }

  const G = window.__sbGuard = {
    muteOn: true,        // 强制静音（任务结束时置 false，把声音还给用户）
    preferResume: true,  // 续播弹窗优先点「继续播放」；刷时长会改成「重新开始」
    muted: 0,            // 累计静音次数
    modal: 0,            // 累计自动关闭的弹窗数
    lastModal: '',
    lastAt: 0,
    ticks: 0
  };

  // ---------- 静音 ----------
  const media = (el) => {
    if (!el) return;
    const tag = (el.tagName || '').toUpperCase();
    if (tag !== 'VIDEO' && tag !== 'AUDIO') return;
    try {
      if (!el.muted) { el.muted = true; G.muted++; }
      if (el.volume !== 0) { try { el.volume = 0; } catch (e) {} }
      if (el.defaultMuted !== true) el.defaultMuted = true;
    } catch (e) {}
  };

  const muteAll = () => {
    if (!G.muteOn) return;
    let list;
    try { list = document.querySelectorAll('video,audio'); } catch (e) { return; }
    for (const el of list) media(el);
  };

  // 平台的播放器经常在 play() 之前把 muted 改回 false（音量条初始化、自动播放策略等），
  // 所以要在 play() 真正下发前再抢一次。
  try {
    const proto = HTMLMediaElement.prototype;
    const raw = proto.play;
    if (raw && !raw.__sbHooked) {
      const hooked = function () {
        try { if (G.muteOn) media(this); } catch (e) {}
        return raw.apply(this, arguments);
      };
      hooked.__sbHooked = true;
      proto.play = hooked;
    }
  } catch (e) {}

  // ---------- 续播弹窗 ----------
  // 只认「上次播放 / 是否继续」这一类询问框，别的弹窗一律不碰（防止误点确认按钮）
  // v1.4.3：文档（PPT/PDF）的续播文案是「上次观看到第23页，是否继续观看？」，
  // 按钮是「确定 / 取消」，和视频那套不一样，之前整类漏掉。
  const DOC_HINT = /上次观看到|上次阅读到|上次浏览到/;
  const HINT = /上次播放|是否继续|继续上次|未播放完|上次未完成|断点续播|继续观看|上次观看到|上次阅读到|上次浏览到/;
  const BTN = 'button,[role="button"],a,.ivu-btn,.el-button,[class*="btn"],[class*="Btn"],[class*="button"]';

  const visible = (e) => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(e);
    return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
  };

  // 从按钮往上找，最近的一个「高 z-index 浮层」里是否含续播文案
  const hintDialog = (el, re) => {
    const pattern = re || HINT;
    let p = el, i = 0;
    while (p && i++ < 12) {
      let st;
      try { st = getComputedStyle(p); } catch (e) { return null; }
      if (st.position === 'fixed' || st.position === 'absolute') {
        const z = parseInt(st.zIndex, 10) || 0;
        if (z >= 50) {
          const t = (p.innerText || '').trim();
          if (t && t.length < 400 && pattern.test(t)) return p;
        }
      }
      p = p.parentElement;
    }
    return null;
  };

  const findBtn = (label, re) => {
    let best = null, all;
    try { all = document.querySelectorAll(BTN); } catch (e) { return null; }
    for (const e of all) {
      if (e.children.length > 1) continue;
      if ((e.textContent || '').trim() !== label) continue;
      if (!visible(e)) continue;
      if (e.disabled) continue;
      if (/disabled/i.test(String(e.className || ''))) continue;
      if (!hintDialog(e, re)) continue;
      if (!best) { best = e; continue; }
      if (best.contains && best.contains(e)) best = e;   // 取最内层，别点外层容器
    }
    return best;
  };

  let lastClick = 0;
  const closeModal = () => {
    const now = Date.now();
    if (now - lastClick < 900) return false;   // 冷却，避免对着同一个按钮连点
    const order = G.preferResume ? ['继续播放', '重新开始'] : ['重新开始', '继续播放'];
    for (const label of order) {
      const b = findBtn(label);
      if (!b) continue;
      try { b.click(); } catch (e) { continue; }
      G.modal++; G.lastModal = label; G.lastAt = now;
      lastClick = now;
      return true;
    }
    // 文档类续播：只有浮层里明确写了「上次观看到第X页」才点「确定」，
    // 普通公告里的「确定」绝不能点（DOC_HINT 不匹配就不会命中）。
    const docBtn = findBtn('确定', DOC_HINT) || findBtn('继续观看', DOC_HINT);
    if (docBtn) {
      try { docBtn.click(); } catch (e) { return false; }
      G.modal++; G.lastModal = '确定（文档续播）'; G.lastAt = now;
      lastClick = now;
      return true;
    }
    return false;
  };

  // ---------- 定时 ----------
  let pending = false;
  const scheduleMute = () => {          // DOM 变动时补一次静音（节流，避免频繁 querySelectorAll）
    if (pending) return;
    pending = true;
    setTimeout(() => { pending = false; muteAll(); }, 200);
  };
  try {
    new MutationObserver(scheduleMute).observe(document.documentElement, {childList: true, subtree: true});
  } catch (e) {}

  setInterval(() => { G.ticks++; muteAll(); closeModal(); }, 500);

  window.__sbGuardTick = () => { muteAll(); closeModal(); return G; };
  muteAll();
})();
"""


def _frames_of(page):
    """取页面下所有 frame（含跨域 iframe），失败则只返回主 frame。"""
    try:
        return list(page.frames)
    except Exception:
        return [page]


def install_guard(page, prefer_resume=True, mute=True):
    """给页面及所有 frame 装看门狗（幂等，可重复调用）。

    用 `add_init_script` 注册一份，保证**每次导航 / 新建 iframe 都自动注入**；
    同时立刻对已存在的 frame 执行一次，当前页面不用等刷新就生效。
    """
    try:
        page.add_init_script(_GUARD_JS)
    except Exception:
        pass
    flags = {"preferResume": bool(prefer_resume), "muteOn": bool(mute)}
    for f in _frames_of(page):
        try:
            f.evaluate(_GUARD_JS)
            f.evaluate(
                """(o) => { const g = window.__sbGuard; if (!g) return false;
                    g.preferResume = o.preferResume; g.muteOn = o.muteOn; return true; }""",
                flags,
            )
        except Exception:
            continue


class Browser:
    """**一次任务内**的 Playwright 连接。

    必须在任务线程里 `connect()`，并在同一线程使用；任务结束由 TaskRunner 调 `detach()`。
    绝不可把它缓存下来给下一个任务线程复用——那正是旧版本崩溃的原因。
    """

    def __init__(self, bus=None):
        self.bus = bus
        self.pw = None
        self._cdp = None
        self.ctx = None
        self.page = None
        self._shot_seq = 0
        self._guard_seen = {"modal": 0, "muted": 0}
        self._mute_logged = False

    # ---------- 生命周期 ----------
    def connect(self, launch=True, headless=False, start_url=None, timeout=40, log=None):
        """在当前线程建立 CDP 连接；Edge 没起来就先把它拉起来。"""
        if not _port_alive():
            if not launch:
                raise RuntimeError("浏览器未启动，请先点「① 启动浏览器 / 登录」")
            EdgeProc.start(headless=headless, start_url=start_url, log=log or self._log)

        self.pw = _sync_playwright()().start()
        self._cdp = self.pw.chromium.connect_over_cdp(CDP_URL, timeout=timeout * 1000)
        self.ctx = self._cdp.contexts[0] if self._cdp.contexts else self._cdp.new_context()
        self.page = self._pick_page()
        self._log("已连接到浏览器")
        return self.page

    def _pick_page(self):
        """优先选 icve 页面，其次用户当前停留的最后一个标签页。"""
        pages = list(self.ctx.pages or [])
        if not pages:
            return self.ctx.new_page()
        for p in reversed(pages):
            try:
                if "icve" in (p.url or ""):
                    return p
            except Exception:
                continue
        return pages[-1]

    def page_count(self):
        try:
            return len(list(self.ctx.pages or []))
        except Exception:
            return 0

    def switch_to_newest_tab(self, expected=None, timeout=8):
        """点击某些课程卡片后平台会**新开标签页**；切到那个最新页。

        expected: URL 子串，只有新页 URL 含它才切（避免误切广告页）。
        返回是否成功切换。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            pages = list(self.ctx.pages or [])
            for p in reversed(pages):
                try:
                    url = p.url or ""
                except Exception:
                    continue
                if p is self.page:
                    continue
                if expected and expected not in url:
                    continue
                if "icve" not in url:
                    continue
                self.page = p
                return True
            time.sleep(0.4)
        return False

    def switch_to_course_tab(self, course_name, timeout=8):
        """在所有标签页里找页面文本含指定课程名的那个，切过去。

        课程卡片会新开 tab，且 tab 顺序不一定最新就是刚点的那门；用课程名
        校验最可靠（课程页的面包屑和标题里都有课程名）。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            pages = list(self.ctx.pages or [])
            for p in pages:
                try:
                    url = p.url or ""
                    if "spoccourse" not in url:
                        continue
                    text = p.evaluate(
                        "() => ((document.querySelector('.info_title')||document.body)"
                        ".innerText||'').slice(0,400)"
                    ) or ""
                    if course_name in text:
                        self.page = p
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def close_extra_tabs(self, keep_url_substr=None):
        """关掉多余标签页，只保留含 keep_url_substr 的那个（没有则保留当前页）。

        课程扫描会连点十几门课，某些平台每次都新开 tab，积多了会让页面选择混乱。
        """
        pages = list(self.ctx.pages or [])
        keep = None
        if keep_url_substr:
            for p in pages:
                if keep_url_substr in (p.url or ""):
                    keep = p
                    break
        keep = keep or self.page
        for p in pages:
            if p is keep:
                continue
            try:
                p.close()
            except Exception:
                pass
        self.page = keep

    def detach(self):
        """断开本次任务的连接，**不关** Edge，登录态与页面都保留。

        注意：这里绝不能调用 `self._cdp.close()`。对 `connect_over_cdp` 拿到的 Browser，
        Playwright 的 `Browser.close()` 会把**浏览器进程一起关掉**（v1.0.3 实测踩坑）。
        只停掉 driver（`pw.stop()`）就足够断开 websocket，Edge 不受影响。
        """
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self._cdp = None
        self.ctx = None
        self.page = None
        self.pw = None

    def close(self):
        """关闭整个受控浏览器（杀 Edge 进程）。"""
        self.detach()
        EdgeProc.kill()

    # ---------- 工具 ----------
    def _log(self, msg, level="info"):
        if self.bus:
            self.bus.log(msg, level)

    def alive(self):
        """连接是否仍然可用（轻量探活，不抛异常）。"""
        try:
            return self.page is not None and not self.page.is_closed()
        except Exception:
            return False

    # ---------- 看门狗（静音 + 弹窗） ----------
    def install_guards(self, prefer_resume=True, mute=True):
        """开始刷课/刷时长前调用：自动静音 + 自动关续播弹窗。"""
        if self.page is None:
            return False
        install_guard(self.page, prefer_resume=prefer_resume, mute=mute)
        self._guard_seen = {"modal": 0, "muted": 0}
        self._mute_logged = False
        return True

    def guard_set(self, prefer_resume=None, mute=None):
        """运行中调整开关（例如刷时长改成优先「重新开始」）。"""
        for f in _frames_of(self.page):
            try:
                f.evaluate(
                    """(o) => { const g = window.__sbGuard; if (!g) return false;
                        if (o.preferResume !== null) g.preferResume = o.preferResume;
                        if (o.muteOn !== null) g.muteOn = o.muteOn;
                        return true; }""",
                    {"preferResume": prefer_resume, "muteOn": mute},
                )
            except Exception:
                continue

    def guard_stats(self):
        """汇总所有 frame 的计数（弹窗关闭数 / 静音次数）。"""
        agg = {"modal": 0, "muted": 0, "last": "", "frames": 0}
        if self.page is None:
            return agg
        for f in _frames_of(self.page):
            try:
                g = f.evaluate(
                    "() => window.__sbGuard ? {m: window.__sbGuard.modal, "
                    "u: window.__sbGuard.muted, l: window.__sbGuard.lastModal} : null"
                )
            except Exception:
                continue
            if not g:
                continue
            agg["frames"] += 1
            agg["modal"] += g.get("m") or 0
            agg["muted"] += g.get("u") or 0
            if g.get("l"):
                agg["last"] = g["l"]
        return agg

    def guard_flush(self):
        """把看门狗新做的事写成日志（没有变化就什么都不做，可高频调用）。"""
        agg = self.guard_stats()
        prev = self._guard_seen or {"modal": 0, "muted": 0}
        # 页面导航会重置脚本状态，计数变小不代表出错，直接重新对齐
        if agg["modal"] < prev["modal"] or agg["muted"] < prev["muted"]:
            prev = {"modal": 0, "muted": 0}
        dm = agg["modal"] - prev["modal"]
        du = agg["muted"] - prev["muted"]
        if dm:
            self._log(f"已自动关闭续播弹窗 ×{dm}（点「{agg['last']}」）")
        if du and not self._mute_logged:
            self._mute_logged = True
            self._log("播放器已自动静音（刷课全程不出声）")
        self._guard_seen = {"modal": agg["modal"], "muted": agg["muted"]}
        return dm

    def release_guards(self):
        """任务结束：停止强制静音，把音量控制权还给用户（自己看视频时能开声音）。"""
        try:
            self.guard_flush()
            self.guard_set(mute=False)
        except Exception:
            pass

    def shot(self, tag="shot"):
        """截图并返回路径；失败返回 None（不抛异常）。"""
        try:
            self._shot_seq += 1
            name = (f"{time.strftime('%m%d_%H%M%S')}_{self._shot_seq:02d}_"
                    f"{re.sub(r'[^0-9A-Za-z_]', '_', tag)[:32]}.png")
            path = os.path.join(SHOTS_DIR, name)
            self.page.screenshot(path=path, timeout=10000, animations="disabled")
            if self.bus:
                self.bus.shot(path)
            return path
        except Exception:
            return None

    def wait(self, ms):
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            time.sleep(ms / 1000.0)

    def text(self):
        try:
            return self.page.locator("body").inner_text()
        except Exception:
            return ""

    def click_text(self, label, exact=True, visible_only=True, timeout=5000):
        """点击文本完全匹配的最内层元素。"""
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            try:
                ok = self.page.evaluate(
                    """({label, exact, visible_only}) => {
                        for (const e of document.querySelectorAll('*')) {
                            if (e.children.length !== 0) continue;
                            const t = (e.textContent || '').trim();
                            const hit = exact ? t === label : t.includes(label);
                            if (!hit) continue;
                            if (visible_only && e.offsetParent === null) continue;
                            e.click();
                            return true;
                        }
                        return false;
                    }""",
                    {"label": label, "exact": exact, "visible_only": visible_only},
                )
            except Exception:
                ok = False
            if ok:
                return True
            self.wait(400)
        return False

    def has_text(self, label):
        return label in self.text()

    def visible_modal_text(self):
        """返回当前**真正**挡路的浮层文本（用于弹窗检测）。

        v1.0.5 两次修正：
        1. ai.icve 的「检测到上次播放至X分X秒，是否继续？」对话框既不是 ivu-modal 也没有
           modal 类名，原选择器完全抓不到 → 改成按几何特征找浮层 + 全文关键字兜底。
        2. 反过来又踩了误报：`[class*=layer]`、`[class*=popup]` 把视频时间条
           「00:01 / 08:22」和右侧「课程评价」面板都算成了弹窗。现在要求元素必须是
           position:fixed/absolute、z-index≥100、覆盖一定面积，且内部有按钮文字。
        """
        try:
            txt = self.page.evaluate(
                """() => {
                    const styles = e => getComputedStyle(e);
                    const visible = e => {
                        const r = e.getBoundingClientRect();
                        const st = styles(e);
                        return r.width > 120 && r.height > 60 &&
                               st.display !== 'none' && st.visibility !== 'hidden' &&
                               st.opacity !== '0';
                    };
                    const cands = [];
                    for (const e of document.querySelectorAll('div,section,aside,form')) {
                        const st = styles(e);
                        if (st.position !== 'fixed' && st.position !== 'absolute') continue;
                        const z = parseInt(st.zIndex, 10) || 0;
                        if (z < 100) continue;
                        if (!visible(e)) continue;
                        const t = (e.innerText || '').trim();
                        if (!t || t.length > 400) continue;
                        // 像对话框的东西一定有按钮文字
                        if (!/继续播放|重新开始|确定|取消|知道了|关闭/.test(t)) continue;
                        const r = e.getBoundingClientRect();
                        cands.push({t, z, area: r.width * r.height});
                    }
                    cands.sort((a, b) => (b.z - a.z) || (b.area - a.area));
                    return cands.length ? cands[0].t.slice(0, 300) : '';
                }"""
            )
            if txt:
                return txt
        except Exception:
            pass
        # 兜底：整页文本里出现续播询问的专用文案（只可能来自那个弹窗）
        try:
            body = self.text()
            for key in ("检测到上次播放", "是否继续"):
                i = body.find(key)
                if i >= 0:
                    return body[max(0, i - 20): i + 60].replace("\n", " ")
        except Exception:
            pass
        return ""

    # ---------- 页面导航 ----------
    def goto(self, url, timeout=45000, retries=2):
        """导航到指定 URL。

        v1.0.1：站点会自动跳 SSO 登录页，如果此时我们也在发导航，Playwright 会抛
        `Navigation is interrupted by another navigation`。这里改为等页面稳定后重试，
        而不是直接把错误甩给用户。
        """
        last = None
        for attempt in range(retries + 1):
            try:
                self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                return self.page
            except Exception as e:
                last = e
                msg = str(e)
                if "interrupted by another navigation" not in msg and "net::ERR_ABORTED" not in msg:
                    raise
                # 等对方那次跳转落地，再重试
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                self.wait(1200)
        raise last

    def reload(self, timeout=45000):
        self.page.reload(timeout=timeout, wait_until="domcontentloaded")
        return self.page

    def current_url(self):
        try:
            return self.page.url
        except Exception:
            return ""

    def on_login_page(self):
        """当前是否被踢回统一登录页（登录态过期）。

        v1.4.2：读进度/复核时如果掉登录，页面既没有「观看时长」也没有课程卡片，
        以前会被当成「这门课不计学时」「目录里没读到资源」，让人往错的方向排查。

        v1.7.1：只看 URL 会误判。zjy2 是 SPA，身份校验走 sso 的那一瞬间
        `page.url` 已经是 sso，但 DOM 还是学习空间；若此时正好读进度/扫描，
        就会被判成「登录失效」，实际登录是好的。所以 URL 命中登录页时，
        再确认页面上有没有平台内容。
        """
        try:
            u = (self.page.url or "").lower()
        except Exception:
            u = ""
        if "sso.icve.com.cn" in u or "auth_v2" in u:
            try:
                t = self.text()[:800]
            except Exception:
                t = ""
            if any(k in t for k in ("学习空间", "我的课程", "我的课堂", "个人中心",
                                    "全部课程", "加入班级")):
                return False
            return True
        if "/login" in u:
            return True
        try:
            return "欢迎登录" in self.text()[:400]
        except Exception:
            return False
