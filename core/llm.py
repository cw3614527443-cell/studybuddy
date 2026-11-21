"""大模型客户端（自动答题用）。

设计原则
--------
1. **OpenAI 兼容协议**：POST {base}/chat/completions，`Authorization: Bearer <key>`。
   DeepSeek / 通义千问 / Kimi / 豆包 / OpenAI 都遵循这套协议，换服务只改 base 和 model。
2. **不内置 Key**：这个工具要发给别人，内置 Key 等于公开你的额度。
   Key 存在本机 settings.json（明文，界面上已经提示）。
3. **只吃结构化答案**：要求模型输出 JSON，解析失败就重问一次，
   绝不用「自然语言猜答案」——猜错的题会直接体现在分数上。
"""
import json
import re
import urllib.error
import urllib.request

SYSTEM_PROMPT = """你是一名严谨的高校课程题库答题助手，负责回答智慧职教平台的作业/测验/考试题。

规则：
1. 只依据题干和选项本身作答，不要臆造题干里没有的信息。
2. 单选题/判断题：keys 里只能有一个选项字母。
3. 多选题：keys 里放全部正确选项字母（按字母顺序）。
4. 填空题/简答题：keys 留空数组，把答案文字写进 text；多个空用 "||" 分隔。
5. 若实在无法确定，选最可能的答案，并在 reason 里写"不确定"。
6. 严格只输出 JSON，不要解释、不要 Markdown 代码块。"""

USER_TMPL = """课程：{course}

题目（JSON）：
{payload}

请按下列结构输出答案 JSON：
{{"answers":[{{"i":题号,"keys":["A"],"text":"","reason":"不超过20字的理由"}}]}}"""


class LLMError(RuntimeError):
    pass


def _extract_json(text):
    """从模型回复里抠出 JSON（容忍 ```json 包裹、前后废话）。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    # 抓第一个完整的 {...}
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(s[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except Exception:
                    return None
    return None


class LLMClient:
    def __init__(self, base, key, model, timeout=90):
        self.base = (base or "").rstrip("/")
        self.key = (key or "").strip()
        self.model = (model or "").strip()
        self.timeout = timeout

    # ---------- 基础 ----------
    def ready(self):
        return bool(self.base and self.key and self.model)

    def chat(self, user, system=SYSTEM_PROMPT, temperature=0.2, max_tokens=2048):
        if not self.ready():
            raise LLMError("还没配置大模型（接口地址 / Key / 模型名），请先在「⚙ 大模型设置」里填好")
        url = self.base + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            raise LLMError(f"接口返回 {e.code}：{detail or e.reason}")
        except urllib.error.URLError as e:
            raise LLMError(f"连不上大模型接口（{e.reason}），检查网络或接口地址")
        except Exception as e:
            raise LLMError(f"请求失败：{e}")

        try:
            return body["choices"][0]["message"]["content"] or ""
        except Exception:
            raise LLMError(f"返回格式不认识：{str(body)[:200]}")

    def test(self):
        """设置页的「测试连接」。"""
        return self.chat("用一句话回答：1+1 等于几？", max_tokens=32)

    # ---------- 答题 ----------
    def answer(self, questions, course="", batch=8):
        """questions: [{i,type,stem,options:[{k,text}]}] → {i: {keys,text,reason}}"""
        result = {}
        for start in range(0, len(questions), batch):
            chunk = questions[start:start + batch]
            got = self._answer_chunk(chunk, course)
            result.update(got)
        return result

    def _answer_chunk(self, chunk, course):
        payload = json.dumps(chunk, ensure_ascii=False, indent=1)
        user = USER_TMPL.format(course=course or "（未提供）", payload=payload)
        data = None
        for attempt in range(2):
            text = self.chat(user if attempt == 0 else
                             user + "\n\n注意：上一次的输出不是合法 JSON，这次只输出 JSON。",
                             max_tokens=2048)
            data = _extract_json(text)
            if isinstance(data, dict) and isinstance(data.get("answers"), list):
                break
            data = None
        if not data:
            raise LLMError("模型没有返回可解析的答案 JSON")

        valid = {q["i"]: {o["k"] for o in (q.get("options") or [])} for q in chunk}
        out = {}
        for a in data["answers"]:
            try:
                i = int(a.get("i"))
            except Exception:
                continue
            keys = [str(k).strip().upper()[:1] for k in (a.get("keys") or [])]
            keys = [k for k in keys if k and k in (valid.get(i) or set())]
            text = (a.get("text") or "").strip()
            out[i] = {"keys": keys, "text": text,
                      "reason": (a.get("reason") or "")[:60]}
        missing = [q["i"] for q in chunk if q["i"] not in out]
        if missing:
            raise LLMError(f"模型漏答了第 {missing} 题")
        return out
