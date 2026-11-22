"""第四遍自检：边界与失败路径（故障注入，不联网、不登录、不开浏览器）。

覆盖：忙时拒单、停止信号、未登录提示、课程名错误、大模型未配置、
关闭/退出的空转安全、异常后任务线程恢复、错误文案翻译。
"""
import os
import sys
import threading
import time
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.events import EventBus
from core.tasks import TaskRunner
from core.platforms.chaoxing_adapter import ChaoxingSuite

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ((" :: " + str(extra)) if extra else ""))


class Recorder(EventBus):
    """把日志留在内存里，便于断言提示文案。"""

    def __init__(self):
        super().__init__()
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, str(msg)))
        super().log(msg, level)

    def has(self, needle):
        return any(needle in m for _, m in self.lines)


def t01_busy_reject():
    bus = Recorder()
    r = TaskRunner(bus)
    gate = threading.Event()
    fin = threading.Event()

    def slow():
        gate.wait(5)
        return "done"

    r.on_finish = lambda: fin.set()
    assert r.start(slow)
    time.sleep(0.05)
    check("忙时拒绝第二个任务", r.start(lambda: "x") is False)
    check("忙时给出明确日志", bus.has("已有任务在运行"))
    gate.set()
    fin.wait(3)
    r.thread.join(3)
    check("任务结束后可再次启动", r.is_running() is False)


def t02_stop_signal():
    bus = Recorder()
    check("停止信号初始未置位", bus.should_stop() is False)
    bus.request_stop()
    check("停止信号可置位", bus.should_stop() is True)
    bus.reset_stop()
    check("停止信号可复位", bus.should_stop() is False)

    sd = os.path.join(ROOT, "chaoxing")
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import client
    client.set_stop_check(bus.should_stop)
    bus.request_stop()
    check("停止信号穿透到套件 client.stopped", client.stopped() is True)
    client.set_stop_check(None)
    check("停止钩子可解除（关闭流程依赖）", client.stopped() is False)
    bus.reset_stop()


def t03_unknown_course():
    sd = os.path.join(ROOT, "chaoxing")
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import run
    fake = type("B", (), {"js": lambda self, *a, **k: "return ''",
                          "goto": lambda self, *a, **k: None,
                          "html": lambda self, *a, **k: "",
                          "click": lambda self, *a, **k: None})()
    with patch.object(run, "list_courses", return_value=[
            {"name": "大学语文", "url": "u1", "done": 0, "total": 5, "cid": "1"}]):
        try:
            run.cmd_open(fake, "不存在的课程")
            got = None
        except RuntimeError as e:
            got = str(e)
    check("课程名不存在时明确报错", bool(got and "没找到课程" in got), got)
    check("错误提示引导重新扫描", bool(got and "重新扫描" in got), got)


def t04_not_logged_in_scan():
    bus = Recorder()
    cx = ChaoxingSuite(bus)
    with patch.object(cx, "list_courses", side_effect=RuntimeError(
            "学习通没读到课程列表，可能还没登录。请点「① 启动浏览器 / 登录」扫码")):
        from core.tasks import TaskRunner as TR
        r = TR(bus)
        r.cx = cx
        done = threading.Event()
        r.on_finish = done.set
        r.start(lambda: r.action_list_courses("cx"))
        done.wait(3)
        r.thread.join(2)
    check("未登录扫描不静默成功", bus.has("学习通没读到课程列表"))
    check("未登录提示给出下一步动作", bus.has("启动浏览器 / 登录"))


def t05_llm_not_configured():
    bus = Recorder()
    cx = ChaoxingSuite(bus)
    with patch.dict(os.environ, {}, clear=False):
        try:
            with patch("config.llm_config", return_value={"base": "", "key": "", "model": ""}):
                cx.answer_quiz("任意课程")
            got = None
        except RuntimeError as e:
            got = str(e)
    check("大模型未配置时拒绝答题", bool(got and "大模型设置" in got), got)


def t06_empty_close_and_shutdown():
    bus = Recorder()
    r = TaskRunner(bus)
    with patch("core.tasks.EdgeProc.kill") as kill:
        r.close_browser()
        r.shutdown(wait=0.01)
    check("无浏览器时关闭不报错", True)
    check("关闭与退出都清理 Edge", kill.call_count >= 2)
    check("cx 未启动时关闭安全", r.cx is None)


def t07_error_explain():
    cases = [
        ("TargetClosedError: page closed", "浏览器连接已断开"),
        ("Executable doesn't exist at ...", "找不到 Edge"),
        ("ECONNREFUSED 127.0.0.1:9222", "无法连接"),
        ("Timeout 30000ms exceeded while goto", "页面加载超时"),
    ]
    ok = all(word in TaskRunner._explain(Exception(raw)) for raw, word in cases)
    check("常见异常都翻译成人话", ok)


def t08_exception_recovers():
    bus = Recorder()
    r = TaskRunner(bus)
    fin = threading.Event()
    r.on_finish = fin.set
    r.start(lambda: (_ for _ in ()).throw(ValueError("故意炸")))
    fin.wait(3)
    r.thread.join(2)
    check("任务异常触发 done 回执", bus.q.qsize() >= 2)
    check("异常后 runner 不卡死", r.is_running() is False)


if __name__ == "__main__":
    for fn in (t01_busy_reject, t02_stop_signal, t03_unknown_course,
               t04_not_logged_in_scan, t05_llm_not_configured,
               t06_empty_close_and_shutdown, t07_error_explain, t08_exception_recovers):
        print("\n== " + fn.__name__ + " ==")
        fn()
    print("\n第4遍自检：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("ALL PASS")
