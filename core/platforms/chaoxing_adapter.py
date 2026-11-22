"""学习通（chaoxing-suite）桥接层。

设计取舍
--------
学习通脚本套件已经在真机上验证过，核心逻辑（目录解析、视频绿标判定、失焦防护、
测验三段式）不要重写。这里只做「软件化封装」：

1. 用 PyInstaller 已经打进来的 node.exe + playwright-core 驱动常驻浏览器，
   使用者不需要装 Node / npm，也不需要另开命令行窗口；
2. 把脚本的 print 全部转发到界面日志，长任务能响应「停止当前任务」；
3. 扫描课程 / 读进度 / 刷视频 / 自动答题统一成和 AI 优课、zjy2 一样的返回结构，
   TaskRunner 与 GUI 不用为学习通写第二套逻辑；
4. 答题沿用套件的导出→填答管线，但「作答」由软件自己调用配置好的大模型完成，
   不依赖聊天窗口里的人工回复。

浏览器是**独立的 Chrome/Edge 窗口 + 独立 profile**（学习通要求有头、不能失焦），
和 AI 优课 / zjy2 共用的受控 Edge 分开，避免两套自动化的标签页互相干扰。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import USERDATA_DIR

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def suite_dir():
    """脚本套件目录：源码运行取项目内 chaoxing/，打包后取 _MEIPASS/chaoxing。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "chaoxing")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "chaoxing")


def _playwright_root():
    # 打包后必须优先用 _MEIPASS 里的 driver：PyInstaller 的模块 __file__ 有时
    # 指向解释器安装目录，那会导致 node.exe / index.mjs 找不到（用户机上没装 Python）。
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "playwright")
    try:
        import playwright
        return os.path.dirname(os.path.abspath(playwright.__file__))
    except Exception:
        return ""


def _node_executable():
    root = _playwright_root()
    node = os.path.join(root, "driver", "node.exe") if root else ""
    if node and os.path.exists(node):
        return node
    import shutil
    return shutil.which("node") or ""


def _playwright_entry():
    root = _playwright_root()
    entry = os.path.join(root, "driver", "package", "index.mjs") if root else ""
    if entry and os.path.exists(entry):
        return Path(entry).as_uri()
    return ""


def _find_edge():
    try:
        from core.browser import _find_edge as find
        return find()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# stdout 转发：脚本里所有 print 都进界面日志，同时顺手解析进度 / 二维码截图
# ---------------------------------------------------------------------------

class _LogWriter:
    PROGRESS_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")

    def __init__(self, bus):
        self.bus = bus
        self.buf = ""
        # 防重入：如果 bus.log 自己也往 stdout 写（第三方日志器、调试 print），
        # 转发链会自我递归直到 RecursionError。这里直接丢弃内层输出。
        self._busy = False

    def write(self, s):
        if not s or self._busy:
            return len(str(s))
        self.buf += str(s)
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line.rstrip())
        return len(s)

    def flush(self):
        if self.buf.strip():
            self._line(self.buf.strip())
        self.buf = ""

    def _line(self, line):
        if not line or self._busy:
            return
        self._busy = True
        try:
            self._emit(line)
        finally:
            self._busy = False

    def _emit(self, line):
        # 二维码出来就立刻推给「最新画面」，用户不用去文件夹里找截图
        m = re.search(r"二维码截图[:：]\s*(\S+\.png)", line)
        if m:
            self.bus.shot(m.group(1))
        p = self.PROGRESS_RE.search(line)
        if p:
            try:
                i, n = int(p.group(1)), int(p.group(2))
                if n:
                    self.bus.progress(min(99, int(i / n * 100)), line[:70])
            except Exception:
                pass
        level = "ok" if line.startswith(("已跳转", "导出完成", "=== ")) else "info"
        self.bus.log(line[:400], level)


# ---------------------------------------------------------------------------
# 大模型作答：把套件导出的题目交给用户配置的模型，输出套件认识的答案格式
# ---------------------------------------------------------------------------

LLM_SYSTEM = """你是高校章节测验答题助手。只根据题干、选项和给出的阅读材料作答，不要编造材料里没有的信息。
输出必须是严格的 JSON，不要解释、不要 Markdown 代码块：
{"format":"chaoxing-answers-v1","answers":[{"no":"11.6","q":1,"answer":"B"}]}
规则：
1. no 和 q 原样照抄输入，每道题恰好一条，不能漏题、不能加题；
2. 单选题 answer 为一个大写字母；多选题为字母数组，如 ["A","C"]；
3. 判断题 answer 只能是 "对" 或 "错"；
4. 填空/简答题 answer 为文本，多个空用数组按先后顺序给出；
5. 实在不会就选最可能的答案，不要留空。"""

LLM_USER = """课程：{course}

题目（JSON）：
{payload}

请按系统要求的格式输出全部题目的答案。"""


def _rows_to_payload(papers):
    """导出题目 → LLM 输入（按批送，避免单条消息过长）。"""
    rows = []
    for paper in papers:
        no = paper.get("no") or ""
        for q in paper.get("questions") or []:
            rows.append({
                "no": no,
                "q": q.get("q"),
                "type": q.get("type"),
                "passage": (q.get("passage") or "")[:3000],
                "stem": (q.get("stem") or "")[:2000],
                "options": [{"label": o.get("label"), "text": (o.get("text") or "")[:500]}
                            for o in (q.get("options") or [])],
            })
    return rows


def _normal_answer(question, raw):
    """按套件 build_plan 的约定归一化模型答案。"""
    typ = question.get("type")
    if typ in ("single", "multi"):
        letters = _norm_letters(raw)
        return letters[0] if typ == "single" and letters else letters
    if typ == "judge":
        return _norm_judge(raw) or "对"
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    return "" if raw is None else str(raw)


def _norm_letters(ans):
    if ans is None:
        return []
    if isinstance(ans, (list, tuple)):
        out = []
        for a in ans:
            out += _norm_letters(a)
        return out
    s = str(ans).strip().upper()
    s = s.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
    s = s.replace(".", "").replace("。", "").replace(" ", "")
    parts = [x for x in s.split(",") if x] if "," in s else (
        list(s) if re.fullmatch(r"[A-H]{2,}", s) else ([s] if s else []))
    out = []
    for part in parts:
        part = re.sub(r"[^A-H]", "", part)
        if part:
            out.append(part)
    return out


def _norm_judge(ans):
    s = str(ans).strip().lower()
    if s in ("对", "正确", "是", "true", "t", "√", "yes", "y", "a"):
        return "对"
    if s in ("错", "错误", "否", "false", "f", "×", "x", "no", "n", "b"):
        return "错"
    if "对" in s or "正确" in s or "true" in s:
        return "对"
    if "错" in s or "false" in s:
        return "错"
    return None


# ---------------------------------------------------------------------------
# 桥接主体
# ---------------------------------------------------------------------------

class ChaoxingSuite:
    def __init__(self, bus):
        self.bus = bus
        self.root = os.path.join(USERDATA_DIR, "chaoxing")
        for d in ("profile", "io", "shots"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        # 套件里 video.main() / exam.main() 这些入口会在内部无参 new Browser()，
        # 只认环境变量或套件默认目录。必须让它们和桥接层用同一个会话目录，
        # 否则 cmd.json 会写到另一处、永远等不到 agent 的回包（表现为卡死超时）。
        os.environ["BROWSER_ROOT"] = self.root
        self.proc = None
        self._pump = None
        self._modules = None

    # ---------- 套件模块（延迟导入，GUI 启动时不需要） ----------
    def modules(self):
        if self._modules is None:
            sd = suite_dir()
            if not os.path.isfile(os.path.join(sd, "client.py")):
                raise RuntimeError(f"学习通脚本套件不完整：{sd}")
            if sd not in sys.path:
                sys.path.insert(0, sd)
            import client
            import catalog
            import doc
            import run
            import video
            import exam
            import chapter_quiz
            client.set_stop_check(self.bus.should_stop)
            self._modules = dict(client=client, catalog=catalog, doc=doc, run=run,
                                 video=video, exam=exam, chapter_quiz=chapter_quiz)
        return self._modules

    def browser(self):
        m = self.modules()
        return m["client"].Browser(root=self.root)

    # ---------- 常驻驱动 ----------
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure_agent(self):
        if self.alive():
            return self.browser()
        node = _node_executable()
        entry = _playwright_entry()
        agent = os.path.join(suite_dir(), "agent.mjs")
        if not node or not os.path.exists(agent):
            raise RuntimeError("找不到内嵌的浏览器驱动，请重新安装 StudyBuddy")
        # 旧 cmd.json 会被新驱动当成新指令重放，启动前必须清掉
        for name in ("cmd.json", "result.json", "state.json"):
            try:
                os.remove(os.path.join(self.root, "io", name))
            except OSError:
                pass
        env = os.environ.copy()
        env["BROWSER_ROOT"] = self.root
        if entry:
            env["SB_PW_ENTRY"] = entry
        edge = _find_edge()
        if edge and not env.get("CHROME_PATH"):
            env["CHROME_PATH"] = edge
        env["NO_COLOR"] = "1"
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.bus.log("正在启动学习通浏览器（独立窗口，扫码或账号密码都可以）…")
        self.proc = subprocess.Popen(
            [node, agent], cwd=suite_dir(), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
        self._pump = threading.Thread(target=self._pump_agent, daemon=True)
        self._pump.start()
        deadline = time.time() + 45
        state_path = os.path.join(self.root, "io", "state.json")
        stage = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("学习通浏览器驱动启动失败，请看上面的日志")
            try:
                with open(state_path, encoding="utf-8") as f:
                    stage = (json.load(f) or {}).get("stage")
                if stage in ("qr-ready", "logged-in", "nav-error"):
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if self.proc.poll() is None:
            if stage == "logged-in":
                # 带着上次的登录态进来：别把登录页截图当成「最新画面」推给用户，会误以为又要扫码
                self.bus.log("检测到上次的登录状态，已免登录进入学习通")
            else:
                shot = os.path.join(self.root, "shots", "01-login.png")
                if os.path.exists(shot):
                    self.bus.shot(shot)
        return self.browser()

    def save_session(self):
        """把当前 cookie 立刻落盘（不等浏览器正常退出）。

        真机验证过：登录成功后如果进程是被强杀/管道断开带走的，Chromium 不会
        把 passport2 的登录 cookie 写进 profile，下一次启动又得重新扫码。
        """
        try:
            b = self.browser()
            r = b.send({"type": "save-state"}, timeout=10)
            if r.get("ok"):
                n = (r.get("data") or {}).get("count")
                self.bus.log(f"已保存登录状态（{n} 条 cookie），下次启动免登录")
            return bool(r.get("ok"))
        except Exception:
            return False

    # 驱动的每条指令都会回一行「<id> <type> OK」，一次刷课几百条，
    # 全塞进界面日志会把有用的信息顶走。只保留出错与关键阶段。
    _DRIVER_NOISE = re.compile(r"^c\d+-\d+ \w+ OK\s*$")

    def _pump_agent(self):
        try:
            for line in self.proc.stdout or ():
                line = line.rstrip()
                if not line or self._DRIVER_NOISE.match(line):
                    continue
                if " ERR " in line or line.startswith(("QR READY", "LOGGED IN", "SESSION", "NAV ERROR")):
                    self.bus.log("[driver] " + line[:300],
                                 "error" if " ERR " in line else "info")
        except Exception:
            pass

    def close(self):
        """退出程序 / 关闭浏览器：先发 quit 让 cookie 落盘，再兜底 kill。"""
        try:
            self.modules()["client"].set_stop_check(None)  # 关闭动作不能被停止标志拦住
        except Exception:
            pass
        if not self.alive():
            self.proc = None
            return
        try:
            self.browser().send({"type": "quit"}, timeout=8)
        except Exception:
            pass
        for _ in range(20):
            if self.proc.poll() is not None:
                break
            time.sleep(0.25)
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.bus.log("学习通浏览器已关闭")

    # ---------- 通用调用 ----------
    def _call(self, fn, *args, **kwargs):
        """在 stdout 转日志的上下文里调用套件函数。"""
        # 关闭驱动时会临时摘掉停止钩子，这里每次调用重新挂上，避免后续任务无法停止
        if self._modules is not None:
            self._modules["client"].set_stop_check(self.bus.should_stop)
        old = sys.stdout
        sys.stdout = _LogWriter(self.bus)
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            sys.stdout = old

    def _open_course(self, b, name):
        m = self.modules()
        sess = self._call(m["run"].cmd_open, b, name)
        self.bus.shot(os.path.join(self.root, "shots", "course-page.png"))
        return sess

    # ---------- 业务动作 ----------
    def login(self, wait_seconds=180):
        self.ensure_agent()
        b = self.browser()
        res = self._call(self.modules()["run"].cmd_login, b, wait_s=wait_seconds)
        if isinstance(res, dict) and res.get("stopped"):
            return "已停止等待登录"
        if isinstance(res, dict) and res.get("logged_in") is False:
            raise RuntimeError("看起来还没登录成功（仍停在登录页）。"
                               "请重新点「① 启动浏览器 / 登录」，在弹出窗口里扫码或用账号密码登录")
        self.save_session()
        return "学习通已登录"

    def login_with_password(self, user, password, wait_seconds=180):
        """账号密码登录。

        平台会弹滑块：驱动自动填表并提交，出现滑块时日志提示用户拖一次，
        程序继续轮询，成功即返回。**不要让异常吞掉提示**，否则用户只会看到「没反应」。
        """
        self.ensure_agent()
        b = self.browser()
        self.bus.log("正在填入账号密码并提交…")
        res = self._call(self.modules()["run"].cmd_login_password, b,
                         user, password, wait_s=wait_seconds)
        if not isinstance(res, dict):
            raise RuntimeError("登录返回异常：%r" % (res,))
        if res.get("reason") == "stopped":
            return "已停止等待登录"
        if res.get("logged_in"):
            try:
                self.bus.shot(os.path.join(self.root, "shots", "login-ok.png"))
            except Exception:
                pass
            self.save_session()
            return "学习通已登录（账号密码）"
        raise RuntimeError("账号密码登录没成功：%s。可点「扫码 / 手动登录」，"
                           "在弹出的窗口里自己登录一次" % (res.get("reason") or "未知原因"))

    def list_courses(self):
        self.ensure_agent()
        b = self.browser()
        try:
            courses = self._call(self.modules()["run"].list_courses, b)
        except RuntimeError as e:
            if "没解析到课程链接" in str(e) or "个人空间框架" in str(e):
                raise RuntimeError("学习通没读到课程列表，可能还没登录。请点「① 启动浏览器 / 登录」扫码") from e
            raise
        items = []
        for c in courses:
            total, done = c.get("total"), c.get("done")
            brushable = bool(total and total > 0)
            note = "可刷" if brushable else "无课"
            name = (c.get("name") or "").strip() or ("课程" + str(c.get("cid")))
            items.append({"name": name, "brushable": brushable, "note": note,
                          "progress": f"{done}/{total}" if total else ""})
        return items

    def read_progress(self, course_name):
        self.ensure_agent()
        b = self.browser()
        self._open_course(b, course_name)
        m = self.modules()
        cat = m["catalog"].parse(browser=b, save=True)
        p = cat.get("progress") or {}
        if p.get("total"):
            return int(p.get("done") or 0), int(p["total"]), ""
        items = cat.get("items") or []
        if not items:
            return None, 0, "目录里没读到任务点（课程为空或页面还没加载完）"
        done = sum(1 for x in items if x.get("state") == "done")
        return done, len(items), "按目录条目估算（页面没有任务点进度表头）"

    def brush_course(self, course_name):
        self.ensure_agent()
        b = self.browser()
        self._open_course(b, course_name)
        m = self.modules()
        self.bus.progress(5, "正在解析目录…")
        try:
            old_argv = sys.argv
            sys.argv = ["video.py", "--pending", "--skip-quiz"]
            try:
                self._call(m["video"].main)
            except SystemExit as e:
                if e.code == 3:
                    raise RuntimeError("学习通浏览器驱动不可用，请重新点「① 启动浏览器 / 登录」")
        finally:
            sys.argv = old_argv

        path = os.path.join(self.root, "io", "video-progress.json")
        try:
            with open(path, encoding="utf-8") as f:
                results = json.load(f) or []
        except Exception:
            results = []
        good = {"done", "video-done", "skip-done", "doc-done", "doc-flushed"}
        ok = sum(1 for r in results if r.get("status") in good)
        bad = [r for r in results if r.get("status") in
               ("timeout", "openfail", "unconfirmed", "fillerror", "doc-unconfirmed",
                "limit", "novideo", "tabfail")]
        limit_hit = any(r.get("status") == "limit" for r in results)
        quiz = []
        try:
            with open(os.path.join(self.root, "io", "remaining.json"), encoding="utf-8") as f:
                quiz = (json.load(f) or {}).get("quiz") or []
        except Exception:
            pass
        done, total, _ = (None, None, "")
        try:
            cat = m["catalog"].parse(browser=b, save=True)
            p = cat.get("progress") or {}
            if p.get("total"):
                done, total = int(p.get("done") or 0), int(p["total"])
            else:
                items = cat.get("items") or []
                total = len(items) or None
                done = sum(1 for x in items if x.get("state") == "done") or None
        except Exception:
            pass
        aborted = bool(self.bus.should_stop()) or (len(results) >= 3 and len(bad) >= 2)
        if limit_hit:
            self.bus.log("平台提示「今日视频任务点完成数已达上限」：视频今天再刷也不会计完成，"
                         "明天再跑剩下的就行。文档类任务点不受这个限制。", "warn")
        self.bus.progress(100, "刷课结束")
        return {"todo": len(results), "ok": ok, "aborted": aborted, "quiz": quiz,
                "limit": limit_hit,
                "done_after": done, "total_after": total}

    def answer_quiz(self, course_name, dry_run=False, auto_submit=True):
        """导出题目（含章节内嵌测验）→ 软件调大模型 → 自动填答。"""
        from config import llm_config
        from core.llm import LLMClient, LLMError, _extract_json

        cfg = llm_config()
        llm = LLMClient(cfg.get("base"), cfg.get("key"), cfg.get("model"),
                        cfg.get("timeout", 90))
        if not llm.ready():
            raise RuntimeError("还没配置大模型：点左下角「⚙ 大模型设置」填接口地址 / Key / 模型名")

        self.ensure_agent()
        b = self.browser()
        self._open_course(b, course_name)
        m = self.modules()
        catalog = m["catalog"].parse(browser=b, save=True)
        items = catalog.get("items") or []

        standalone = [x for x in items if x.get("state") == "pending"
                      and m["exam"].is_quiz(x.get("title")) and x.get("pending")]
        chapters = [x["no"] for x in items if x.get("state") == "pending"
                    and not m["exam"].is_quiz(x.get("title")) and x.get("pending")]
        self.bus.log(f"待答题：独立测验 {len(standalone)} 套，章节内嵌测验候选 {len(chapters)} 章")

        outdir = os.path.join(self.root, "io", "exports")
        os.makedirs(outdir, exist_ok=True)
        paths = []
        if standalone:
            self.bus.progress(10, f"正在采集 {len(standalone)} 套测验题目…")
            paths.append(self._call(m["exam"].export, b, standalone, outdir))
        if chapters:
            self.bus.progress(20, "正在检查章节内嵌测验…")
            got = self._call(m["chapter_quiz"].export_nos, b, chapters, outdir)
            if got:
                paths.append(got)

        papers = []
        seen = set()
        for bundle in paths:
            jpath = bundle[0] if isinstance(bundle, (tuple, list)) else bundle
            try:
                with open(jpath, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                continue
            for paper in data.get("papers") or []:
                no = paper.get("no")
                if no in seen:
                    continue
                seen.add(no)
                papers.append(paper)
        questions = _rows_to_payload(papers)
        if not questions:
            return {"todo": 0, "ok": 0, "items": []}
        self.bus.log(f"共采到 {len(papers)} 套 / {len(questions)} 道题，软件正在调用大模型作答…")

        # 软件自己调模型，按 no+q 对齐套件的答案格式
        answers, answer_rows = {}, []
        batch = 6
        for start in range(0, len(questions), batch):
            if self.bus.should_stop():
                break
            chunk = questions[start:start + batch]
            user = LLM_USER.format(course=course_name,
                                   payload=json.dumps(chunk, ensure_ascii=False))
            data = None
            for attempt in range(2):
                try:
                    text = llm.chat(user if attempt == 0 else
                                    user + "\n\n注意：上一次输出不是合法 JSON，这次只输出 JSON。",
                                    system=LLM_SYSTEM, max_tokens=2048)
                    data = _extract_json(text)
                except LLMError as e:
                    self.bus.log(str(e), "error")
                    return {"todo": len(questions), "ok": 0, "items": [], "error": str(e)}
                if isinstance(data, dict) and isinstance(data.get("answers"), list):
                    break
                data = None
            if not data:
                raise RuntimeError(f"第 {start + 1}~{start + len(chunk)} 题模型没返回合法 JSON")
            by_key = {}
            for a in data["answers"]:
                try:
                    by_key[(str(a.get("no") or "").strip(), int(a.get("q")))] = a.get("answer")
                except Exception:
                    continue
            for q in chunk:
                key = (q["no"], q["q"])
                raw = by_key.get(key)
                value = _normal_answer(q, raw)
                if value in (None, "", []):
                    raise RuntimeError(f"模型漏答：{q['no']} 第 {q['q']} 题，"
                                       "为避免空卷交上去已停止，请重试或检查题目")
                answers[key] = value
                answer_rows.append({"no": q["no"], "q": q["q"], "answer": value})
            pct = 20 + int(40 * min(start + batch, len(questions)) / len(questions))
            self.bus.progress(pct, f"大模型已作答 {min(start + batch, len(questions))}/{len(questions)} 题")
            time.sleep(0.3)

        if self.bus.should_stop():
            return {"todo": len(questions), "ok": 0, "items": [], "stopped": True}

        stamp = time.strftime("%Y%m%d-%H%M%S")
        answer_path = os.path.join(outdir, f"answers-auto-{stamp}.json")
        with open(answer_path, "w", encoding="utf-8") as f:
            json.dump({"format": "chaoxing-answers-v1", "answers": answer_rows},
                      f, ensure_ascii=False, indent=1)
        self.bus.log(f"答案已保存：{answer_path}")

        if dry_run:
            preview = "；".join(f"{r['no']}#{r['q']}={r['answer']}" for r in answer_rows[:12])
            self.bus.log(f"试运行：已解析 {len(answer_rows)} 题，未点选未提交。预览：{preview}")
            return {"todo": len(questions), "ok": 0, "items": papers}

        submitted, results = 0, []
        for key_name, fn in (("single", m["exam"].answer_file),
                             ("embedded", m["chapter_quiz"].answer_file)):
            if self.bus.should_stop():
                break
            rows = [r for r in answer_rows if
                    (("#" in r["no"]) == (key_name == "embedded"))]
            if not rows:
                continue
            part = os.path.join(outdir, f"answers-auto-{stamp}-{key_name}.json")
            with open(part, "w", encoding="utf-8") as f:
                json.dump({"format": "chaoxing-answers-v1", "answers": rows},
                          f, ensure_ascii=False, indent=1)
            self.bus.log(f"正在{'填答并提交' if auto_submit else '填答（不提交）'}：{len(rows)} 题…")
            got = self._call(fn, b, part, dry=not auto_submit, force=False) or []
            results += got
            submitted += sum(1 for r in got if r.get("status") == "submitted")
        if not auto_submit:
            self.bus.log("设置里关闭了「填完自动提交」：答案已填入页面，请人工核对后自行提交", "warn")
        self.bus.progress(100, "答题结束")
        return {"todo": len(questions), "ok": submitted, "items": results}
