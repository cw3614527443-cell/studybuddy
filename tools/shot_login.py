"""登录弹窗截图工具（开发期自检用，不参与打包）。

用法：D:\\python\\python.exe tools/shot_login.py out.png [ai|cx|zjy2] [password|manual]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import ImageGrab

import ui.widgets as W
from ui.app import App
from ui.login_dialog import LoginDialog


def dpi_aware():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot_login.png"
    platform = sys.argv[2] if len(sys.argv) > 2 else "ai"
    mode = sys.argv[3] if len(sys.argv) > 3 else "password"

    dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.attributes("-topmost", True)
    app._on_platform_change(platform)

    dlg = LoginDialog(root, platform, on_submit=lambda c: print("submit", c))
    dlg.seg.set(mode)
    dlg._on_mode(mode)
    if mode == "password":
        dlg.user_var.set("13800138000")
        dlg.pwd_var.set("demo-password")
    dlg.top.attributes("-topmost", True)
    dlg.top.update_idletasks()

    def cap():
        root.update_idletasks()
        root.update()
        dlg.top.update_idletasks()
        dlg.top.update()
        x, y = dlg.top.winfo_rootx(), dlg.top.winfo_rooty()
        w, h = dlg.top.winfo_width(), dlg.top.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(out)
        print("saved", out, img.size, "dialog", w, h, "scale", W.s(1))
        dlg.close()
        root.destroy()

    root.after(1000, cap)
    root.mainloop()


if __name__ == "__main__":
    main()
