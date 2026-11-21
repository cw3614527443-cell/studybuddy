"""学习通套件整合的离线回归测试。

不登录、不联网、不启动浏览器：用仿造 HTML 钉死课程解析与「可刷/无课」标注，
用假日志总线钉死脚本 stdout → 界面日志/进度/二维码截图的转发。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.events import EventBus
from core.platforms import chaoxing_adapter as CA

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


SAMPLE_HTML = """
<div class="course clearfix" id="c_1001">
  <div class="course-name" title="大学英语口语"></div>
  任务点进度：3/10
  <a href="/visit/stucoursemiddle?courseid=1001&amp;clazzid=&amp;cpi=">进入</a>
</div>
<div class="course clearfix" id="c_1002">
  <div class="course-name" title="空课"></div>
  任务点进度：0/0
  <a href="/visit/stucoursemiddle?courseid=1002&amp;clazzid=&amp;cpi=">进入</a>
</div>
"""


class FakeCourseBr:
    def __init__(self, html=SAMPLE_HTML):
        self.html_text = html
        self.gotos = []

    def goto(self, url, settle=0):
        self.gotos.append(url)

    def frame_of(self, needle):
        return "visit/interaction"

    def html(self, frame=None):
        return self.html_text

    def wait(self, ms=0):
        pass

    def titles(self):
        return []

    def shot(self, name):
        return ""


def test_bundle_files():
    print("== 套件文件与改造标记 ==")
    sd = pathlib.Path(CA.suite_dir())
    for name in ("README.md", "agent.mjs", "client.py", "catalog.py", "video.py",
                 "exam.py", "chapter_quiz.py", "quiz.py", "run.py", "start.cmd"):
        check(f"包含 {name}", (sd / name).is_file())
    agent = (sd / "agent.mjs").read_text(encoding="utf-8")
    check("agent 支持 SB_PW_ENTRY", "SB_PW_ENTRY" in agent)
    check("agent 支持 Edge 内核", "msedge.exe" in agent)
    check("agent 支持优雅退出", "case 'quit'" in agent)
    run_src = (sd / "run.py").read_text(encoding="utf-8")
    check("run 暴露 list_courses", "def list_courses(b)" in run_src)
    client = (sd / "client.py").read_text(encoding="utf-8")
    check("client 支持停止钩子", "set_stop_check" in client and "STOPPED" in client)
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    check("打包包含套件目录", '"chaoxing"' in spec and "os.walk(_SUITE)" in spec)


def test_course_parse_and_labels():
    print("== 课程解析与可刷标注 ==")
    m = CA.ChaoxingSuite(EventBus()).modules()
    courses = m["run"].list_courses(FakeCourseBr())
    check("解析出 2 门课", len(courses) == 2, str(courses))
    check("课程名正确", [c["name"] for c in courses] == ["大学英语口语", "空课"])
    check("任务点进度正确", [(c["done"], c["total"]) for c in courses] == [(3, 10), (0, 0)])

    cx = CA.ChaoxingSuite(EventBus())
    cx.ensure_agent = lambda: None
    cx.browser = lambda: FakeCourseBr()
    items = cx.list_courses()
    check("可刷/无课标注", [x["note"] for x in items] == ["可刷", "无课"], str(items))
    check("进度带进列表", items[0]["progress"] == "3/10")


def test_open_course_strict_name():
    print("== 课程名精确匹配 ==")
    m = CA.ChaoxingSuite(EventBus()).modules()
    try:
        m["run"].cmd_open(FakeCourseBr(), "不存在的课")
        check("找不到课程时报错", False, "没有抛异常")
    except RuntimeError as e:
        check("找不到课程时报错", "没找到课程" in str(e), str(e))


def test_stdout_forward():
    print("== 脚本输出转发 ==")
    bus = EventBus()
    writer = CA._LogWriter(bus)
    writer.write("[2/4] 正在检测课程\n")
    writer.write("二维码截图: D:/tmp/login-qr.png\n")
    writer.flush()
    events = bus.poll(50)
    logs = [d["msg"] for k, d in events if k == "log"]
    prog = [d["value"] for k, d in events if k == "progress"]
    shots = [d["path"] for k, d in events if k == "shot"]
    check("日志转发", any("正在检测课程" in x for x in logs), str(logs))
    check("进度解析", prog == [50], str(prog))
    check("二维码截图推送", shots == ["D:/tmp/login-qr.png"], str(shots))


def test_answer_normalization():
    print("== 大模型答案归一化 ==")
    check("多选字母", CA._norm_letters(["A", "C"]) == ["A", "C"])
    check("AC 连写", CA._norm_letters("AC") == ["A", "C"])
    check("判断题 对", CA._norm_judge("true") == "对")
    check("判断题 错", CA._norm_judge("×") == "错")
    q = {"no": "7.1#123", "q": 2, "type": "judge"}
    check("判断题归一化", CA._normal_answer(q, "正确") == "对")
    q2 = {"no": "11.6", "q": 1, "type": "single"}
    check("单选题归一化", CA._normal_answer(q2, "B、xxx") == "B")
    rows = CA._rows_to_payload([{"no": "11.6", "questions": [
        {"q": 1, "type": "single", "stem": "题干", "passage": "", "options": [
            {"label": "A", "text": "选项一"}]}]}])
    check("题目载荷带 no/q", rows[0]["no"] == "11.6" and rows[0]["q"] == 1, str(rows))


def main():
    test_bundle_files()
    test_course_parse_and_labels()
    test_open_course_strict_name()
    test_stdout_forward()
    test_answer_normalization()
    if FAILS:
        print(f"\nFAILED {len(FAILS)}: {FAILS}")
        raise SystemExit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    main()
