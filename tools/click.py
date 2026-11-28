"""按屏幕坐标点真实按钮（开发期工具）。

为什么不用裸 pyautogui：桌面上还有别的窗口，点击会落到最上层那个。
这里先把这个进程的所有窗口置顶，点完再取消置顶，保证命中目标。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

u = ctypes.windll.user32


def dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def win_list(title_prefix="StudyBuddy"):
    """找出该进程的全部可见窗口（主窗 + 所有 Toplevel 弹窗）。"""
    found = []
    holder = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value.startswith(title_prefix):
                holder.append(h)
        return True

    u.EnumWindows(cb, 0)
    if not holder:
        return []
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(holder[0], ctypes.byref(pid))
    pid_v = pid.value

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb2(h, l):
        p = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid_v and u.IsWindowVisible(h):
            found.append(h)
        return True

    u.EnumWindows(cb2, 0)
    return found


def topmost(hwnds, on=True):
    flag = -1 if on else -2        # HWND_TOPMOST / HWND_NOTOPMOST
    for h in hwnds:
        u.SetWindowPos(h, flag, 0, 0, 0, 0, 0x0001 | 0x0002)   # NOSIZE|NOMOVE


def main():
    dpi()
    x, y = int(sys.argv[1]), int(sys.argv[2])
    pause = float(sys.argv[3]) if len(sys.argv) > 3 else 0.35
    hs = win_list()
    if not hs:
        print("未找到 StudyBuddy 窗口")
        return 1
    topmost(hs, True)
    time.sleep(0.3)
    import pyautogui
    pyautogui.moveTo(x, y, duration=0.12)
    time.sleep(0.08)
    pyautogui.click()
    time.sleep(pause)
    topmost(hs, False)
    print("click", x, y, "in", len(hs), "windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
