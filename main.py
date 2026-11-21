"""StudyBuddy 入口。

支持两种模式：
- 正常启动：图形界面
- 自检模式：`StudyBuddy.exe --selftest`（或 python main.py --selftest）
  无头启动浏览器并打开站点，验证打包产物中 playwright 与 Edge 是否可用。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_selftest():
    """无头自检：验证 playwright 驱动 + 系统 Edge 是否可用。结果写入文件便于窗口模式查看。"""
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest_result.txt")
    lines = []

    def out(s):
        lines.append(str(s))
        try:
            print(s)
        except Exception:
            pass

    out("[selftest] 开始")
    rc = 0
    try:
        from core.browser import Browser
        from core.events import EventBus
        bus = EventBus()
        br = Browser(bus)
        # v1.0.1：Edge 改为独立进程 + CDP 连接；导航单独发，避免和 SSO 跳转撞车
        br.connect(launch=True, headless=True)
        br.goto("https://ai.icve.com.cn/app/my-excellent-home")
        br.wait(2500)
        url = br.current_url()
        title = br.page.title()
        out(f"[selftest] URL={url[:70]}")
        out(f"[selftest] TITLE={title[:40]}")
        shot = br.shot("selftest")
        out(f"[selftest] SHOT={shot}")
        # 验证连接可断开、浏览器仍在（这是 v1.0.1 修复的核心行为）
        br.detach()
        out(f"[selftest] DETACH_OK 端口仍在={__import__('core.browser', fromlist=['EdgeProc']).EdgeProc.is_running()}")
        br.close()
        out("[selftest] OK")
    except Exception as e:
        import traceback
        out(traceback.format_exc())
        out(f"[selftest] FAIL: {e}")
        rc = 1
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    return rc


def run_gui():
    import tkinter as tk
    from ui.app import App
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


def main():
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    run_gui()


if __name__ == "__main__":
    main()
