"""界面截图工具：启动 GUI、塞几条示例日志、截图存文件后自动退出。

用法：D:\\python\\python.exe tools\\shot_ui.py out.png
仅用于开发期看视觉效果，不参与打包。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import ImageGrab

from ui.app import App


def dpi_aware():
    """截图进程也要声明 DPI 感知，否则 winfo_* 是虚拟坐标，截出来是放大的模糊图。"""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def window_rect(root):
    x, y = root.winfo_rootx(), root.winfo_rooty()
    return (x, y, x + root.winfo_width(), y + root.winfo_height())


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot_ui.png"
    dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.attributes("-topmost", True)

    demo = [
        ("17:00:48", "启动受控浏览器（首次需在弹出的窗口里登录）…", "info"),
        ("17:00:50", "Edge 已拉起（PID 21124），等待调试端口就绪…", "info"),
        ("17:00:53", "已连接到浏览器", "ok"),
        ("17:00:54", "已打开：https://ai.icve.com.cn/app/my-excellent-home", "info"),
        ("17:01:22", "观看时长 5.36 时，学习次数 208", "info"),
        ("17:01:23", "时长数字没变化：平台按分钟结算，稍后点②再看", "warn"),
        ("17:01:40", "进入课程「大学生心理健康」失败，无法复核", "error"),
    ]
    for t, msg, lv in demo:
        app._append_log({"time": t, "msg": msg, "level": lv})
    app.prog.set_value(38)
    app.prog_var.set("正在处理第 12/31 个资源：3.2 压力与应对 · 视频")
    app.pct_label.config(text="38%")
    app._set_status("运行中…", "run")

    def cap():
        root.update_idletasks()
        root.update()
        x, y, x2, y2 = window_rect(root)
        img = ImageGrab.grab(bbox=(x, y, x2, y2))
        img.save(out)
        print("saved", out, img.size, "scale", app.root.tk.call("tk", "scaling"))
        root.destroy()

    root.after(900, cap)
    root.mainloop()


if __name__ == "__main__":
    main()
