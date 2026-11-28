"""手机页面截图（开发期自查，不参与打包）。

用法：python tools/shot_phone.py shots/phone.png

起一个只绑回环的远程服务，塞几条假数据，用 Edge 以 iPhone 尺寸打开并截图：
- <out>_login.png  登录页
- <out>.png        登录后的主界面（长图）

不联网、不开真实浏览器会话，只借 Playwright 渲染本地页面。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.remote import RemoteHub, RemoteServer


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/phone.png"
    hub = RemoteHub(port=18766)
    for name in ("scan", "brush", "read", "watch", "stop"):
        hub.handlers[name] = name

    # 假数据：让截图能看出「刷完 / 可刷 / 无课 / 未确认」四态与日志配色
    hub.feed("status", {"text": "刷课中：正在翻第 12/48 页"})
    hub.feed("progress", {"value": 42, "text": "《人工智能通识课》第 2 章"})
    hub.feed("log", {"time": "19:42:01", "msg": "打开课件《第2章 机器学习概述》", "level": "info"})
    hub.feed("log", {"time": "19:42:14", "msg": "检测到新版阅读器，开始逐页翻（1.4 秒/页）",
                     "level": "info"})
    hub.feed("log", {"time": "19:42:39", "msg": "第 11 页加载较慢，已多等 0.8 秒", "level": "warn"})
    hub.feed("log", {"time": "19:42:52", "msg": "进度 42%", "level": "ok"})
    hub.feed("courses", {"platform": "ai", "items": [
        {"name": "形势与政策", "note": "刷完", "done": 4, "total": 4,
         "brushable": True, "finished": True},
        {"name": "人工智能通识课（2026-2027-1）", "note": "可刷", "done": 5, "total": 12,
         "brushable": True, "finished": False},
        {"name": "大学生心理健康", "note": "可刷", "done": 0, "total": 9,
         "brushable": True, "finished": False},
        {"name": "大学英语（1）", "note": "无课", "done": 0, "total": 0,
         "brushable": False, "finished": False},
        {"name": "程序设计基础", "note": "未确认", "done": 0, "total": 0,
         "brushable": True, "unknown": True},
    ]})
    # 借一张真实截图当「当前画面」，路径必须落在 SHOTS_DIR 里才能被 /api/shot 读到
    src = os.path.join("shots", "remote_dialog.png")
    if os.path.exists(src):
        os.makedirs(config.SHOTS_DIR, exist_ok=True)
        dst = os.path.join(config.SHOTS_DIR, "phone_demo.png")
        shutil.copy2(src, dst)
        hub.feed("shot", {"path": dst})

    srv = RemoteServer(hub, host="127.0.0.1", port=hub.port)
    if not srv.start():
        print("启动失败：", srv.error)
        return 1
    base = "http://127.0.0.1:%d" % hub.port
    print("serve", base, "code", hub.code)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("没有 Playwright，跳过截图：", e)
        srv.stop()
        return 0

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="msedge", headless=True)
            ctx = br.new_context(viewport={"width": 390, "height": 844},
                                 device_scale_factor=2, is_mobile=True,
                                 has_touch=True, locale="zh-CN")
            page = ctx.new_page()
            page.goto(base + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            page.screenshot(path=out.replace(".png", "_login.png"))
            print("saved", out.replace(".png", "_login.png"))

            # 直接拿口令换 token 塞进 localStorage，跳过手输
            token = page.evaluate(
                "async (code) => {const r = await fetch('/api/login',{method:'POST',"
                "headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});"
                "const j = await r.json(); return j.token || '';}",
                hub.code)
            page.evaluate("(t) => localStorage.setItem('sb_token', t)", token)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(1600)
            page.screenshot(path=out)                     # 首屏（真实观感）
            page.screenshot(path=out.replace(".png", "_full.png"), full_page=True)
            print("saved", out)
            print("saved", out.replace(".png", "_full.png"))
            print("login hidden =", page.evaluate("() => document.querySelector('#login').hidden"))
            print("login display =", page.evaluate(
                "() => getComputedStyle(document.querySelector('#login')).display"))
            br.close()
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
