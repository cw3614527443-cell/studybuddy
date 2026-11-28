"""手机远控链路自查（开发期，不打包）。

验证的是「手机点一下 → 电脑真的收到指令」这条链：
  Playwright 以 iPhone 视口打开页面 → 输配对码登录 → 点按钮
  → HTTP /api/action → hub.pending → GUI 主线程 _pump_remote → _remote_action

为了不真的去开浏览器，这里把 App._remote_action 换成一个记录器，
只断言「收到的动作名与参数对得上」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import tkinter as tk

from ui.app import App

got = []


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/e2e_phone.png"
    root = tk.Tk()
    app = App(root)
    app.remote_server.host = "127.0.0.1"
    app.remote_server.port = 18767
    app.remote_hub.port = 18767

    # 记录器：替代真实执行，避免拉起浏览器
    app._remote_action = lambda name, params: got.append((name, params))

    if not app.remote_server.start():
        print("start fail:", app.remote_server.error)
        return 1
    app.remote_hub.feed("courses", {"platform": "ai", "items": [
        {"name": "形势与政策", "note": "刷完", "done": 4, "total": 4,
         "brushable": True, "finished": True},
        {"name": "人工智能通识课", "note": "可刷", "done": 5, "total": 12,
         "brushable": True, "finished": False},
    ]})
    base = "http://127.0.0.1:18767"
    code = app.remote_hub.code
    print("serve", base, "code", code)

    result = {}

    def drive():
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as pw:
                br = pw.chromium.launch(channel="msedge", headless=True)
                ctx = br.new_context(viewport={"width": 390, "height": 844},
                                     device_scale_factor=2, is_mobile=True,
                                     has_touch=True, locale="zh-CN")
                page = ctx.new_page()
                page.goto(base + "/#c=" + code, wait_until="domcontentloaded")
                page.wait_for_timeout(400)
                result["prefill"] = page.input_value("#code")
                page.screenshot(path=out.replace(".png", "_login.png"))
                page.click("#go")
                page.wait_for_timeout(1200)
                result["login_display"] = page.evaluate(
                    "() => getComputedStyle(document.querySelector('#login')).display")
                result["app_hidden"] = page.evaluate(
                    "() => document.querySelector('#app').hidden")

                # 选第一门课再刷，验证 params.course 能带过去
                result["courses"] = page.eval_on_selector_all(
                    ".course .nm", "els => els.map(e => e.textContent)")
                if result["courses"]:
                    page.click(".course")
                page.click("#b-scan")
                page.wait_for_timeout(500)
                page.click("#b-brush")
                page.wait_for_timeout(500)
                page.click("#b-watch")
                page.wait_for_timeout(500)
                page.screenshot(path=out, full_page=True)
                br.close()
        except Exception as e:
            import traceback
            traceback.print_exc()
            result["error"] = str(e)
        finally:
            root.after(300, root.destroy)

    root.after(600, lambda: threading.Thread(target=drive, daemon=True).start())
    root.mainloop()

    print("prefill code =", result.get("prefill"))
    print("login display after login =", result.get("login_display"))
    print("app hidden =", result.get("app_hidden"))
    print("courses on phone =", result.get("courses"))
    print("actions received =", got)
    ok = (result.get("prefill") == code
          and result.get("login_display") == "none"
          and result.get("app_hidden") is False
          and [a[0] for a in got] == ["scan", "brush", "watch"])
    print("RESULT =", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
