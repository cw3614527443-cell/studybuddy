"""「② 读取学习进度」回退逻辑测试（v1.4.2）。

不必联网、不必开浏览器：把 tasks 里的平台类换成假的，只验证编排逻辑。
覆盖：
- AI 优课有观看时长 → 只报时长，不白跑一趟目录；
- AI 优课没有时长（课程不计学时）→ 自动改读目录完成度；
- zjy2 → 本来就无学时统计，直接读完成度；
- 进不去课程 / 目录 0 项 → 明确报失败，不谎报 0%；
- 中途点停止 → 统计中断，不当成 0%。
"""
import inspect
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.tasks as T
from core.events import EventBus
from core.platforms.ai_icve import AiIcve

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


class FakeAi:
    def __init__(self, watch=(None, None), enter=True, total=125, done=100):
        self.watch = watch
        self.enter = enter
        self.total, self.done = total, done
        self.enter_called = 0
        self.watch_kwargs = {}

    # 签名与真实类一致（read_watch 新增 quiet）
    def read_watch(self, **kw):
        self.watch_kwargs = kw
        return self.watch

    def enter_course(self, name, timeout=20):
        self.enter_called += 1
        return self.enter

    def verify(self):
        return {"total": self.total, "done": self.done, "items": [{}] * self.total}


class FakeZjy:
    def __init__(self, total=61, done=40):
        self.total, self.done = total, done
        self.verify_called = 0

    def verify(self):
        self.verify_called += 1
        return {"total": self.total, "done": self.done, "items": [{}] * self.total}


class FakeBr:
    """假的 Browser：只需要回答「是否停在登录页」。

    after：从第 2 次询问起改口（模拟统计途中登录过期）。
    """

    def __init__(self, login=False, after=None):
        self.login = login
        self.after = after
        self.calls = 0

    def on_login_page(self):
        self.calls += 1
        if self.after is not None and self.calls > 1:
            return self.after
        return self.login


def run(platform="ai", ai=None, zjy=None, stop=False, course="大学生心理健康",
        login=False, login_after=None):
    """跑一次 action_read_time，返回 (summary, logs, fakes)。"""
    bus = EventBus()
    r = T.TaskRunner(bus)
    r.ensure_browser = lambda: FakeBr(login, login_after)     # 不连 CDP

    ai = ai or FakeAi()
    zjy = zjy or FakeZjy()
    orig_ai, orig_zjy = T.AiIcve, T.Zjy2
    T.AiIcve, T.Zjy2 = (lambda br, b: ai), (lambda br, b: zjy)
    try:
        if stop:
            bus.request_stop()
        summary = r.action_read_time(platform, course)
    finally:
        T.AiIcve, T.Zjy2 = orig_ai, orig_zjy

    logs = []
    for kind, data in bus.poll(500):
        if kind == "log":
            logs.append((data["level"], data["msg"]))
    return summary, logs, ai, zjy


def main():
    print("== AI 优课：有观看时长 ==")
    s, logs, ai, _ = run(ai=FakeAi(watch=(5.2, 12)))
    check("返回时长与次数", s == "观看时长 5.2 时 / 12 次", s)
    check("不白跑目录（没进课程）", ai.enter_called == 0, ai.enter_called)
    check("读时长时带 quiet=True", ai.watch_kwargs.get("quiet") is True, ai.watch_kwargs)
    check("日志有时长", any("观看时长 5.2 时" in m for _, m in logs))

    print("== AI 优课：没有观看时长 → 回退完成度 ==")
    s, logs, ai, _ = run(ai=FakeAi(watch=(None, None), total=125, done=100))
    check("返回完成度", s == "完成度 100/125（80%）", s)
    check("确实进了课程再统计", ai.enter_called == 1, ai.enter_called)
    check("说明改读完成度的原因",
          any("改为读取刷课完成度" in m for lv, m in logs if lv == "warn"), logs)
    check("日志有完成度", any("课程完成度 100/125（80%）" in m for _, m in logs))

    print("== AI 优课：读不到时长但课程进不去 ==")
    s, logs, _, _ = run(ai=FakeAi(watch=(None, None), enter=False))
    check("报失败而不是 0%", s == "读取失败", s)
    check("日志点明进不去课程", any("进入课程" in m for _, m in logs))

    print("== AI 优课：目录 0 项 ==")
    s, _, _, _ = run(ai=FakeAi(watch=(None, None), total=0, done=0))
    check("报失败（不谎报 100%）", s == "读取失败", s)

    print("== zjy2：直接读完成度 ==")
    s, logs, ai, zjy = run("zjy2", ai=FakeAi(watch=(9.9, 99), total=125, done=100))
    check("返回完成度", s == "完成度 40/61（66%）", s)
    check("没去读观看时长", ai.watch_kwargs == {}, ai.watch_kwargs)
    check("统计被调用", zjy.verify_called == 1, zjy.verify_called)
    check("说明 zjy2 无学时统计", any("zjy2 没有学时统计" in m for _, m in logs))

    print("== zjy2：目录 0 项 ==")
    s, _, _, _ = run("zjy2", zjy=FakeZjy(total=0, done=0))
    check("报失败", s == "读取失败", s)

    print("== 登录态失效（被踢回登录页）==")
    s, logs, ai, _ = run(ai=FakeAi(watch=(None, None)), login=True)
    check("AI：报未登录", s == "未登录", s)
    check("AI：不误判成「课程不计学时」",
          not any("改为读取刷课完成度" in m for _, m in logs), logs)
    check("AI：提示重新扫码", any("重新扫码" in m for _, m in logs), logs)
    check("AI：没白跑目录", ai.enter_called == 0, ai.enter_called)

    s, logs, _, zjy = run("zjy2", login=True)
    check("zjy2：报未登录", s == "未登录", s)
    check("zjy2：不白跑统计", zjy.verify_called == 0, zjy.verify_called)

    # 目录打开后才发现掉登录（中途过期）：也要说清是登录问题
    print("== 统计时才发现登录失效 ==")
    s, logs, _, _ = run("zjy2", zjy=FakeZjy(total=0, done=0), login_after=True)
    check("报未登录而不是「读取失败」", s == "未登录", s)

    print("== 统计中途点停止 ==")
    s, logs, _, _ = run("zjy2", stop=True)
    check("返回已停止", s == "已停止", s)
    check("日志说明中断", any("已停止统计完成度" in m for _, m in logs))

    print("== 真实类签名 ==")
    sig = inspect.signature(AiIcve.read_watch)
    check("read_watch 有 quiet 参数", "quiet" in sig.parameters, sig)
    check("quiet 默认 False", sig.parameters["quiet"].default is False, sig)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
