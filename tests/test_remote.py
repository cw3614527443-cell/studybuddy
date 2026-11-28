"""手机远控服务的接口测试（v1.9.0）。

只测「服务这一层」——不联网、不启动浏览器、不碰真实课程：
鉴权、白名单、路径穿越、配对码轮换、启停。

用法：python tests/test_remote.py
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.remote import RemoteHub, RemoteServer   # noqa: E402

# 测试里禁用系统代理：环境里配了 http_proxy 的话会把 127.0.0.1 的请求也带走
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ok = True


def check(name, cond, extra=""):
    global ok
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra else ""))
    ok = ok and bool(cond)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def call(url, method="GET", body=None, token=None, raw=False):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-SB-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=5) as r:
            blob = r.read()
            return r.status, (blob if raw else json.loads(blob.decode() or "{}")), dict(r.headers)
    except urllib.error.HTTPError as e:
        blob = e.read()
        try:
            payload = json.loads(blob.decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload, dict(e.headers)


def main():
    port = free_port()
    hub = RemoteHub(port=port)
    for name in ("scan", "brush", "read", "watch", "stop"):
        hub.handlers[name] = name
    hub.feed("log", {"time": "12:00:00", "msg": "测试日志", "level": "info"})
    hub.feed("courses", {"platform": "ai", "items": [
        {"name": "大学生心理健康", "note": "刷完", "done": 9, "total": 9,
         "brushable": True, "finished": True},
    ]})
    srv = RemoteServer(hub, host="127.0.0.1", port=port)
    check("服务能启动", srv.start(), srv.error)
    base = "http://127.0.0.1:%d" % port

    try:
        # 1. 页面与静态资源（不需要登录）
        st, blob, _ = call(base + "/", raw=True)
        check("首页 200", st == 200, st)
        check("首页是手机页面", b"StudyBuddy" in blob and b"viewport" in blob)
        st, man, _ = call(base + "/manifest.webmanifest")
        check("manifest 可解析", st == 200 and man.get("short_name") == "StudyBuddy", st)
        st, blob, _ = call(base + "/icon-192.png", raw=True)
        check("图标是 PNG", st == 200 and blob[:8] == b"\x89PNG\r\n\x1a\n", st)

        # 2. 未登录一律 401
        st, body, _ = call(base + "/api/state")
        check("未登录读状态 401", st == 401 and not body.get("ok"), st)
        st, body, _ = call(base + "/api/action", "POST", {"name": "scan"})
        check("未登录发指令 401", st == 401, st)

        # 3. 配对码
        st, body, _ = call(base + "/api/login", "POST", {"code": "000000" if hub.code != "000000" else "111111"})
        check("错配对码被拒", st == 401 and "配对码" in body.get("error", ""), body.get("error"))
        st, body, headers = call(base + "/api/login", "POST", {"code": hub.code})
        token = body.get("token") or ""
        check("正确配对码换到 token", st == 200 and len(token) > 16, st)
        check("登录响应带 HttpOnly cookie",
              "HttpOnly" in (headers.get("Set-Cookie") or ""), headers.get("Set-Cookie"))

        # 4. 状态结构
        st, body, _ = call(base + "/api/state", token=token)
        data = body.get("data") or {}
        check("带 token 读状态 200", st == 200 and body.get("ok"), st)
        check("状态含日志镜像", any(x.get("msg") == "测试日志" for x in data.get("logs") or []),
              len(data.get("logs") or []))
        check("状态含课程列表", (data.get("courses") or [{}])[0].get("name") == "大学生心理健康")
        check("状态含端口与版本", data.get("port") == port and bool(data.get("version")),
              data.get("version"))

        # 5. 动作白名单
        st, body, _ = call(base + "/api/action", "POST", {"name": "rm_rf"}, token=token)
        check("未知动作被拒", st == 400 and not body.get("ok"), st)
        st, body, _ = call(base + "/api/action", "POST", {"name": "scan"}, token=token)
        pending = hub.take_pending()
        check("scan 进入待执行队列", st == 200 and pending and pending[0][0] == "scan", pending)

        # 6. 运行中只放行 stop
        hub.set_running(True)
        st, body, _ = call(base + "/api/action", "POST", {"name": "brush"}, token=token)
        check("运行中拒绝新任务", st == 400 and "停止" in body.get("error", ""), body.get("error"))
        st, body, _ = call(base + "/api/action", "POST", {"name": "stop"}, token=token)
        check("运行中允许 stop", st == 200 and hub.take_pending()[0][0] == "stop", st)
        hub.set_running(False)

        # 7. 截图接口：没有截图时 404，路径穿越也拿不到东西
        st, body, _ = call(base + "/api/shot", token=token)
        check("无截图返回 404", st == 404, st)
        hub.state["shot"] = "../../config.py"
        st, body, _ = call(base + "/api/shot", token=token)
        check("路径穿越被挡掉", st == 404, st)

        # 8. 换配对码后旧 token 失效
        hub.rotate_code()
        st, body, _ = call(base + "/api/state", token=token)
        check("换码后旧 token 失效", st == 401, st)
        st, body, _ = call(base + "/api/login", "POST", {"code": hub.code})
        token2 = body.get("token") or ""
        check("新码可再登录", st == 200 and token2 and token2 != token, st)

        # 9. 连错达到 5 次锁 60 秒
        #    锁是在第 5 次失败后的下一次请求才体现（那一次才读到解禁时间），
        #    所以这里多发一次，别只发 5 次就断言。
        locked = False
        for _ in range(6):
            wrong = "111111" if hub.code != "111111" else "222222"
            st, body, _ = call(base + "/api/login", "POST", {"code": wrong})
            if st == 401 and "秒后再试" in body.get("error", ""):
                locked = True
                break
        check("连错 5 次被锁", locked)
    finally:
        srv.stop()

    # 10. 停掉后端口应当释放（能重新 bind）
    time.sleep(0.3)
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.close()
        released = True
    except OSError:
        released = False
    check("停止后端口已释放", released)

    print("\n结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
