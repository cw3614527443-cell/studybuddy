"""5 轮全功能 GUI 回归驱动：所有操作都通过 pyautogui 模拟鼠标/键盘点击主界面。

用法：
    python tools/uia_suite.py round 1
    python tools/uia_suite.py round 2
    ...
    python tools/uia_suite.py report

说明：本脚本只往 shots/cmd.json 下发点击指令，真正的鼠标动作在 tools/uia.py
进程（与 GUI 共享 DPI 上下文）里执行；不调用任何后台任务函数，不绕过界面。
"""
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMD = os.path.join(BASE, "shots", "cmd.json")
DUMP = os.path.join(BASE, "shots", "uia.json")
LOG = os.path.join(BASE, "shots", "uia_log.txt")
OUT = os.path.join(BASE, "shots", "suite5")
os.makedirs(OUT, exist_ok=True)
REPORT = os.path.join(OUT, "report.jsonl")

PLATFORM_TABS = ("AI 优课", "zjy2", "学习通")
STATES = ("就绪", "运行中…", "完成", "已停止 / 出错")


_seq = 0


def send(cmd, timeout=25):
    """下发指令并等 GUI 写回结果；不删除 cmd.json（见 uia.py 里的说明）。"""
    global _seq
    _seq += 1
    cmd = dict(cmd)
    cmd["id"] = _seq
    with open(CMD, "w", encoding="utf-8") as f:
        json.dump(cmd, f, ensure_ascii=False)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = json.load(open(CMD, encoding="utf-8"))
            if d.get("done") and d.get("id") == _seq:
                return str(d.get("result") or "")
        except Exception:
            pass
        time.sleep(0.06)
    return "TIMEOUT"


def items():
    send({"action": "collect"})
    try:
        return json.load(open(DUMP, encoding="utf-8"))
    except Exception:
        return []


def find(text, exact=False):
    arr = items()
    cands = [x for x in arr if x.get("cls") in ("CanvasItem", "Button")]
    for x in cands:
        if (x.get("text") or "") == text:
            return x
    if exact:
        return None
    for x in cands:
        if text in (x.get("text") or ""):
            return x
    return None


def click(text, exact=False):
    r = send({"action": "click", "text": text, "exact": exact})
    if r.startswith("NOT_FOUND"):
        raise RuntimeError("点击目标不存在: %s (%s)" % (text, r))
    return r


def clickxy(x, y):
    return send({"action": "clickxy", "x": int(x), "y": int(y)})


def key(name):
    return send({"action": "key", "name": name})


def shot(name):
    return send({"action": "shot", "path": "shots/suite5/%s.png" % name}, timeout=40)


def shotfull(name):
    return send({"action": "shotfull", "path": "shots/suite5/%s.png" % name}, timeout=40)


def gui_status():
    # 状态芯片不止「就绪/完成」三种：扫描时会显示「扫描课程中…」，
    # 不能只认白名单，否则会把运行中的任务误判成没启动。
    cands = [x for x in items()
             if x.get("cls") == "CanvasItem" and x.get("x", 0) > 1200
             and x.get("y", 999) < 150 and x.get("text")]
    return cands[-1]["text"] if cands else "?"


def is_running(status):
    return status not in ("就绪", "完成", "已停止 / 出错", "?")


def logs(n=40):
    r = send({"action": "logtail", "n": n}, timeout=25)
    return r.split(" | ", 1)[1].split(" || ") if " | " in r else []


def wait_idle(timeout=300):
    t0, started, last = time.time(), False, "?"
    while time.time() - t0 < timeout:
        last = gui_status()
        if is_running(last):
            started = True
        elif started and last in ("完成", "已停止 / 出错"):
            return last
        time.sleep(1.2)
    return "超时(%s)" % last


def record(round_no, step, ok, detail=""):
    row = {"round": round_no, "step": step, "ok": bool(ok), "detail": str(detail)[:2000],
           "time": time.strftime("%H:%M:%S")}
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("[R%d] %-16s %s %s" % (round_no, step, "OK " if ok else "FAIL", str(detail)[:150]),
          flush=True)


def close_dialogs():
    """关掉所有残留的自绘弹窗（远控窗叠设置窗时会同时存在两层）。"""
    for _ in range(8):
        arr = items()
        tops = [x for x in arr if x.get("win", "").startswith("top")]
        if not tops:
            break
        btn = None
        for label in ("完成", "取消", "关闭"):
            for x in arr:
                if (x.get("win", "").startswith("top")
                        and (x.get("text") or "") == label):
                    btn = x
                    break
            if btn:
                break
        if btn:
            clickxy(btn["cx"], btn["cy"])
        else:
            key("Escape")
        time.sleep(0.4)
    close_wins = send({"action": "closewins"})   # 点掉标题栏 ×（远控窗没有关闭按钮）
    send({"action": "dialog"})          # 原生 messagebox 兜底
    return close_wins


def set_minutes(value):
    """用鼠标点输入框，退格后键入新值（最长挂机）。"""
    e = None
    for x in items():
        if x.get("cls") == "Entry" and 1220 < x.get("x", 0) < 1400 and 460 < x.get("y", 0) < 530:
            e = x
            break
    if not e:
        return False
    clickxy(e["cx"], e["cy"])
    key("End")
    for _ in range(4):
        key("BackSpace")
    send({"action": "type", "text": str(value)})
    return True


def dismiss_native(name, round_no):
    """原生 messagebox：由 GUI 进程按窗口句柄定位，模拟鼠标点最右按钮。"""
    shotfull("r%d_%s_modal" % (round_no, name))
    r = send({"action": "dialog"})
    time.sleep(0.6)
    close_dialogs()                     # 万一误点「是」打开了设置窗，一并清掉
    return r


def click_task(text, timeout=300, exact=True):
    """点一个后台任务按钮；若被模态框拦住，先关框再点一次。"""
    click(text, exact=exact)
    time.sleep(0.9)
    st = gui_status()
    if not is_running(st):
        send({"action": "dialog"})  # 清掉可能残留的原生 messagebox
        time.sleep(0.4)
        click(text, exact=exact)
        time.sleep(0.9)
        st = gui_status()
    if not is_running(st):
        return False, "任务未启动(状态=%s)" % st
    done = wait_idle(timeout)
    return done in ("完成", "已停止 / 出错"), done


def round_run(n):
    tag = "r%d" % n
    record(n, "begin", True, "5轮全功能模拟点击开始")
    shot(tag + "_00_start")

    # 1) 三平台卡片逐个点击 + 回到 AI
    plat = []
    for name in PLATFORM_TABS:
        try:
            click(name, exact=True)
            time.sleep(0.5)
            plat.append(name + ":OK")
        except Exception as e:
            plat.append(name + ":ERR " + str(e)[:60])
    click("AI 优课", exact=True)
    time.sleep(0.5)
    shot(tag + "_01_platform")
    record(n, "平台切换", all(":OK" in x for x in plat), " / ".join(plat))

    # 2) ① 启动浏览器 / 登录：登录弹窗里选扫码/手动 → 打开页面
    try:
        click("启动浏览器 / 登录", exact=True)
        time.sleep(0.7)
        m = find("扫码 / 手动")
        if m:
            clickxy(m["cx"], m["cy"])
            time.sleep(0.4)
        btn = find("打开页面") or find("登录并打开")
        if btn:
            clickxy(btn["cx"], btn["cy"])
        else:
            key("Return")
        st = wait_idle(180)
        record(n, "①登录", st in ("完成", "已停止 / 出错"),
               st + " | " + " || ".join(logs(8)[-8:]))
        shot(tag + "_02_login")
    except Exception as e:
        record(n, "①登录", False, repr(e))

    # 3) 扫描课程 + 下拉弹层能开能关
    try:
        ok, st = click_task("扫描课程", timeout=600)
        tail = " || ".join(logs(20)[-20:])
        record(n, "扫描课程", ok, st + " | " + tail)
        shot(tag + "_03_scan")
        p = None
        for x in items():
            if x.get("cls") == "CanvasItem" and x.get("win") == "main" \
                    and 500 < x.get("x", 0) < 1010 and 450 < x.get("y", 0) < 510:
                p = x
                break
        if p:
            clickxy(p["cx"], p["cy"])
            time.sleep(0.6)
            shot(tag + "_04_picker_open")
            clickxy(700, 850)          # 点日志区空白处，测失焦关闭
            time.sleep(0.5)
            shot(tag + "_05_picker_close")
            record(n, "课程下拉", True, "展开+点外部关闭")
        else:
            record(n, "课程下拉", False, "找不到课程选择框")
    except Exception as e:
        record(n, "扫描课程", False, repr(e))

    # 4) ② 读取进度
    try:
        ok, st = click_task("读取学习进度", timeout=360)
        record(n, "②读进度", ok, st + " | " + " || ".join(logs(18)[-18:]))
        shot(tag + "_06_read")
    except Exception as e:
        record(n, "②读进度", False, repr(e))

    # 5) ③ 刷课（一键过完）
    try:
        ok, st = click_task("刷课（一键过完）", timeout=600)
        record(n, "③刷课", ok, st + " | " + " || ".join(logs(25)[-25:]))
        shot(tag + "_07_brush")
    except Exception as e:
        record(n, "③刷课", False, repr(e))

    # 6) ④ 刷时长：把最长挂机设 1 分钟，跑起来后点停止
    try:
        ok = set_minutes(1)
        click("刷观看时长", exact=True)
        time.sleep(0.9)
        if not is_running(gui_status()):
            send({"action": "dialog"})
            time.sleep(0.4)
            click("刷观看时长", exact=True)
            time.sleep(0.9)
        started = is_running(gui_status())
        time.sleep(25)
        click("■  停止当前任务", exact=True)
        st = wait_idle(150)
        record(n, "④刷时长", ok and started and st in ("完成", "已停止 / 出错"),
               "参数改1分钟=%s 启动=%s | %s | %s" % (ok, started, st,
                                                 " || ".join(logs(18)[-18:])))
        shot(tag + "_08_watch_stop")
        set_minutes(300)
    except Exception as e:
        record(n, "④刷时长", False, repr(e))

    # 7) ⑤ 复核
    try:
        ok, st = click_task("复核完成情况", timeout=360)
        record(n, "⑤复核", ok, st + " | " + " || ".join(logs(18)[-18:]))
        shot(tag + "_09_verify")
    except Exception as e:
        record(n, "⑤复核", False, repr(e))

    # 8) ⑥ 自动答题入口（未配置大模型会弹窗）
    try:
        click("自动答题（大模型）", exact=True)
        dismiss_native("quiz", n)
        record(n, "⑥答题入口", True, "点击并关闭配置提示")
    except Exception as e:
        record(n, "⑥答题入口", False, repr(e))

    # 9) 手机远控：打开弹窗检查配对码与地址，再关闭
    try:
        click("手机远控", exact=True)
        time.sleep(1.0)
        shot(tag + "_10_remote")
        arr = items()
        tops = [x for x in arr if x.get("win", "").startswith("top")]
        record(n, "手机远控", bool(tops),
               "弹窗控件数=%d | %s" % (len(tops), " / ".join(
                   [x["text"] for x in tops if x.get("text")][:12])))
        close_dialogs()
    except Exception as e:
        record(n, "手机远控", False, repr(e))

    # 10) 辅助按钮：设置 / 关于 / 功能说明 / 清空日志
    for label, key_name in (("⚙ 大模型设置", "settings"),
                            ("功能说明", "help"),
                            ("关于此软件", "about")):
        try:
            click(label)
            time.sleep(0.6)
            shot(tag + "_11_" + key_name)
            dismiss_native(key_name, n)
            close_dialogs()
            record(n, key_name, True, "点击并关闭")
        except Exception as e:
            record(n, key_name, False, repr(e))
    try:
        before = len(logs(60))
        click("清空", exact=True)
        time.sleep(0.4)
        after = len(logs(60))
        record(n, "清空日志", after < max(3, before // 2),
               "清空前=%d 清空后=%d" % (before, after))
    except Exception as e:
        record(n, "清空日志", False, repr(e))

    # 11) 关闭浏览器：下一轮①会重新拉起
    try:
        click("关闭浏览器", exact=True)
        time.sleep(1.0)
        if not is_running(gui_status()):
            send({"action": "dialog"})
            time.sleep(0.4)
            click("关闭浏览器", exact=True)
            time.sleep(1.0)
        st = wait_idle(120)
        record(n, "关闭浏览器", st in ("完成", "已停止 / 出错"),
               st + " | " + " || ".join(logs(8)[-8:]))
        shot(tag + "_12_closed")
    except Exception as e:
        record(n, "关闭浏览器", False, repr(e))
    record(n, "end", True, "本轮结束")


def report():
    rows = [json.loads(x) for x in open(REPORT, encoding="utf-8") if x.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["round"], []).append(r)
    for n in sorted(by):
        steps = [r for r in by[n] if r["step"] not in ("begin", "end")]
        bad = [r["step"] for r in steps if not r["ok"]]
        print("R%s: %d/%d 通过; 失败=%s" % (n, len(steps) - len(bad), len(steps), bad))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "round":
        round_run(int(sys.argv[2]))
    else:
        report()
