"""作业 / 测验 / 考试自动答题（大模型作答）。

页面结构说明
------------
ai.icve 的课程页是 SPA，三个入口对应三条路由（实测）：
    /work  作业      /test  测验      /exam  考试
导航栏那几个「作业 / 测验 / 考试」是 <label> 包在 <li> 里，JS 直接 `.click()`
不触发路由，所以这里统一用 `goto` 直达路由 —— 比点导航稳。

题目 DOM 没有稳定 class（不同题库页面不一样），所以解析走**结构特征**：
- 选择题：`input[type=radio|checkbox]` 天然按 `name` 分组，一组即一题；
  选项文字取最近的 `label`（没有就取父级/兄弟），题干 = 题目容器文本减去选项文字；
- 填空/简答：`input[type=text]` / `textarea`，按最近的块级祖先分组，多个空视为一题；
- 作答：点选项的 `label`（框架才认），填文本用原生 setter + input/change 事件；
- 提交：按「提交 / 交卷 / 提交作业」按钮，再吃掉一层「确定」确认框。

安全设计
--------
- `dry_run=True`：只解析 + 问模型 + 打印答案，**不点选、不提交**；
  第一次在真实课程上用，先跑这个确认解析对不对。
- 考试默认不参与（需要显式勾选），因为考试通常限时、且提交后不可改。
- 每题作答后会回读控件状态，没勾上就换方式重点，绝不「假装答了」。
"""
import json
import re
import time

NAV = {"work": "作业", "test": "测验", "exam": "考试"}
NAV_ORDER = ["work", "test", "exam"]

# 列表页：抓「作业/测验/考试」条目
_LIST_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const rows = [];
  for (const li of document.querySelectorAll('li')) {
    if (!vis(li)) continue;
    const t = (li.innerText || '').trim();
    if (!t || t.length > 400) continue;
    if (!/已完成|未完成|未提交|进行中|待提交|未开始|未作答/.test(t)) continue;
    // 有可点的「去作答 / 开始作答」才算能做的
    const btns = [...li.querySelectorAll('a,button,span,div')].filter(e =>
        e.children.length === 0 && vis(e) &&
        /^(去作答|开始作答|进入作答|继续作答|开始答题|去答题|作答)$/.test((e.innerText || '').trim()));
    rows.push({
      text: norm(t).slice(0, 300),
      canAnswer: btns.length > 0,
      done: /已完成/.test(t),
      btnText: btns.length ? btns[0].innerText.trim() : ''
    });
  }
  // 去重（同一行可能被内外层各抓一次）
  const seen = new Set(), out = [];
  for (const r of rows) {
    if (seen.has(r.text)) continue;
    seen.add(r.text);
    out.push(r);
  }
  return out;
}
"""

# 作答页：解析题目
_READ_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();

  window.__sbQuizInputs = window.__sbQuizInputs || [];
  const inputs = [];
  for (const el of document.querySelectorAll('input[type=radio],input[type=checkbox]')) {
    if (!vis(el)) continue;
    el.__sbIdx = inputs.length;
    inputs.push(el);
  }
  window.__sbQuizInputs = inputs;

  // 按 name 分组（同组 = 同一题）
  const groups = new Map();
  inputs.forEach(el => {
    const key = el.name || ('__noname_' + el.__sbIdx);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(el);
  });

  const questionBox = (els) => {
    let box = els[0].parentElement;
    let p = box;
    for (let i = 0; i < 8 && p && p.parentElement; i++) {
      const up = p.parentElement;
      const cnt = up.querySelectorAll('input[type=radio],input[type=checkbox]').length;
      if (cnt > els.length) break;       // 再往上就会混进别的题
      box = up; p = up;
    }
    return box;
  };

  const out = [];
  for (const [name, els] of groups) {
    const box = questionBox(els);
    const options = els.map((el, k) => {
      let holder = el.closest('label');
      let t = '';
      if (holder) t = norm(holder.innerText);
      if (!t) t = norm((el.parentElement || {}).innerText);
      if (!t) t = norm((el.nextElementSibling || {}).innerText);
      return {k: String.fromCharCode(65 + k), text: t.slice(0, 300), idx: el.__sbIdx};
    });
    let stem = norm(box ? box.innerText : '');
    for (const o of options) if (o.text) stem = stem.split(o.text).join(' ');
    stem = norm(stem).slice(0, 800);
    const mark = stem.match(/单选题?|多选题?|判断题?|不定项/);
    out.push({
      type: els[0].type === 'checkbox' ? 'multi' : 'single',
      kindText: mark ? mark[0] : '',
      stem, options
    });
  }

  // 填空 / 简答：按最近的块分组
  const texts = [...document.querySelectorAll('input[type=text],textarea')].filter(vis);
  window.__sbQuizTexts = texts;
  const tgroups = new Map();
  texts.forEach((el, i) => {
    // 题目容器优先找带题干的那个块（q-item / timu / question），别只取到 <p>
    const box = el.closest('li,tr,[class*=item],[class*=timu],[class*=question],[class*=subject]')
                || el.closest('p,div') || el.parentElement;
    const key = tgroups.size && [...tgroups.keys()].includes(box) ? box : box;
    if (!tgroups.has(key)) tgroups.set(key, []);
    tgroups.get(key).push(el);
    el.__sbIdx = i;
  });
  const fills = [];
  for (const [box, els] of tgroups) {
    let stem = norm(box ? box.innerText : '');
    if (stem.length < 4) stem = norm((els[0].placeholder || '')) || '填空题';
    fills.push({
      type: els[0].tagName === 'TEXTAREA' ? 'essay' : 'fill',
      kindText: '',
      stem: stem.slice(0, 800),
      options: [],
      blanks: els.length
    });
  }

  return {choice: out, fill: fills, total: out.length + fills.length};
}
"""

# 作答：点选项 / 填文本
_FILL_JS = r"""
(payload) => {
  const results = [];
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    try { desc.set.call(el, v); } catch (e) { el.value = v; }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true}));
  };

  for (const a of payload.choices) {
    let ok = false;
    for (const idx of a.idxs) {
      const el = (window.__sbQuizInputs || [])[idx];
      if (!el) continue;
      if (el.checked === a.want) { ok = true; continue; }
      const holder = el.closest('label') || el.parentElement;
      try { (holder || el).click(); } catch (e) {}
      if (el.checked !== a.want) { try { el.click(); } catch (e) {} }
      if (el.checked === a.want) ok = true;
    }
    results.push({i: a.i, ok});
  }

  for (const f of payload.fills) {
    const target = (window.__sbQuizTexts || [])[f.idx];
    if (!target) { results.push({i: f.i, ok: false}); continue; }
    setVal(target, f.text || '');
    results.push({i: f.i, ok: target.value === (f.text || '')});
  }
  return results;
}
"""

_SUBMIT_TEXTS = ["提交作业", "提交答案", "交卷", "提交", "保存并提交"]


class QuizAI:
    """自动答题器：一个实例对应一次任务（Browser 连接的生命周期）。"""

    def __init__(self, br, bus=None, llm=None):
        self.br = br
        self.bus = bus
        self.p = br.page
        self.llm = llm
        self.course = ""

    # ---------- 基础 ----------
    def _log(self, m, level="info"):
        if self.bus:
            self.bus.log(m, level)

    def _stopped(self):
        return self.bus.should_stop() if self.bus else False

    def base_url(self):
        """当前课程的基础地址（到 classId 为止）。"""
        url = self.br.current_url()
        for seg in ("/work", "/test", "/exam", "/notices", "/kcnr", "/homework"):
            if url.endswith(seg):
                return url[: -len(seg)]
        parts = url.split("/")
        if len(parts) > 6:
            return "/".join(parts[:7])
        return url.rstrip("/")

    def go(self, kind):
        """跳到 作业 / 测验 / 考试 列表页。"""
        url = self.base_url() + "/" + kind
        self.br.goto(url)
        self.br.wait(3500)
        return self.br.current_url().rstrip("/").endswith("/" + kind)

    def list_items(self):
        try:
            return self.p.evaluate(_LIST_JS) or []
        except Exception:
            return []

    # ---------- 作答页 ----------
    def read_questions(self):
        try:
            return self.p.evaluate(_READ_JS) or {}
        except Exception:
            return {}

    def ask(self, parsed):
        """把解析结果整理成模型输入；返回 {i: answer}。"""
        qs, index = [], {}
        i = 0
        for q in parsed.get("choice", []):
            i += 1
            opts = [{"k": o["k"], "text": o["text"]} for o in q["options"]]
            qtype = q["type"]
            if q["kindText"] and "判断" in q["kindText"]:
                qtype = "judge"
            qs.append({"i": i, "type": qtype, "stem": q["stem"], "options": opts})
            index[i] = {"kind": "choice", "q": q}
        for q in parsed.get("fill", []):
            i += 1
            qs.append({"i": i, "type": q["type"], "stem": q["stem"], "options": []})
            index[i] = {"kind": "fill", "q": q}
        if not qs:
            return {}, index, qs
        answers = self.llm.answer(qs, course=self.course)
        return answers, index, qs

    def fill(self, answers, index):
        """按答案点选/填空，返回 (成功题数, 总题数)。

        选择题：单选只点一个，多选逐个点（包括把不该选的确认成未选）。
        填空/简答：`__sbIdx` 是解析时按页面顺序编号的，这里按同样顺序累加偏移。
        """
        choices = []
        for i, meta in index.items():
            if meta["kind"] != "choice":
                continue
            q = meta["q"]
            want = set((answers.get(i) or {}).get("keys") or [])
            if not want:
                continue
            pick = [o for o in q["options"] if o["k"] in want]
            if not pick:
                continue
            for o in q["options"]:
                # 单选只认第一个命中项，其余一律“不选”
                chosen = (o["k"] in want) and (q["type"] != "single" or o is pick[0])
                choices.append({"i": i, "idxs": [o["idx"]], "want": chosen})

        fills, offset = [], 0
        for i in sorted(index):
            meta = index[i]
            if meta["kind"] != "fill":
                continue
            q = meta["q"]
            n = q.get("blanks") or 1
            text = (answers.get(i) or {}).get("text") or ""
            parts = [t.strip() for t in text.split("||")] if "||" in text else [text]
            for k in range(n):
                val = parts[k] if k < len(parts) else (parts[-1] if parts else "")
                fills.append({"i": i, "idx": offset + k, "text": val})
            offset += n

        try:
            res = self.p.evaluate(_FILL_JS, {"choices": choices, "fills": fills}) or []
        except Exception as e:
            self._log(f"  作答动作出错：{e}", "error")
            return 0, len(index)
        done = {r["i"] for r in res if r.get("ok")}
        return len(done), len(index)

    def submit(self):
        """点提交，并吃掉确认框。返回是否点到提交按钮。"""
        for label in _SUBMIT_TEXTS:
            try:
                loc = self.p.get_by_text(label, exact=True)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=5000)
                self.br.wait(1800)
                break
            except Exception:
                continue
        else:
            return False
        # 确认框
        for label in ("确定", "确认", "是", "提交"):
            try:
                loc = self.p.get_by_text(label, exact=True)
                if loc.count():
                    loc.first.click(timeout=3000)
                    self.br.wait(2500)
                    break
            except Exception:
                continue
        return True

    def read_score(self):
        """提交后页面上会显示得分，读回来做记录。"""
        try:
            return self.p.evaluate(
                """() => {
                    const t = document.body.innerText || '';
                    const m = t.match(/得分[:：]?\\s*(\\d+(?:\\.\\d+)?)/);
                    const m2 = t.match(/(\\d+(?:\\.\\d+)?)\\s*分/);
                    return m ? m[1] : (m2 ? m2[1] : '');
                }"""
            ) or ""
        except Exception:
            return ""

    # ---------- 主流程 ----------
    def run(self, kinds=("work", "test"), auto_submit=True, dry_run=False,
            max_items=20, progress_cb=None):
        if not self.llm or not self.llm.ready():
            self._log("还没配置大模型：请先点左下角「⚙ 大模型设置」填接口地址 / Key / 模型名", "error")
            return {"ok": 0, "todo": 0, "items": []}

        summary = {"ok": 0, "todo": 0, "items": [], "dry": dry_run}
        for kind in kinds:
            if self._stopped():
                break
            label = NAV.get(kind, kind)
            if not self.go(kind):
                self._log(f"{label}：页面没打开（可能这门课没有这个入口）", "warn")
                continue
            rows = self.list_items()
            pending = [r for r in rows if r["canAnswer"] and not r["done"]]
            self._log(f"{label}：共 {len(rows)} 条，可作答 {len(pending)} 条")
            if not rows:
                self._log(f"  {label}列表没读到条目（页面结构可能变了），已截图", "warn")
                self.br.shot(f"quiz_{kind}_empty")
                continue
            if not pending:
                continue

            for n, row in enumerate(pending[:max_items], 1):
                if self._stopped():
                    break
                if progress_cb:
                    progress_cb(n, len(pending), row["text"][:30])
                name = self._row_name(row["text"])
                self._log(f"[{n}/{len(pending)}] {label}：{name}")
                if not self.open_row(row):
                    self._log("  点不进去（可能已完成或没权限），跳过", "warn")
                    continue
                self.br.wait(2500)
                parsed = self.read_questions()
                total = parsed.get("total") or 0
                if not total:
                    self._log("  没解析到题目：可能页面还没加载完或结构变了", "warn")
                    self.br.shot(f"quiz_{kind}_noparse")
                    continue
                self._log(f"  解析到 {total} 题（选择 {len(parsed.get('choice', []))}，"
                          f"填空/简答 {len(parsed.get('fill', []))}）")
                try:
                    answers, index, qs = self.ask(parsed)
                except Exception as e:
                    self._log(f"  大模型答题失败：{e}", "error")
                    continue

                if dry_run:
                    for i in sorted(answers)[:5]:
                        a = answers[i]
                        q = next((x for x in qs if x["i"] == i), {})
                        self._log(f"  · 第{i}题（{q.get('type')}）→ "
                                  f"{','.join(a.get('keys') or []) or a.get('text', '')[:30]}"
                                  f"  {a.get('reason', '')}")
                    self._log("  试运行模式：只解析不提交，请核对上面的题目与答案是否对得上")
                    summary["items"].append({"name": name, "total": total, "dry": True})
                    continue

                done, all_q = self.fill(answers, index)
                self._log(f"  作答 {done}/{all_q} 题")
                if done < all_q:
                    self._log("  有题没勾上，已截图，提交前请人工确认", "warn")
                    self.br.shot(f"quiz_{kind}_partial")
                if auto_submit:
                    if self.submit():
                        self.br.wait(2500)
                        score = self.read_score()
                        self._log(f"  已提交" + (f"，得分 {score}" if score else ""))
                        summary["ok"] += 1
                        summary["items"].append({"name": name, "total": all_q, "score": score})
                    else:
                        self._log("  没找到提交按钮，已停在作答页，请手动提交", "warn")
                        self.br.shot(f"quiz_{kind}_nosubmit")
                else:
                    self._log("  已填好，未提交（按设置保留了提交前人工确认）")
                    summary["items"].append({"name": name, "total": all_q, "score": ""})
                summary["todo"] += 1
        return summary

    # ---------- 列表点击 ----------
    @staticmethod
    def _row_name(text):
        t = re.sub(r"^(已完成|未完成|未提交|进行中|待提交|未开始)\s*", "", text.strip())
        t = re.sub(r"总分[:：].*$", "", t).strip()
        return (t or text)[:40]

    def open_row(self, row):
        """点开列表里的这一条：优先点「去作答」，否则点标题。"""
        try:
            before = self.br.current_url()
            clicked = self.p.evaluate(
                """(t) => {
                    const vis = e => e && e.getBoundingClientRect().width > 2;
                    const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
                    for (const li of document.querySelectorAll('li')) {
                        if (!vis(li)) continue;
                        if (norm(li.innerText).slice(0, 300) !== t) continue;
                        const btn = [...li.querySelectorAll('a,button,span,div')].find(e =>
                            e.children.length === 0 && vis(e) &&
                            /^(去作答|开始作答|进入作答|继续作答|开始答题|去答题|作答)$/.test(norm(e.innerText)));
                        if (btn) { btn.click(); return 'btn'; }
                        li.click(); return 'row';
                    }
                    return '';
                }""",
                row["text"],
            )
            self.br.wait(2500)
            return bool(clicked) and self.br.current_url() != before
        except Exception:
            return False
