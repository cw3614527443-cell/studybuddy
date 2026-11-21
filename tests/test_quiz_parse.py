"""自动答题的解析/作答回归测试（v1.3.0）。

平台真实作答页看不到（账号上的作业/考试都做完了），所以用一个仿造的题库页
把「题目解析 → 答案映射 → 点选/填空」这条链路钉死：

- 选择题按 `name` 分组成题，题干必须是「题目容器文本 − 选项文字」；
- 单选只点一个，多选按答案逐个点；
- 填空按空位顺序填，并触发 input/change 事件（Vue/React 才认）；
- 提交按钮要能被找到。

跑法：python tests/test_quiz_parse.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.platforms.quiz_ai import QuizAI  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
  body{margin:0;padding:16px;font:14px/1.7 sans-serif}
  .q-item{border-bottom:1px solid #eee;padding:10px 0}
  .q-title{font-weight:600;margin-bottom:6px}
  label{display:block}
</style></head><body>

<div class="q-item">
  <div class="q-title">1.（单选题）心理健康的标准不包括哪一项？</div>
  <label><input type="radio" name="q1" value="A">A 情绪基本稳定</label>
  <label><input type="radio" name="q1" value="B">B 人际关系和谐</label>
  <label><input type="radio" name="q1" value="C">C 从不出现焦虑</label>
  <label><input type="radio" name="q1" value="D">D 自我认知清晰</label>
</div>

<div class="q-item">
  <div class="q-title">2.（多选题）属于情绪调节方法的有？</div>
  <label><input type="checkbox" name="q2" value="A">A 认知重评</label>
  <label><input type="checkbox" name="q2" value="B">B 压抑不说</label>
  <label><input type="checkbox" name="q2" value="C">C 合理宣泄</label>
  <label><input type="checkbox" name="q2" value="D">D 转移注意</label>
</div>

<div class="q-item">
  <div class="q-title">3.（判断题）压力一定有害健康。</div>
  <label><input type="radio" name="q3" value="A">A 正确</label>
  <label><input type="radio" name="q3" value="B">B 错误</label>
</div>

<div class="q-item">
  <div class="q-title">4.（填空题）心理健康包括 ____ 与 ____ 两个方面。</div>
  <p><input type="text" name="f1" placeholder="第一个空">
     <input type="text" name="f1" placeholder="第二个空"></p>
</div>

<button id="submit-btn" onclick="window.__submitted = true">提交作业</button>
</body></html>"""


class FakeBr:
    def __init__(self, page):
        self.page = page
        self.bus = None

    def current_url(self):
        return self.page.url

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def goto(self, url):
        self.page.goto(url)

    def shot(self, tag="x"):
        pass


class FakeLLM:
    """给定答案，顺便把模型实际收到的题面记录下来供断言。"""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def ready(self):
        return True

    def answer(self, questions, course=""):
        self.seen = questions
        return self.answers


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "sb_fixture_quiz.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HTML)

    answers = {
        1: {"keys": ["C"], "text": "", "reason": "绝对化表述"},
        2: {"keys": ["A", "C", "D"], "text": "", "reason": ""},
        3: {"keys": ["B"], "text": "", "reason": ""},
        4: {"text": "身体||心理", "keys": [], "reason": ""},
    }
    llm = FakeLLM(answers)

    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=EDGE if os.path.exists(EDGE) else None, headless=True
        )
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto("file:///" + tmp.replace("\\", "/"))
        page.wait_for_timeout(200)

        q = QuizAI(FakeBr(page), bus=None, llm=llm)
        parsed = q.read_questions()

        choices = parsed.get("choice", [])
        fills = parsed.get("fill", [])
        ok &= check("选择题解析出 3 组", len(choices) == 3, str(len(choices)))
        ok &= check("填空解析出 1 题 / 2 个空",
                    len(fills) == 1 and fills[0]["blanks"] == 2, str(fills))
        ok &= check("总题数 = 4", parsed.get("total") == 4, str(parsed.get("total")))

        stems = [c["stem"] for c in choices]
        ok &= check("题干含题目文字", "心理健康的标准不包括" in stems[0], stems[0][:40])
        ok &= check("题干不含选项文字", "情绪基本稳定" not in stems[0], stems[0][:60])
        ok &= check("选项字母与文字正确",
                    [o["k"] for o in choices[0]["options"]] == ["A", "B", "C", "D"]
                    and "人际关系和谐" in choices[0]["options"][1]["text"],
                    str([o["text"][:8] for o in choices[0]["options"]]))
        ok &= check("多选被识别为 checkbox", choices[1]["type"] == "multi", choices[1]["type"])
        ok &= check("判断题被识别（kindText）", "判断" in choices[2]["kindText"], choices[2]["kindText"])

        got_answers, index, qs = q.ask(parsed)
        types = [x["type"] for x in qs]
        ok &= check("题型映射 single/multi/judge/fill",
                    types == ["single", "multi", "judge", "fill"], str(types))
        ok &= check("题干里保留了题型标记", "单选" in qs[0]["stem"], qs[0]["stem"][:30])

        done, total = q.fill(got_answers, index)
        ok &= check("4 题全部作答成功", done == 4 and total == 4, f"{done}/{total}")

        state = page.evaluate("""() => ({
            q1: [...document.querySelectorAll('input[name=q1]')].map(e => e.checked),
            q2: [...document.querySelectorAll('input[name=q2]')].map(e => e.checked),
            q3: [...document.querySelectorAll('input[name=q3]')].map(e => e.checked),
            f1: [...document.querySelectorAll('input[name=f1]')].map(e => e.value),
            events: window.__events || 0
        })""")
        ok &= check("单选只勾了 C", state["q1"] == [False, False, True, False], str(state["q1"]))
        ok &= check("多选勾了 A/C/D", state["q2"] == [True, False, True, True], str(state["q2"]))
        ok &= check("判断题勾了 B（错误）", state["q3"] == [False, True], str(state["q3"]))
        ok &= check("两个空分别填了「身体」「心理」", state["f1"] == ["身体", "心理"], str(state["f1"]))

        # 文本输入要触发事件，否则 Vue/React 拿不到值
        page.evaluate("""() => {
            window.__events = 0;
            document.querySelector('input[name=f1]').addEventListener('input', () => window.__events++);
        }""")
        page.evaluate("""() => {
            const el = document.querySelector('input[name=f1]');
            const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
            d.set.call(el, 'x');
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        ok &= check("input 事件能被监听到（框架同步机制可用）",
                    page.evaluate("() => window.__events") == 1)

        clicked = q.submit()
        ok &= check("找到并点击了「提交作业」",
                    clicked is True and page.evaluate("() => !!window.__submitted") is True)

        browser.close()

    print("\n" + ("全部通过" if ok else "有用例失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
