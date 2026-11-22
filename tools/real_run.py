"""真机全流程测试：登录 → 扫描课程 → 全部可刷 → 逐门刷课 → 复核。

GUI 里点按钮跑的是同一套 TaskRunner 动作，这个脚本只是把「点按钮」换成命令行，
方便长时间跑（刷课几小时）时不被窗口遮挡、日志能落盘。

用法：
  python tools/real_run.py --platform zjy2 --user 手机号 --pwd 密码 --do login,scan
  python tools/real_run.py --platform zjy2 --do scan,brush,verify
  python tools/real_run.py --platform cx  --user 手机号 --pwd 密码 --do login,scan,brush
"""
import argparse
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.events import EventBus           # noqa: E402
from core.tasks import TaskRunner          # noqa: E402
from config import ZJY2_HOME, AI_ICVE_HOME, LOGS_DIR  # noqa: E402

LEVEL_MARK = {"info": "  ", "ok": "[OK] ", "warn": "[!] ", "error": "[X] "}

# 学习通桥接层会把 sys.stdout 临时换成日志转发器（_LogWriter）。
# 如果这里用 print，主线程打印日志就会被转发器再捕一遍，形成
# 「日志 → print → 转发器 → 日志」的无限放大（实测一分钟涨 1MB）。
# 所以这里抓住启动时的原始流，直接写它。
_STREAM = sys.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="zjy2", choices=["zjy2", "ai", "cx"])
    ap.add_argument("--user", default="")
    ap.add_argument("--pwd", default="")
    ap.add_argument("--do", default="login,scan")
    ap.add_argument("--course", default="")
    ap.add_argument("--wait", type=int, default=240)
    args = ap.parse_args()

    steps = [s.strip() for s in args.do.split(",") if s.strip()]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"real-{args.platform}-{stamp}.log")
    lf = open(log_path, "w", encoding="utf-8")

    def out(text, level="info"):
        line = f"{time.strftime('%H:%M:%S')} {LEVEL_MARK.get(level, '  ')}{str(text)[:300]}"
        _STREAM.write(line + "\n")
        _STREAM.flush()
        lf.write(line + "\n")
        lf.flush()

    bus = EventBus()
    runner = TaskRunner(bus)
    home = {"zjy2": ZJY2_HOME, "ai": AI_ICVE_HOME, "cx": ""}[args.platform]

    courses = []
    results = {}
    ok_all = True

    def run_one(title, fn, *a, **kw):
        nonlocal ok_all
        out(f"===== {title} =====", "ok")
        t0 = time.time()
        runner.start(fn, *a, **kw)
        fatal = None
        while True:
            for kind, data in bus.poll():
                if kind == "log":
                    out(data["msg"], data["level"])
                elif kind == "progress":
                    if data.get("text"):
                        out(f"· {data['text']}", "info")
                elif kind == "shot":
                    out(f"· 截图 {data['path']}")
                elif kind == "courses":
                    courses[:] = data.get("items") or []
                elif kind == "done":
                    if not data.get("ok"):
                        ok_all = False
                        fatal = data.get("summary") or "失败"
                    out(f"→ {title} 结束：{data.get('summary')}（{time.time() - t0:.0f}s）",
                        "ok" if data.get("ok") else "error")
            if not runner.is_running() and bus.q.empty():
                break
            time.sleep(0.2)
        return fatal

    try:
        if "login" in steps:
            err = run_one("登录", runner.action_open_site, home,
                          platform=args.platform,
                          login={"user": args.user, "pwd": args.pwd, "wait": args.wait})
            if err:
                raise SystemExit(f"登录失败：{err}")

        if "scan" in steps:
            err = run_one("扫描课程", runner.action_list_courses, platform=args.platform)
            if courses:
                out(f"课程清单（{len(courses)} 门）：", "ok")
                for i, c in enumerate(courses, 1):
                    out(f"  {i:2d}. {c['name']}  [{c.get('note')}] {c.get('progress', '')}")
                lf.write("课程清单（分隔）\n")
                for c in courses:
                    lf.write(f"{c['name']}\t{c.get('note')}\t{c.get('progress', '')}\n")
                lf.flush()

        if "brush" in steps:
            if args.course:
                err = run_one(f"刷课 {args.course}", runner.action_brush_course,
                              platform=args.platform, course_name=args.course)
            else:
                err = run_one("刷课（全部可刷）", runner.action_brush_course,
                              platform=args.platform, course_name="__all__",
                              courses=courses)
            results["brush"] = err

        if "verify" in steps:
            targets = ([c["name"] for c in courses if c.get("brushable")]
                       or ([args.course] if args.course else []))
            for name in targets:
                run_one(f"复核 {name}", runner.action_read_time,
                        platform=args.platform, course_name=name)
    except SystemExit as e:
        out(str(e), "error")
        ok_all = False
    except Exception:
        out(traceback.format_exc(), "error")
        ok_all = False
    finally:
        # 必须优雅收尾：学习通的浏览器若被强杀，登录 cookie 不会写盘，
        # 下一次启动又会退回登录页（真机踩过：刚登录成功，扫描却要求重新扫码）。
        out("正在关闭浏览器并保存登录状态…")
        try:
            runner.shutdown(wait=5)
            for kind, data in bus.poll():
                if kind == "log":
                    out(data["msg"], data["level"])
        except Exception:
            out(traceback.format_exc(), "error")
        out("===== 结束 =====" if ok_all else "===== 结束（有失败项）=====",
            "ok" if ok_all else "error")
        out(f"日志：{log_path}")
        lf.close()


if __name__ == "__main__":
    main()
