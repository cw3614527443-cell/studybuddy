"""按 Win32 窗口矩形截 StudyBuddy（开发期工具）。

要点：这台机器是「主屏 2560 + 扩展区」的虚拟桌面，Tk 按屏幕总宽居中，
窗口常常落在主屏右侧之外。所以一律按虚拟桌面全图抓，再按真实矩形裁。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

u = ctypes.windll.user32


def app_window():
    hs = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value.startswith("StudyBuddy"):
                hs.append(h)
        return True

    u.EnumWindows(cb, 0)
    return hs[0] if hs else None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/win.png"
    h = app_window()
    if not h:
        print("no window")
        return 1
    u.SetForegroundWindow(h)
    time.sleep(0.2)
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    x0 = u.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
    y0 = u.GetSystemMetrics(77)
    from PIL import ImageGrab
    im = ImageGrab.grab(all_screens=True)
    box = (r.left - x0, r.top - y0, r.right - x0, r.bottom - y0)
    im.crop(box).save(out)
    print("saved", out, im.size, "win", r.left, r.top, r.right, r.bottom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
