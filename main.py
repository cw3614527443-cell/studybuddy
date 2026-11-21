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
    # v1.1.0：结果写到数据目录（logs/），不写程序目录 ——
    # 安装版装在 %LOCALAPPDATA%\Programs 时程序目录未必可写。
    try:
        from config import LOGS_DIR
        log_path = os.path.join(LOGS_DIR, "selftest_result.txt")
    except Exception:
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


def enable_dpi_awareness():
    """让程序自己按屏幕 DPI 渲染，而不是交给系统做位图拉伸。

    v1.2.0：不声明 DPI 感知时，Windows 会把 96dpi 渲染出的窗口整体放大 125%，
    文字发虚。声明之后 Tk 拿到真实 DPI，ui/widgets.py 里的 s() 负责按比例放大尺寸。
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 系统级 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()        # Win7 兜底
    except Exception:
        pass


def run_gui():
    import tkinter as tk
    enable_dpi_awareness()
    from ui.app import App
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


def run_uishot(out_path):
    """渲染界面、截图、退出。用于验证打包产物里的界面是否正常（不连浏览器）。"""
    import tkinter as tk
    from PIL import ImageGrab
    from ui.app import App
    enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.attributes("-topmost", True)

    def cap():
        root.update_idletasks()
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(),
                             y + root.winfo_height())).save(out_path)
        root.destroy()

    root.after(800, cap)
    root.mainloop()
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    if "--uishot" in sys.argv:
        i = sys.argv.index("--uishot")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else "uishot.png"
        sys.exit(run_uishot(out))
    run_gui()


if __name__ == "__main__":
    main()
