"""AI 优课目录识别回归测试（v1.9.1 真机 bug）。

钉死两个根因，防止再退化：
1. 课程卡片必须真实点击 + 捕获 popup —— JS `e.click()` 在 Vue 组件上返回 true
   但不跳页，曾导致 12 门里 6 门「未确认」，读进度/刷课全读到 0 资源。
2. 目录必须认新版 `.listItem[id]` + `aria-valuenow` —— 只认旧 `div.node`
   时明明 12 章也统计成 0，读进度/复核全报「没找到资源节点」。

仿造页面 + 假 Browser，不登录不联网。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.platforms.ai_icve import AiIcve

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


class FakePop:
    def __init__(self, url):
        self.url = url


class FakePopupCtx:
    def __init__(self, pop):
        self._pop = pop

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        return self._pop


class FakeLocator:
    def __init__(self, pop, fail=False):
        self._pop = pop
        self._fail = fail

    def filter(self, **kw):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 0 if self._fail else 1

    def click(self, timeout=0):
        return None


class FakePage:
    def __init__(self, pop):
        self.url = "https://ai.icve.com.cn/app/my-excellent-home/myCourses"
        self._pop = pop
        self.real_clicked = False

    def locator(self, sel, **kw):
        return FakeLocator(self._pop)

    def expect_popup(self, timeout=0):
        self.real_clicked = True
        return FakePopupCtx(self._pop)

    def evaluate(self, js, arg=None):
        if "expect_popup" in js:
            return False
        return []


class FakeBr:
    def __init__(self):
        self.page = None
        self.logs = []

    def goto(self, url):
        pass

    def wait(self, ms=0):
        pass

    def current_url(self):
        return self.page.url if self.page else ""


class FakeBus:
    def log(self, *a, **k):
        pass


def main():
    print("== 1. 卡片必须走真实点击 + popup ==")
    pop = FakePop("https://zjy2.icve.com.cn/study/v2/coursePreview/spoccourseIndex")
    page = FakePage(pop)
    br = FakeBr()
    br.page = page
    plat = AiIcve(br, FakeBus())
    plat.p = page
    got = plat._click_course_card_real("形势与政策")
    check("返回捕获到的 popup", got is pop)
    check("用了真实点击（expect_popup）", page.real_clicked)

    print("== 2. 新版 .listItem 目录统计 ==")
    items = [
        {"id": "a1", "name": "第一章", "done": True},
        {"id": "a2", "name": "第二章", "done": False},
    ]

    class DirPage:
        url = "https://zjy2.icve.com.cn/study/v2/coursePreview/spoccourseIndex"

        def evaluate(self, js, arg=None):
            if "listItem[id]" in js:
                return [
                    {"id": "a1", "name": "第一章", "done": True},
                    {"id": "a2", "name": "第二章", "done": False},
                ]
            return []

    plat.p = DirPage()
    res = plat.list_resources()
    check("识别到 2 项资源", len(res) == 2, str(res))
    check("第一章标记已完成", res and res[0]["done"] is True)
    check("第二章标记未完成", len(res) > 1 and res[1]["done"] is False)

    print("== 3. 结果汇总 ==")
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
