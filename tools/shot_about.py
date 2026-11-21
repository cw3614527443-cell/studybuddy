"""「关于此软件」窗口截图工具：打开主界面 → 打开关于窗口 → 截图后退出。

用法：
    python tools/shot_about.py 输出.png  [about|log] [宽] [高]
第三个参数决定截哪一页（默认 about），用于开发期检查排版与换行。
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
    out = sys.argv[1] if len(sys.argv) > 1 else "shot_about.png"
    page = sys.argv[2] if len(sys.argv) > 2 else "about"
    dpi_aware()
    root = tk.Tk()
    App(root)
    root.attributes("-topmost", True)

    from ui.about import AboutDialog
    dlg = AboutDialog(root)

    def cap():
        root.update_idletasks()
        root.update()
        if page == "log":
            dlg._show("log")
            root.update_idletasks()
            root.update()
        dlg.top.attributes("-topmost", True)
        root.update_idletasks()
        root.update()
        top = dlg.top
        x, y = top.winfo_rootx(), top.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + top.winfo_width(),
                             y + top.winfo_height())).save(out)
        dlg.close()
        root.destroy()

    root.after(700, cap)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
