"""「手机远控」窗口的截图自查工具（开发期用，不参与打包）。

用法：python tools/shot_remote.py shots/remote.png
会启动主界面 → 点开手机远控 → 截图（含独立 Toplevel 弹窗）→ 退出。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import ImageGrab

from ui.app import App


def dpi_aware():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/remote.png"
    dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.attributes("-topmost", True)

    def open_dialog():
        # 只绑回环：截图时别触发 Windows 防火墙询问（那个系统弹窗会盖住界面）
        app.remote_server.host = "127.0.0.1"
        app.on_remote()
        root.after(1400, grab)

    def grab():
        root.update_idletasks()
        root.update()
        boxes = []
        for w in (root, getattr(app._remote_dialog, "top", None)):
            if w is None:
                continue
            try:
                boxes.append((w.winfo_rootx(), w.winfo_rooty(),
                              w.winfo_rootx() + w.winfo_width(),
                              w.winfo_rooty() + w.winfo_height()))
            except Exception:
                pass
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        img = ImageGrab.grab(bbox=(x0, y0, x1, y1))
        img.save(out)
        print("saved", out, img.size)
        print("running =", app.remote_server.running, "url =", app.remote_server.url())
        print("code =", app.remote_hub.code)
        root.after(200, root.destroy)

    root.after(900, open_dialog)
    root.mainloop()


if __name__ == "__main__":
    main()
