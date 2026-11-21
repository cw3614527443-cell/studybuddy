"""事件总线：后台任务通过它把日志/进度/截图推给 GUI 主线程。

GUI（tkinter）不是线程安全的，所有界面更新必须回到主线程。
TaskRunner 在后台线程运行，这里用线程安全的 queue 传递消息。
"""
import queue
import threading
import time


class EventBus:
    def __init__(self):
        self.q = queue.Queue()
        self._stop = threading.Event()

    # ---- 后台线程调用 ----
    def log(self, msg, level="info"):
        self.q.put(("log", {"time": time.strftime("%H:%M:%S"), "msg": str(msg), "level": level}))

    def progress(self, value, text=""):
        """value: 0-100 或 None（表示不确定进度）。"""
        self.q.put(("progress", {"value": value, "text": text}))

    def status(self, text):
        self.q.put(("status", {"text": text}))

    def shot(self, path):
        self.q.put(("shot", {"path": path}))

    def done(self, ok=True, summary=""):
        self.q.put(("done", {"ok": ok, "summary": summary}))

    def request_stop(self):
        self._stop.set()

    def should_stop(self):
        return self._stop.is_set()

    def reset_stop(self):
        self._stop.clear()

    # ---- GUI 主线程调用（非阻塞） ----
    def poll(self, max_items=200):
        items = []
        for _ in range(max_items):
            try:
                items.append(self.q.get_nowait())
            except queue.Empty:
                break
        return items
