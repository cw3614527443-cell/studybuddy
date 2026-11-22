"""课程下拉弹层的视觉/行为截图工具（开发期自查用，不参与打包）。

用法：D:\\python\\python.exe tools\\shot_picker.py out.png [dy]

会做三件事并截图：
1. 铺一组带「刷完 / 可刷 / 无课 / 未确认」标记的课程，打开弹层；
2. 滚轮往下滚 3 格再截第二张（验证滚动范围被夹紧、不会推出空白）；
3. 把窗口往上挪 dy 像素（默认 -140），验证弹层跟着输入框走。

产物：out.png（打开态）、out_scrolled.png、out_moved.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import ImageGrab

from ui import widgets as W
from ui.app import App


def dpi_aware():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


DEMO = [
    {"name": "★ 全部可刷课程（5 门）", "label": "★ 全部可刷课程（5 门）", "brushable": True,
     "tag": "批量", "note": "全部"},
    {"name": "形势与政策", "brushable": True, "done": 4, "total": 4, "finished": True,
     "note": "刷完"},
    {"name": "人工智能通识课（2026-2027-1）", "brushable": True, "done": 3, "total": 12,
     "finished": False, "note": "可刷"},
    {"name": "大学生心理健康", "brushable": True, "done": 0, "total": 9, "finished": False,
     "note": "可刷"},
    {"name": "大学英语（1）", "brushable": False, "done": 0, "total": 0, "note": "无课"},
    {"name": "Linux操作系统", "brushable": False, "done": 0, "total": 0, "note": "无课"},
    {"name": "人工智能伦理与安全", "brushable": False, "done": 0, "total": 0, "note": "无课"},
    {"name": "职业发展与就业指导（1）", "brushable": False, "done": 0, "total": 0,
     "note": "无课"},
    {"name": "程序设计基础", "unknown": True, "brushable": True, "note": "未确认"},
    {"name": "思想道德与法治", "brushable": True, "done": 7, "total": 7, "finished": True,
     "note": "刷完"},
]


def grab(root, path):
    root.update_idletasks()
    root.update()
    # 弹层是独立的 overrideredirect Toplevel，容易被别的窗口盖住 —— 截屏前抬到最上层，
    # 否则抓到的只是桌面（开发期工具，只在截图期间调整层级，不影响产品行为）。
    pop = getattr(getattr(root, "course_picker", None), "_popup", None)
    if pop is not None and pop.winfo_exists():
        try:
            pop.lift()
            pop.attributes("-topmost", True)
        except Exception:
            pass
        root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    img = ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height() + 420))
    img.save(path)
    print("saved", path, img.size)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot_picker.png"
    dy = int(sys.argv[2]) if len(sys.argv) > 2 else -140
    dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.attributes("-topmost", True)
    app._on_courses({"platform": "ai", "items": DEMO})

    events = []

    def step_open():
        app.course_picker._toggle(None)
        events.append(("open", app.course_picker.is_open()))
        # 打开时应该停在顶部
        root.after(260, step_scroll)

    def step_scroll():
        pop = app.course_picker._popup
        if pop is None:
            events.append(("popup_gone", True))
            return
        # 用滚轮往下滚 3 格，再往上滚 6 格（应被夹在顶部，不能出现空白）
        for _ in range(3):
            pop.event_generate("<MouseWheel>", delta=-120)
        root.update_idletasks()
        grab(root, out.replace(".png", "_scrolled.png"))
        for _ in range(8):
            pop.event_generate("<MouseWheel>", delta=120)
        root.update_idletasks()
        canvas_top = None
        try:
            canv = [w for w in pop.winfo_children()[0].winfo_children()
                    if w.winfo_class() == "Canvas"]
            canvas_top = canv[0].yview()[0] if canv else None
        except Exception:
            pass
        events.append(("yview_top_after_up", canvas_top))
        root.after(200, step_move)

    def step_move():
        cur_y = root.winfo_rooty()
        root.geometry(f"+{root.winfo_rootx()}+{max(0, cur_y + dy)}")
        root.update_idletasks()
        root.update()
        # 等跟随轮询跑几轮（120ms 一次），再量位置
        root.after(700, measure)

    def measure():
        pop = app.course_picker._popup
        px, py = app.course_picker.winfo_rootx(), app.course_picker.winfo_rooty()
        ph = app.course_picker.winfo_height()
        events.append(("picker", (px, py, ph)))
        if pop is not None:
            events.append(("popup", (pop.winfo_rootx(), pop.winfo_rooty())))
            events.append(("gap", pop.winfo_rooty() - (py + ph)))
        grab(root, out.replace(".png", "_moved.png"))
        root.after(120, step_close)

    def step_close():
        app.course_picker.close()
        events.append(("closed", not app.course_picker.is_open()))
        # 关掉后主窗口截图（验证没有残留窗口）
        grab(root, out)
        for e in events:
            print("  ", e)
        root.destroy()

    root.after(700, step_open)
    root.mainloop()


if __name__ == "__main__":
    main()
