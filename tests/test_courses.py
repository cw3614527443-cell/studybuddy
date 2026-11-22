"""课程列表识别回归测试：AI 优课 / zjy2 的课程名枚举 + 可刷性判定。

全部用仿造页面 + 假 Browser，不登录不联网。验证：
- 两平台课程名从列表页正确读出（去重、保序）；
- 有可刷资源标 True、空课标 False、quiz 不算可刷；
- 任务编排把结果回传成带 note 的列表，掉登录/停止时不乱回传。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.events import EventBus
from core.platforms.ai_icve import AiIcve
from core.platforms.zjy2 import Zjy2
import core.tasks as T

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


class FakeBr:
    def __init__(self, url=""):
        self.url = url
        self.waits = 0

    def goto(self, url):
        self.url = url

    def wait(self, ms=0):
        self.waits += 1

    def current_url(self):
        return self.url

    def on_login_page(self):
        return False

    def switch_to_course_tab(self, *a, **k):
        return True

    def switch_to_newest_tab(self, *a, **k):
        return True

    def close_extra_tabs(self, *a, **k):
        return True

    def shot(self, *a, **k):
        pass


class FakePage:
    def __init__(self, eval_map=None):
        self.eval_map = eval_map or {}
        self.calls = []

    def evaluate(self, js, arg=None):
        self.calls.append(js[:40])
        key = arg if isinstance(arg, str) else "default"
        val = self.eval_map.get(key, self.eval_map.get("default", []))
        if callable(val):
            return val()
        return val


def test_ai_names():
    print("== AI 优课课程名枚举 ==")
    names = ["形势与政策", "人工智能数学基础", "大学体育（1）", "形势与政策"]
    page = FakePage({"default": names})
    br = FakeBr()
    br.page = page
    plat = AiIcve(br)
    got = plat.list_course_names()
    check("去重保序", got == ["形势与政策", "人工智能数学基础", "大学体育（1）"], str(got))
    check("导航到 myCourses 页", "myCourses" in br.url, br.url)


def test_zjy2_names():
    print("== zjy2 课程名枚举 ==")
    names = ["形势与政策", "人工智能数学基础", "思想道德与法治"]
    page = FakePage({"default": names})
    br = FakeBr()
    br.page = page
    plat = Zjy2(br)
    got = plat.list_course_names()
    check("读出全部课程名", got == names, str(got))
    check("导航到 /study/v2/course", br.url.endswith("/study/v2/course"), br.url)


def test_probe_logic():
    print("== 可刷/无课判定 ==")
    import core.platforms.zjy2 as ZJY2MOD

    class FakeZ:
        def __init__(self, br, bus=None):
            self.br = br

        def wait_ready(self):
            return True

        def expand_all(self, **k):
            return 0

        resources = []

        def list_resources(self):
            return self.resources

        def probe_resources_fast(self, wait_ms=6000):
            brushable = [x for x in self.resources if x.get("kind") != "quiz"]
            return len(brushable) > 0

    orig_Z = ZJY2MOD.Zjy2
    ZJY2MOD.Zjy2 = FakeZ

    try:
        # 用打桩代替展开/资源读取，验证判定分支
        for plat_cls, label in ((AiIcve, "AI"), (Zjy2, "zjy2")):
            br = FakeBr()
            br.page = FakePage()
            plat = plat_cls(br)
            plat._open_course_for_probe = lambda n, **k: True
            plat.enter_course = lambda n, **k: True

            plat.expand_all = lambda **k: 0
            plat.wait_ready = lambda: True

            def set_resources(res):
                FakeZ.resources = res
                plat.list_resources = lambda: res   # zjy2 走自身方法；AI 走 FakeZ
                plat.probe_resources_fast = lambda wait_ms=6000: (
                    len([x for x in res if x.get("kind") != "quiz"]) > 0)

            set_resources([
                {"name": "视频1", "kind": "video", "done": False},
                {"name": "作业", "kind": "quiz", "done": False},
            ])
            check(f"{label}：有视频→可刷", plat.probe_course("X") is True)

            set_resources([{"name": "作业", "kind": "quiz", "done": False}])
            check(f"{label}：只有quiz→无课", plat.probe_course("X") is False)

            set_resources([])
            check(f"{label}：空目录→无课", plat.probe_course("X") is False)

            plat._open_course_for_probe = lambda n, **k: False
            plat.enter_course = lambda n, **k: False
            check(f"{label}：进不去课→None", plat.probe_course("X") is None)
    finally:
        ZJY2MOD.Zjy2 = orig_Z


def test_action():
    print("== 任务编排回传 ==")
    bus = EventBus()
    runner = T.TaskRunner(bus)
    br = FakeBr()
    runner.ensure_browser = lambda: br

    state = {"names": ["A", "B", "C"]}
    fake = type("FakePlat", (), {
        "__init__": lambda self, br, bus=None: None,
        "list_course_names": lambda self: state["names"],
        "probe_course": lambda self, n: True if n in ("A", "C") else False,
    })
    T.AiIcve = fake
    runner.action_list_courses("ai")
    events = bus.poll(100)
    course_evt = [d for k, d in events if k == "courses"]
    check("回传了一次课程列表事件", len(course_evt) == 1, str(len(course_evt)))
    items = course_evt[0]["items"] if course_evt else []
    check("三项都带标注", [x["note"] for x in items] == ["可刷", "无课", "可刷"], str(items))
    check("platform 字段正确", course_evt[0]["platform"] == "ai")

    # 掉登录：names=None → 回传空列表，summary=未登录
    bus2 = EventBus()
    runner2 = T.TaskRunner(bus2)
    runner2.ensure_browser = lambda: br
    state["names"] = None
    summary = runner2.action_list_courses("ai")
    ev2 = bus2.poll(100)
    ce = [d for k, d in ev2 if k == "courses"]
    check("掉登录回传空列表", ce and ce[0]["items"] == [], str(ce))
    check("掉登录 summary=未登录", summary == "未登录", summary)


def test_ai_two_tabs():
    """v1.8.0：AI 优课的课分在两个标签下，必须都读。

    用户报的 bug：真正的课（大学生心理健康）扫不到 —— 它在「校外公开课」标签，
    而旧代码只读「校内私有课」。
    """
    print("== AI 优课两个标签都要读 ==")

    class TabPage:
        def __init__(self, private, public):
            self.tab = "校内私有课"
            self.private = private
            self.public = public

        def evaluate(self, js, arg=None):
            if "tab-item" in js:
                if arg is not None:
                    self.tab = arg
                return True
            if "course-name" in js:
                return list(self.private if self.tab == "校内私有课" else self.public)
            if "page-item" in js:
                return 1 if arg is None else False
            return []

    page = TabPage(["大学英语（1）", "Linux操作系统"], ["大学生心理健康", "虚拟化技术与应用"])
    br = FakeBr()
    br.page = page
    plat = AiIcve(br)
    got = plat.list_course_names()
    check("校内私有课 + 校外公开课合并",
          got == ["大学英语（1）", "Linux操作系统", "大学生心理健康", "虚拟化技术与应用"],
          str(got))

    # 两个标签有重名课时去重，不要把同一门算两次
    page2 = TabPage(["大学生心理健康", "A"], ["大学生心理健康", "B"])
    br2 = FakeBr()
    br2.page = page2
    got2 = AiIcve(br2).list_course_names()
    check("跨标签去重保序", got2 == ["大学生心理健康", "A", "B"], str(got2))


def main():
    test_ai_names()
    test_ai_two_tabs()
    test_zjy2_names()
    test_probe_logic()
    test_action()
    if FAILS:
        print(f"\nFAILED {len(FAILS)}: {FAILS}")
        raise SystemExit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    main()
