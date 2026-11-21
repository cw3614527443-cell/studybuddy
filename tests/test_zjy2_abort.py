"""zjy2「进度没涨却一直往后刷」的回归测试（v1.4.3）。

场景：文档弹窗（「上次观看到第X页，是否继续观看？」）没被关掉时，
点不到「下一页」，进度一直是 0%。旧代码一项失败后仍继续刷后面十几项，
等于整轮白刷。现在连续 2 项没涨就中止，并且报告里带 aborted 标记。

用假对象替换掉浏览器交互，不联网、不启 Edge。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.platforms.zjy2 import Zjy2  # noqa: E402

FAILS = []
SHOTS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


class FakeBus:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def should_stop(self):
        return False


class FakeBr:
    def __init__(self):
        self.page = None          # Zjy2.__init__ 会读 br.page

    def shot(self, tag="x"):
        SHOTS.append(tag)

    def guard_flush(self):
        pass

    def wait(self, ms):
        pass


def make_z(items, gains_progress=False):
    bus = FakeBus()
    z = Zjy2(FakeBr(), bus)
    calls = {"open": 0, "learn": 0, "ids": set()}

    z.go_index = lambda: True
    z.expand_all = lambda: None
    z.list_resources = lambda: items
    z._stopped = lambda: False

    def opener(r, retries=2):
        calls["open"] += 1
        calls["ids"].add(r["id"])   # 每项最多重试 3 次，按资源去重
        return True

    def learner(name=""):
        calls["learn"] += 1
        return {"kind": "video", "ok": True, "note": ""}

    z.open_resource = opener
    z._learn_current = learner
    state = {"n": 0}

    def pct(node_id):
        state["n"] += 1
        if gains_progress:
            return 100 if state["n"] >= 2 else 0
        return 0

    z.progress_of = pct
    z._wait_progress = lambda node_id, before, timeout=6: gains_progress
    z._doc_pages = lambda: None
    return z, bus, calls


def item(i, pct=0):
    return {"id": f"r{i}", "name": f"资源{i}.mp4", "pct": pct,
            "kind": "video", "prog": "已学: 0%"}


def main():
    SHOTS.clear()
    print("== 进度一直 0%：连续两项后中止 ==")
    z, bus, calls = make_z([item(i) for i in range(1, 6)])
    res = z.brush_all()
    check("中止标记", res["aborted"] is True, str(res))
    check("只处理了 2 项，不是 5 项", len(calls["ids"]) == 2, calls["ids"])
    check("确认成功 0 项（不谎报）", res["ok"] == 0, res["ok"])
    check("记录了失败项", len(res["failed"]) == 2, res["failed"])
    check("日志说明为什么停", any("连续 2 项进度都没涨" in m for _, m in bus.logs),
          [m for _, m in bus.logs][-3:])
    check("截图留证", "brush_aborted" in SHOTS, SHOTS)

    print("== 进度能涨：不该被误中止 ==")
    z2, _, calls2 = make_z([item(i) for i in range(1, 4)], gains_progress=True)
    res2 = z2.brush_all()
    check("没有中止", res2["aborted"] is False, str(res2))
    check("3 项都刷了", len(calls2["ids"]) == 3, calls2["ids"])
    check("确认成功 3 项", res2["ok"] == 3, res2["ok"])

    print("== 只有 1 项失败：不触发中止 ==")
    z3, _, calls3 = make_z([item(1, pct=0), item(2, pct=100)])
    res3 = z3.brush_all()
    check("没有中止", res3["aborted"] is False, str(res3))
    check("只刷了那 1 项", len(calls3["ids"]) == 1, calls3["ids"])

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
