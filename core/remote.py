"""手机远控：把桌面端的能力用 HTTP 暴露给同一网络里的手机。

为什么这样设计（三个约束决定了架构）：

1. **事件只能有一个消费者**
   `EventBus` 的队列已由 GUI 独占轮询（`_poll_bus`）。网页若再去 `get_nowait()`，
   两边就会互相吞事件：网页拿走一条日志，桌面日志区就少一条。
   所以这里用「镜像」——GUI 每处理完一个事件就 `feed()` 一份到这里，
   网页只读镜像，永远不碰原队列。

2. **tkinter 只能在主线程碰**
   HTTP 请求跑在 `ThreadingHTTPServer` 的工作线程里。会话线程里直接调
   `self.on_brush()` 会随机崩（Tcl 不是线程安全的）。所以网页发来的动作
   不直接执行，而是塞进 `pending` 队列，由 GUI 的 `_poll_bus` 在主线程取出执行。

3. **它握着账号登录态，不能敞开**
   默认要 6 位配对码（每次启动重新生成、显示在电脑弹窗里）换一个随机 token，
   token 放在 HttpOnly cookie 里；同一 IP 连续错 5 次锁 60 秒。
   只允许白名单动作，图片接口只读 `SHOTS_DIR` 内的文件，防路径穿越。

**不要把这个端口映射到公网。** 想在外网用，走 Tailscale 之类的虚拟组网，
让手机和电脑在同一个私有网段里互相看见即可，不需要公网 IP、也不用开端口。
"""
import json
import os
import queue
import secrets
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from config import APP_VERSION, SHOTS_DIR, resource_path
from core import remote_page

MAX_BODY = 4096          # 网页只会发几十字节的 JSON，超过这个量直接拒掉
LOG_LIMIT = 200          # 手机屏幕翻不动 200 条以上，多了只是浪费带宽
COOKIE_NAME = "sb_remote"
FAIL_LIMIT = 5           # 同一 IP 连续错 5 次
LOCK_SECONDS = 60

_ICON_PNG = resource_path(os.path.join("assets", "icon.png"))


def lan_ip():
    """本机在局域网里的地址（不实际发包，只让系统选出口网卡）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class RemoteHub:
    """远程端与桌面端共享的状态中枢（不依赖 tkinter，可单独测试）。"""

    def __init__(self, port=8765):
        self.port = int(port)
        self.version = APP_VERSION
        self._lock = threading.Lock()
        self._tokens = set()
        self._fails = {}                      # ip -> [失败次数, 解禁时间戳]
        self.code = self.new_code()
        self.logs = deque(maxlen=LOG_LIMIT)
        self.state = {
            "running": False,
            "status": "空闲",
            "progress": None,                 # 0-100 或 None（不确定）
            "progress_text": "",
            "platform": "",
            "courses": [],
            "shot": "",                        # 最新截图文件名
            "shot_ts": 0,
        }
        self.pending = queue.Queue()          # 网页动作 → GUI 主线程
        self.handlers = {}                    # 动作名 → 可调用对象，由 GUI 注册

    # ---------- 配对码 / 登录 ----------
    def new_code(self):
        """六位数字配对码。用 secrets 而不是 random，避免被推算。"""
        code = "%06d" % secrets.randbelow(1000000)
        return code

    def rotate_code(self):
        """换一个新配对码，并让已发出去的 token 全部失效。"""
        with self._lock:
            self.code = self.new_code()
            self._tokens.clear()
            self._fails.clear()
        return self.code

    def login(self, code, ip=""):
        """校验配对码。返回 (token, 错误文案)。"""
        now = time.time()
        with self._lock:
            ent = self._fails.get(ip) or [0, 0.0]
            if ent[1] > now:
                return None, "失败次数过多，请 %d 秒后再试" % int(ent[1] - now)
            if str(code or "").strip() != self.code:
                ent[0] += 1
                if ent[0] >= FAIL_LIMIT:
                    ent[0] = 0
                    ent[1] = now + LOCK_SECONDS
                self._fails[ip] = ent
                return None, "配对码不对"
            self._fails.pop(ip, None)
            tok = secrets.token_urlsafe(24)
            self._tokens.add(tok)
            return tok, ""

    def authed(self, token):
        if not token:
            return False
        with self._lock:
            return token in self._tokens

    # ---------- 状态镜像（GUI 线程写，HTTP 线程读） ----------
    def feed(self, kind, data):
        """GUI 每处理一个事件就喂一份进来；这里只动内存，不做任何界面操作。"""
        data = data or {}
        with self._lock:
            if kind == "log":
                self.logs.append({
                    "time": data.get("time") or "",
                    "msg": str(data.get("msg") or "")[:600],
                    "level": data.get("level") or "info",
                })
            elif kind == "progress":
                self.state["progress"] = data.get("value")
                self.state["progress_text"] = data.get("text") or ""
            elif kind == "status":
                self.state["status"] = data.get("text") or ""
            elif kind == "courses":
                items = []
                for it in (data.get("items") or [])[:60]:
                    items.append({
                        "name": it.get("name") or "",
                        "note": it.get("note") or "",
                        "done": int(it.get("done") or 0),
                        "total": int(it.get("total") or 0),
                        "brushable": bool(it.get("brushable")),
                        "finished": bool(it.get("finished")),
                        "unknown": bool(it.get("unknown")),
                    })
                self.state["courses"] = items
                self.state["platform"] = data.get("platform") or ""
            elif kind == "shot":
                p = data.get("path") or ""
                if p:
                    self.state["shot"] = os.path.basename(p)
                    self.state["shot_ts"] = int(time.time())
            elif kind == "done":
                self.state["running"] = False
                self.state["status"] = data.get("summary") or ("已完成" if data.get("ok") else "已结束")

    def set_running(self, running):
        with self._lock:
            self.state["running"] = bool(running)

    def snapshot(self):
        with self._lock:
            d = dict(self.state)
            d["courses"] = [dict(x) for x in self.state["courses"]]
            d["logs"] = list(self.logs)
            d["version"] = self.version
            d["ip"] = lan_ip()
            d["port"] = self.port
        return d

    # ---------- 动作 ----------
    def can(self, name):
        return name in self.handlers

    def submit(self, name, params=None):
        """网页线程调用：只入队，真正的执行留给 GUI 主线程。"""
        if not self.can(name):
            return False, "不支持的操作：%s" % name
        if self.state["running"] and name != "stop":
            return False, "任务进行中，先停止再操作"
        self.pending.put((name, dict(params or {})))
        return True, ""

    def take_pending(self, limit=8):
        """GUI 主线程调用：取出待执行动作。"""
        out = []
        for _ in range(limit):
            try:
                out.append(self.pending.get_nowait())
            except queue.Empty:
                break
        return out


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hub = None                            # 由 RemoteServer 注入

    # 不打访问日志：既免刷屏，也避免把 cookie / 路径写进日志文件
    def log_message(self, fmt, *args):
        pass

    # ---------- 响应工具 ----------
    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return b""
        return self.rfile.read(min(n, MAX_BODY))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), extra=extra)

    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v
        return self.headers.get("X-SB-Token") or ""

    def _authed(self):
        return self.hub.authed(self._cookie_token())

    def _need_auth(self):
        self._json({"ok": False, "error": "未登录"}, 401)
        return False

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            # 页面本身公开（就是个登录框），数据接口才要 token
            self._send(200, remote_page.PAGE, "text/html; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            self._send(200, json.dumps(remote_page.MANIFEST, ensure_ascii=False),
                       "application/manifest+json; charset=utf-8")
            return
        if path == "/sw.js":
            self._send(200, remote_page.SW_JS, "application/javascript; charset=utf-8")
            return
        if path in ("/icon-192.png", "/icon-512.png"):
            size = 192 if "192" in path else 512
            data = self._icon(size)
            if data:
                self._send(200, data, "image/png")
            else:
                self._json({"ok": False, "error": "缺少图标"}, 404)
            return
        if path == "/api/state":
            if not self._authed():
                self._need_auth()
                return
            self._json({"ok": True, "data": self.hub.snapshot()})
            return
        if path == "/api/shot":
            if not self._authed():
                self._need_auth()
                return
            self._shot()
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _icon(self, size):
        """用打包进来的 icon.png 现缩放，省得再往仓库塞两套图。"""
        try:
            from io import BytesIO
            from PIL import Image
            if not os.path.exists(_ICON_PNG):
                return None
            im = Image.open(_ICON_PNG).convert("RGBA")
            im = im.resize((size, size), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _shot(self):
        """只允许读 SHOTS_DIR 里存在的那张图，且必须还在目录内（防 ../ 穿越）。"""
        name = os.path.basename(self.hub.state.get("shot") or "")
        if not name:
            self._json({"ok": False, "error": "暂无截图"}, 404)
            return
        full = os.path.realpath(os.path.join(SHOTS_DIR, name))
        root = os.path.realpath(SHOTS_DIR)
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            self._json({"ok": False, "error": "暂无截图"}, 404)
            return
        try:
            with open(full, "rb") as f:
                data = f.read(4 * 1024 * 1024)
            self._send(200, data, "image/png")
        except Exception:
            self._json({"ok": False, "error": "读取失败"}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                payload = json.loads(self._body() or b"{}")
            except Exception:
                payload = {}
            ip = self.client_address[0] if self.client_address else ""
            token, err = self.hub.login(payload.get("code"), ip)
            if not token:
                self._json({"ok": False, "error": err}, 401)
                return
            cookie = ("%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"
                      % (COOKIE_NAME, token))
            self._json({"ok": True, "token": token}, extra={"Set-Cookie": cookie})
            return
        if path == "/api/action":
            if not self._authed():
                self._need_auth()
                return
            try:
                payload = json.loads(self._body() or b"{}")
            except Exception:
                self._json({"ok": False, "error": "请求格式不对"}, 400)
                return
            name = str(payload.get("name") or "")
            ok, err = self.hub.submit(name, payload.get("params") or {})
            self._json({"ok": ok, "error": err}, 200 if ok else 400)
            return
        self._json({"ok": False, "error": "not found"}, 404)


class RemoteServer:
    """HTTP 外壳：只管启停与端口，业务都在 RemoteHub 里。"""

    def __init__(self, hub, host="0.0.0.0", port=None):
        self.hub = hub
        self.host = host
        self.port = int(port or hub.port)
        self.error = ""
        self._httpd = None
        self._thread = None

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        if self._httpd is not None:
            return True
        handler = type("RemoteHandler", (_Handler,), {"hub": self.hub})
        try:
            httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as e:
            self.error = "端口 %d 监听失败（可能被占用）：%s" % (self.port, e)
            return False
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        name="sb-remote", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        self._thread = None

    def url(self):
        return "http://%s:%d" % (lan_ip(), self.port)
