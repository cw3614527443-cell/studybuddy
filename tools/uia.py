"""以「真人操作」的方式跑软件：把主界面开到固定位置，并接受外部下发的点击指令。

为什么要它：tkinter 是自绘的，外部进程拿不到控件树；而外部进程的 DPI 上下文
和本进程不一定一致，按坐标点会飘。所以点击统一在这里做 —— 坐标和截图都出自
同一个 DPI 上下文，按控件文字命中，不靠猜坐标。

用法（外部）：
    echo {"action":"click","text":"扫描课程"} > shots/cmd.json
    echo {"action":"shot","path":"shots/a.png"} > shots/cmd.json
    echo {"action":"type","text":"abc"}          > shots/cmd.json
    echo {"action":"key","name":"Return"}        > shots/cmd.json
    echo {"action":"wait","seconds":3}           > shots/cmd.json
处理完会删掉 cmd.json，并把结果追加进 shots/uia_log.txt。
"""
import ctypes
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMD = os.path.join(BASE, "shots", "cmd.json")
DUMP = os.path.join(BASE, "shots", "uia.json")
LOG = os.path.join(BASE, "shots", "uia_log.txt")
WIN_W, WIN_H = 1400, 975


def log(s):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def text_of(w):
    try:
        return str(w.cget("text") or "")
    except Exception:
        return ""


def canvas_items(w, path, out):
    try:
        ids = w.find_all()
    except Exception:
        return
    for it in ids:
        try:
            if w.type(it) != "text":
                continue
            t = str(w.itemcget(it, "text") or "").strip()
            if not t:
                continue
            bb = w.bbox(it)
            if not bb:
                continue
            x0, y0, x1, y1 = bb
            rx, ry = w.winfo_rootx(), w.winfo_rooty()
            out.append({"cls": "CanvasItem", "text": t,
                        "x": rx + x0, "y": ry + y0,
                        "w": x1 - x0, "h": y1 - y0,
                        "cx": rx + (x0 + x1) // 2, "cy": ry + (y0 + y1) // 2,
                        "win": path})
        except Exception:
            continue


def walk(w, path, out):
    try:
        cls = w.winfo_class()
        ww, hh = w.winfo_width(), w.winfo_height()
        if ww > 1 and hh > 1:
            x, y = w.winfo_rootx(), w.winfo_rooty()
            out.append({"cls": cls, "text": text_of(w),
                        "x": x, "y": y, "w": ww, "h": hh,
                        "cx": x + ww // 2, "cy": y + hh // 2, "win": path})
            if cls == "Canvas":
                canvas_items(w, path, out)
    except Exception:
        pass
    for k in (w.winfo_children() if hasattr(w, "winfo_children") else []):
        walk(k, path, out)


def main():
    dpi()
    import tkinter as tk
    from ui.app import App

    root = tk.Tk()
    app = App(root)
    root.geometry("%dx%d+0+0" % (WIN_W, WIN_H))
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.attributes("-topmost", True)

    state = {"items": []}

    def toplevels():
        # 课程下拉的弹层是 CoursePicker 的子 Toplevel，不在 root 的直接 children
        # 里。只遍历 root.winfo_children() 会漏掉它，看起来像「下拉没展开」。
        found = []

        def dig(w, depth=0):
            if depth > 8:
                return
            for c in w.winfo_children():
                if c.winfo_class() == "Toplevel" and c not in found:
                    found.append(c)
                dig(c, depth + 1)

        dig(root)
        return found

    def collect():
        out = []
        walk(root, "main", out)
        for i, t in enumerate(toplevels()):
            walk(t, "top%d" % i, out)
        state["items"] = out
        try:
            with open(DUMP, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def find(text, exact=False):
        # 只认真正可点的控件：说明文字也是 Label，子串匹配会先撞上
        # 「3 扫描课程 → 选课」这种指引，点过去等于没点。
        cands = [it for it in state["items"]
                 if it.get("cls") in ("CanvasItem", "Button")]
        if not cands:
            cands = state["items"]
        for it in cands:
            if (it.get("text") or "") == text:
                return it
        if exact:
            return None
        for it in cands:
            if text in (it.get("text") or ""):
                return it
        return None

    def do_click(it):
        import pyautogui
        pyautogui.moveTo(it["cx"], it["cy"], duration=0.1)
        time.sleep(0.05)
        pyautogui.click()
        return "%s @(%d,%d)" % (it.get("text", "")[:20], it["cx"], it["cy"])

    def handle(cmd):
        act = cmd.get("action")
        if act == "click":
            collect()
            it = find(cmd.get("text") or "")
            if not it:
                return "NOT_FOUND " + str(cmd.get("text"))
            return "click " + do_click(it)
        if act == "clickxy":
            import pyautogui
            pyautogui.click(int(cmd["x"]), int(cmd["y"]))
            return "clickxy %s,%s" % (cmd["x"], cmd["y"])
        if act == "dialog":
            # Windows 原生 messagebox 不在 tkinter 控件树里，位置每次还会变。
            # 用窗口句柄取实际矩形，再模拟鼠标点最右按钮（否/取消/确定）。
            import ctypes
            from ctypes import wintypes
            import pyautogui
            u32 = ctypes.windll.user32
            needles = cmd.get("titles") or ["还没配置大模型", "功能说明", "自动答题",
                                            "先选课程", "参数错误", "确认退出"]
            hit = []

            def visit(hwnd, _):
                if not u32.IsWindowVisible(hwnd):
                    return True
                n = u32.GetWindowTextLengthW(hwnd)
                if not n:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                if not any(k in title for k in needles):
                    return True
                rect = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(rect))
                hit.append((title, rect))
                return False

            proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            u32.EnumWindows(proto(visit), 0)
            if not hit:
                return "NO_DIALOG"
            title, r = hit[0]
            # GetWindowRect 含不可见边框，实测最右「否/取消」按钮中心
            # 约在 right-45 / bottom-38。
            x, y = r.right - 45, r.bottom - 38
            pyautogui.moveTo(x, y, duration=0.1)
            pyautogui.click()
            return "dialog %s @(%d,%d)" % (title, x, y)
        if act == "closewins":
            # 点掉本进程残留的自绘弹窗：只匹配自己 PID 的窗口，
            # 绝不误关桌面上别的应用。模拟鼠标点标题栏右上角 ×。
            import ctypes
            import pyautogui
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            pid = os.getpid()
            needles = cmd.get("titles") or ["手机远控", "大模型设置", "关于此软件", "登录 ·"]
            closed = []

            def visit(hwnd, _):
                if not u32.IsWindowVisible(hwnd):
                    return True
                owner = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value != pid:
                    return True
                n = u32.GetWindowTextLengthW(hwnd)
                if not n:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if not any(k in buf.value for k in needles):
                    return True
                rect = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(rect))
                hit.append((buf.value, rect))
                return False

            proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            for _ in range(6):
                hit = []
                u32.EnumWindows(proto(visit), 0)
                if not hit:
                    break
                title, r = hit[0]
                pyautogui.click(r.right - 20, r.top + 18)
                closed.append(title)
                time.sleep(0.35)
            return "closewins " + ",".join(closed)
        if act == "type":
            import pyautogui
            pyautogui.write(str(cmd.get("text") or ""), interval=0.03)
            return "type"
        if act == "key":
            import pyautogui
            pyautogui.press(str(cmd.get("name") or "Return"))
            return "key " + str(cmd.get("name"))
        if act == "collect":
            collect()
            return "collected %d" % len(state["items"])
        if act == "logtail":
            # 只读：把 GUI 日志末尾给外部驱动看，用来判断后台任务跑到哪一步。
            try:
                lines = app.log.get("1.0", "end").splitlines()
                n = int(cmd.get("n") or 25)
                return "logtail | " + " || ".join(lines[-n:])
            except Exception as e:
                return "logtail ERR %s" % e
        if act == "shot":
            collect()
            from PIL import ImageGrab
            path = cmd.get("path") or "shots/uia_shot.png"
            # 截图前必须自己抢到最前：桌面上有别家的全屏窗口，一不小心就抓到它
            for t in [root] + toplevels():
                try:
                    t.attributes("-topmost", True)
                    t.lift()
                except Exception:
                    pass
            root.update_idletasks()
            root.update()
            time.sleep(0.25)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            w, h = root.winfo_width(), root.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(os.path.join(BASE, path))
            return "shot %s <%d,%d %dx%d>" % (path, x, y, w, h)
        if act == "shotfull":
            from PIL import ImageGrab
            path = cmd.get("path") or "shots/uia_full.png"
            ImageGrab.grab().save(os.path.join(BASE, path))
            return "shotfull " + path
        if act == "wait":
            time.sleep(float(cmd.get("seconds") or 1))
            return "waited"
        return "UNKNOWN " + str(act)

    def loop():
        # 不再 os.remove 指令文件：短时间成百上千次删除会触发系统的批量删除
        # 保护，直接把 GUI 进程杀掉。改成写回 {"done":true,"result":...} 标记，
        # 外部驱动按 id 识别自己的指令已经被处理。
        if os.path.exists(CMD):
            try:
                with open(CMD, encoding="utf-8") as f:
                    cmd = json.load(f)
                if cmd and not cmd.get("done"):
                    try:
                        result = handle(cmd)
                    except Exception as e:
                        result = "ERR %s" % e
                    log(result)
                    with open(CMD, "w", encoding="utf-8") as f:
                        json.dump({"id": cmd.get("id"), "done": True,
                                   "result": result}, f, ensure_ascii=False)
            except Exception:
                pass
        root.after(300, loop)

    root.after(600, collect)
    root.after(900, loop)
    log("READY %dx%d" % (WIN_W, WIN_H))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
