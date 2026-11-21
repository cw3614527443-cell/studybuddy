"""大模型答案解析的离线测试（v1.3.0）。

不联网：只验证「模型回复 → 结构化答案」这一段，因为这里出错的后果最直接：
解析歪了就会把错的答案点到卷子上。

覆盖：
- ```json 包裹、前后带废话、字符串里含花括号 都能抠出 JSON；
- 非法选项字母（模型瞎给 E / Z）会被丢掉，不会去点不存在的选项；
- 模型漏答某题 → 明确报错，不静默跳过；
- 第一次返回非 JSON → 自动重问一次；
- 填空题多个空用 || 分隔。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import LLMClient, LLMError, _extract_json  # noqa: E402


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(cond)


class FakeLLM(LLMClient):
    """把 chat 换成本地队列，用来喂不同的模型回复。"""

    def __init__(self, replies):
        super().__init__("http://x/v1", "k", "m", timeout=5)
        self.replies = list(replies)
        self.calls = 0

    def chat(self, *a, **kw):
        self.calls += 1
        return self.replies.pop(0) if self.replies else ""


def main():
    ok = True

    # ---------- 1. JSON 提取 ----------
    ok &= check("裸 JSON", _extract_json('{"answers":[{"i":1}]}') == {"answers": [{"i": 1}]})
    ok &= check("```json 包裹",
                _extract_json('```json\n{"answers":[{"i":2}]}\n```') == {"answers": [{"i": 2}]})
    ok &= check("前后有废话",
                _extract_json('好的，答案如下：{"answers":[{"i":3}]} 以上。') == {"answers": [{"i": 3}]})
    ok &= check("字符串里含花括号",
                _extract_json('{"answers":[{"i":4,"reason":"用到 { } 符号"}]}')["answers"][0]["i"] == 4)
    ok &= check("完全不合法时返回 None", _extract_json("我不知道") is None)

    # ---------- 2. 答案校验 ----------
    qs = [
        {"i": 1, "type": "single", "stem": "单选", "options": [{"k": "A", "text": "a"}, {"k": "B", "text": "b"}]},
        {"i": 2, "type": "multi", "stem": "多选",
         "options": [{"k": "A", "text": "a"}, {"k": "B", "text": "b"}, {"k": "C", "text": "c"}]},
        {"i": 3, "type": "fill", "stem": "填空", "options": []},
    ]
    llm = FakeLLM(['{"answers":['
                   '{"i":1,"keys":["B"],"text":"","reason":"ok"},'
                   '{"i":2,"keys":["a","c","Z"],"text":"","reason":""},'
                   '{"i":3,"keys":[],"text":"甲||乙","reason":""}]}'])
    got = llm.answer(qs, course="测试课程")
    ok &= check("单选答案保留", got[1]["keys"] == ["B"], str(got.get(1)))
    ok &= check("多选答案大写化并丢掉非法字母", got[2]["keys"] == ["A", "C"], str(got.get(2)))
    ok &= check("填空保留 || 分隔", got[3]["text"] == "甲||乙", str(got.get(3)))

    # ---------- 3. 漏答必须报错 ----------
    llm2 = FakeLLM(['{"answers":[{"i":1,"keys":["A"]}]}'] * 2)
    try:
        llm2.answer(qs, course="x")
        ok &= check("漏答时报错", False, "没有抛异常")
    except LLMError as e:
        ok &= check("漏答时报错", "漏答" in str(e), str(e)[:40])

    # ---------- 4. 第一次不是 JSON → 重问 ----------
    llm3 = FakeLLM([
        "我觉得第一题选B吧",
        '{"answers":[{"i":1,"keys":["B"]},{"i":2,"keys":["A"]},{"i":3,"text":"乙"}]}',
    ])
    got3 = llm3.answer(qs, course="x")
    ok &= check("非 JSON 会自动重问一次", llm3.calls == 2 and got3[1]["keys"] == ["B"],
                f"calls={llm3.calls}")

    # ---------- 5. 未配置时给出人话错误 ----------
    empty = LLMClient("", "", "")
    ok &= check("没配置时 ready() 为 False", empty.ready() is False)
    try:
        empty.chat("hi")
        ok &= check("未配置时抛 LLMError", False)
    except LLMError as e:
        ok &= check("未配置时抛 LLMError", "还没配置" in str(e), str(e)[:30])

    print("\n" + ("全部通过" if ok else "有用例失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
