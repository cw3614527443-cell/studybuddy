"""批量刷课与「进入课程」回归测试（v1.7.0）。

两个真实缺陷在这里钉住：

1. zjy2 的刷课 / 读进度以前从不调用 enter_course，刷的是「浏览器当前停留的那门课」。
   换一门课就会刷错课、或读到上一门课的目录。
2. 替别人刷课时一账号十几门课，只能一门门点。新增「全部可刷课程」批量模式，
   只刷扫描结果里标了「可刷」的，无课跳过；中途停止要能立刻停。

跑法：python tests/test_batch.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.tasks as T  # noqa: E402
from core.events import EventBus  # noqa: E402

ok_count = 0
bad = []


def check(name, cond, extra=""):
    global ok_count
    if cond:
        ok_count += 1
        print("  ok   " + name)
    else:
        bad.append(name)
        print("  FAIL " + name + ((" :: " + str(extra)) if extra else ""))
    return cond


class Bus(EventBus):
    def __init__(self):
        super().__init__()
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, str(msg)))

    def progress(self, v, t=""):
        pass

    def text(self):
        return "\n".join(m for _, m in self.lines)


def make_runner():
    bus = Bus()
    r = T.TaskRunner(bus)
    return r, bus


class FakeBrowser:
    """刷课会装静音/弹窗守卫，测试里给个空实现。"""

    def install_guards(self, prefer_resume=True, mute=True):
        return None

    def release_guards(self):
        return None

    def current_url(self):
        return "https://zjy2.icve.com.cn/study/v2/course"

    def wait(self, ms=0):
        return None


class FakeZjy2:
    """记录是否被要求进入课程，以及刷的是哪门课。"""
    instances = []

    def __init__(self, br, bus):
        self.br, self.bus = br, bus
        self.entered = None
        FakeZjy2.instances.append(self)

    def enter_course(self, name):
        self.entered = name
        return name != "进不去"

    def brush_all(self, progress_cb=None):
        if progress_cb:
            progress_cb(1, 1, "x")
        return {"total": 3, "todo": 1, "ok": 1, "done_after": 3, "total_after": 3,
                "failed": [], "aborted": False}

    def verify(self):
        return {"total": 3, "done": 3}


class FakeAi:
    def __init__(self, br, bus):
        self.entered = None

    def enter_course(self, name):
        self.entered = name
        return True

    def brush_all(self, only_undone=True, progress_cb=None):
        return {"total": 2, "todo": 1, "ok": 1, "done_after": 2, "total_after": 2,
                "failed": [], "aborted": False}


def test_zjy2_enters_course():
    print("\n== zjy2 必须按课程名进入（否则刷错课） ==")
    r, bus = make_runner()
    r.ensure_browser = lambda attach=False: FakeBrowser()
    orig_z, orig_a = T.Zjy2, T.AiIcve
    T.Zjy2, T.AiIcve = FakeZjy2, FakeAi
    FakeZjy2.instances.clear()
    try:
        res = r._brush_one("zjy2", "高等数学")
        check("刷课调用了 enter_course", FakeZjy2.instances[0].entered == "高等数学",
              FakeZjy2.instances[0].entered)
        check("返回结果字典", res.get("ok") == 1, res)

        FakeZjy2.instances.clear()
        r._course_progress("zjy2", "大学英语", r.ensure_browser())
        check("读进度也先进入课程", FakeZjy2.instances[0].entered == "大学英语",
              FakeZjy2.instances[0].entered)

        FakeZjy2.instances.clear()
        res = r._brush_one("zjy2", "进不去")
        check("进不去时报错而不是报「已完成 0/0」", res.get("error") == "进入课程失败", res)
    finally:
        T.Zjy2, T.AiIcve = orig_z, orig_a


def test_batch():
    print("\n== 批量刷课 ==")
    r, bus = make_runner()
    calls = []

    def fake_one(platform, name):
        calls.append(name)
        return {"total": 2, "todo": 2, "ok": 2, "done_after": 2, "total_after": 2,
                "failed": [], "aborted": False}

    r._brush_one = fake_one
    courses = [
        {"name": "课一", "brushable": True},
        {"name": "空课", "brushable": False},
        {"name": "课二", "brushable": True},
    ]
    out = r.action_brush_course("ai", "__all__", courses)
    check("只刷可刷课程", calls == ["课一", "课二"], calls)
    check("汇总文案含完成门数", "2/2" in out, out)

    print("\n-- 中途停止 --")
    calls.clear()
    bus.reset_stop()
    bus.request_stop()
    out = r.action_brush_course("ai", "__all__", courses)
    check("停止后不再开始新课", calls == [], calls)

    print("\n-- 没有可刷课程 --")
    out = r.action_brush_course("ai", "__all__", [{"name": "空课", "brushable": False}])
    check("提示没有可刷课程", "没有可刷" in out, out)


def test_error_not_reported_as_done():
    print("\n== 失败不能报成「已完成」 ==")
    r, bus = make_runner()
    msg = r._brush_summary({"total": 0, "todo": 0, "ok": 0, "error": "进入课程失败",
                            "failed": [], "aborted": False})
    check("文案是失败", "失败" in msg and "已完成" not in msg, msg)
    check("日志级别是 error", any(lv == "error" for lv, _ in bus.lines), bus.lines)


def test_empty_course_name():
    print("\n== 没选课程时给出可操作提示 ==")
    r, bus = make_runner()
    out = r.action_brush_course("ai", "")
    check("提示先扫描选课", "扫描课程" in out, out)


def test_picker_items():
    print("\n== 下拉列表里的「全部可刷课程」 ==")
    import tkinter as tk
    from ui.app import App

    root = tk.Tk()
    root.withdraw()
    app = App(root)
    try:
        items = [{"name": "无课A", "brushable": False},
                 {"name": "可刷B", "brushable": True},
                 {"name": "可刷C", "brushable": True}]
        app._on_courses({"platform": "ai", "items": items})
        got = app.course_picker.get_items()
        check("≥2 门可刷时插入批量项", got and got[0].get("name") == "__all__",
              [x.get("name") for x in got])
        check("批量项文案带门数", "2 门" in (got[0].get("label") or ""), got[0].get("label"))
        check("默认不选批量项（防误刷）", app.course_var.get() == "可刷B", app.course_var.get())

        app._on_courses({"platform": "ai", "items": [{"name": "只有一门", "brushable": True},
                                                     {"name": "空课", "brushable": False}]})
        got = app.course_picker.get_items()
        check("只有 1 门可刷时不显示批量项",
              all(x.get("name") != "__all__" for x in got), [x.get("name") for x in got])
    finally:
        root.destroy()


def test_login_wiring():
    print("\n== ① 弹窗结果接到登录动作 ==")
    import tempfile
    import time
    import tkinter as tk
    import config
    from ui.app import App

    root = tk.Tk()
    root.withdraw()
    app = App(root)
    old_path = config.SETTINGS_PATH
    config.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="sb_cfg_"), "settings.json")
    captured = {}

    def fake_open(home, attach=False, platform="ai", login=None):
        captured.update(home=home, platform=platform, login=login)
        return "ok"

    app.runner.action_open_site = fake_open
    try:
        app._start_login("ai", {"mode": "password", "user": "u1", "pwd": "p1",
                                "remember": True})
        for _ in range(60):
            root.update()
            time.sleep(0.02)
        check("账号密码模式把账号传给任务层",
              (captured.get("login") or {}).get("user") == "u1", captured)
        check("记住后写入本机设置", config.load_account("ai").get("pwd") == "p1",
              config.load_account("ai"))
        check("打开的是 AI 优课首页", "ai.icve.com.cn" in (captured.get("home") or ""),
              captured.get("home"))

        app._set_busy(False)
        captured.clear()
        app._start_login("cx", {"mode": "manual"})
        for _ in range(60):
            root.update()
            time.sleep(0.02)
        check("手动模式不传账号", captured.get("login") is None, captured)
        check("学习通走独立分支", captured.get("platform") == "cx"
              and captured.get("home") == "", captured)
    finally:
        config.SETTINGS_PATH = old_path
        root.destroy()


def main():
    test_login_wiring()
    test_zjy2_enters_course()
    test_batch()
    test_error_not_reported_as_done()
    test_empty_course_name()
    test_picker_items()
    print("\n通过 %d 项，失败 %d 项" % (ok_count, len(bad)))
    if bad:
        print("失败：", bad)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
