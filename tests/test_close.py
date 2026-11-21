"""关闭流程的回归测试（v1.4.1）。

锁定的一件事：**点「×」不能占住 GUI 主线程**。
旧实现直接在主线程里 `close_browser()`（等 Edge 优雅退出，几秒起步），
Windows 会把窗口标成「未响应」。现在要求：

1. `on_close()` 返回后窗口立刻 withdrawn（视觉上已关闭）；
2. 收尾期间主线程仍在跑 after 回调（不卡）；
3. 走的是 `runner.shutdown()`，不再从主线程碰 `close_browser()`；
4. 收尾完成后窗口才真正 destroy。

不联网、不启动浏览器：把 `runner.shutdown` 换成可控的假实现。
运行：python tests/test_close.py
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 与真实启动一致，否则坐标是虚拟的
except Exception:
    pass

from ui.app import App
import ui.widgets as W

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


def scenario(slow):
    # 每个场景新建一个 Tk 解释器，字体缓存里存的是上一个解释器的 Font 对象，
    # 不清掉会在 measure 时报「application has been destroyed」（仅测试环境会遇到）
    W._FONTS.clear()
    root = tk.Tk()
    app = App(root)
    root.update()

    calls = {"shutdown": 0, "close_browser": 0}

    def fake_shutdown(wait=8.0):
        calls["shutdown"] += 1
        if slow:
            time.sleep(1.0)

    def fake_close_browser():
        calls["close_browser"] += 1

    app.runner.shutdown = fake_shutdown
    app.runner.close_browser = fake_close_browser

    ticks = {"n": 0, "stop": False}
    job = {"id": None}

    def tick():
        if ticks["stop"]:
            return
        ticks["n"] += 1
        job["id"] = root.after(40, tick)

    root.after(40, tick)

    state = {}

    def go():
        state["t0"] = time.time()
        app.on_close()
        state["state"] = root.state()
        state["ticks_at_close"] = ticks["n"]

    # 销毁前先掐掉测试自己的计时器：否则 destroy 之后 Tk 还会去调这个已失效的
    # 回调，往 stderr 打「invalid command name ...tick」，干扰输出。
    orig_pump = app._pump_shutdown

    def pump_then_cleanup():
        done = app._shutdown_done is None or app._shutdown_done.is_set()
        if done or time.time() >= app._shutdown_deadline:   # 只有真要销毁时才掐计时器
            ticks["stop"] = True
            if job["id"] is not None:
                try:
                    root.after_cancel(job["id"])
                except Exception:
                    pass
                job["id"] = None
        orig_pump()

    app._pump_shutdown = pump_then_cleanup

    def on_destroy(e):
        if e.widget is root:
            ticks["stop"] = True
            state.setdefault("t_end", time.time())

    root.after(120, go)
    root.bind("<Destroy>", on_destroy)
    root.mainloop()

    state.setdefault("t_end", time.time())
    elapsed = state["t_end"] - state["t0"]
    during = ticks["n"] - state["ticks_at_close"]
    return state, elapsed, during, calls


print(f"[场景 1] 收尾很快（无浏览器）")
st, dt, during, calls = scenario(slow=False)
check("on_close 后窗口已收起", st.get("state") == "withdrawn", st.get("state"))
check("0.6 秒内真正退出", dt < 0.6, f"{dt:.2f}s")
check("走的是 shutdown 而不是 close_browser",
      calls["shutdown"] == 1 and calls["close_browser"] == 0, str(calls))

print(f"[场景 2] 收尾要 1 秒（模拟关 Edge）")
st, dt, during, calls = scenario(slow=True)
check("on_close 后窗口已收起", st.get("state") == "withdrawn", st.get("state"))
check("等到收尾完成才 destroy", 1.0 <= dt < 2.0, f"{dt:.2f}s")
check("收尾期间主线程仍在跑事件循环", during >= 10, f"{during} 次")
check("未调用 close_browser", calls["close_browser"] == 0, str(calls))

print()
if FAILS:
    print("FAILED:", ", ".join(FAILS))
    sys.exit(1)
print("ALL PASS")
