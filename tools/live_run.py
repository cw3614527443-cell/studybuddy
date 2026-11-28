"""真机走一遍指定流程，把每一步的日志原样打出来（开发期排查用，不打包）。

用法：python tools/live_run.py scan ai
      python tools/live_run.py read ai
      python tools/live_run.py brush ai 大学生心理健康
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.events import EventBus
from core.tasks import TaskRunner


def dump(bus):
    for kind, data in bus.poll():
        if kind == "log":
            tag = data.get("level") or "info"
            print("[%s] %s %s" % (tag.upper(), data.get("time") or "", data.get("msg")), flush=True)
        elif kind == "progress":
            print("[PROG] %s%% %s" % (data.get("value"), data.get("text")), flush=True)
        elif kind == "courses":
            for it in (data.get("items") or []):
                print("[COURSE] %-28s %-4s %s/%s" % (it.get("name"), it.get("note"),
                                                     it.get("done"), it.get("total")), flush=True)
        elif kind == "done":
            print("[DONE] ok=%s summary=%s" % (data.get("ok"), data.get("summary")), flush=True)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "scan"
    plat = sys.argv[2] if len(sys.argv) > 2 else "ai"
    course = sys.argv[3] if len(sys.argv) > 3 else ""

    bus = EventBus()
    runner = TaskRunner(bus)
    t0 = time.time()

    if action == "scan":
        fn = lambda: runner.action_list_courses(plat)          # noqa: E731
    elif action == "read":
        fn = lambda: runner.action_read_time(plat, course)      # noqa: E731
    elif action == "brush":
        fn = lambda: runner.action_brush_course(plat, course, None)   # noqa: E731
    elif action == "watch":
        fn = lambda: runner.action_watch_time(2.0, 300, platform=plat)  # noqa: E731
    else:
        print("unknown action", action)
        return 2

    if not runner.start(fn):
        print("已有任务在跑")
        return 1
    while runner.is_running():
        dump(bus)
        time.sleep(0.25)
    dump(bus)
    print("=== 用时 %.1f 秒 ===" % (time.time() - t0))
    # 不调 runner.shutdown()：那会把 Edge 一起关掉，后面的步骤就断了。
    # _wrap 的 finally 已经 detach，浏览器进程留着给下一步用。
    return 0


if __name__ == "__main__":
    sys.exit(main())
