"""任务编排：在后台线程执行各功能，通过 EventBus 向 GUI 汇报。

线程模型（v1.0.1）
--------------------------------
- 每个按钮点击都开一个新线程执行，便于随时「停止」和实时刷进度。
- Playwright 同步 API 有线程亲和性，所以 **Browser 连接必须在本线程创建、本线程使用、
  本线程销毁**。`_wrap()` 的 finally 会调用 `detach()` 断开连接（不关浏览器进程）。
- Edge 进程本身跨线程共享，由 `EdgeProc` 单例管理，登录态靠固定 user-data-dir 保持。
"""
import threading
import traceback

from core.browser import Browser, EdgeProc
from core.platforms.ai_icve import AiIcve
from core.platforms.zjy2 import Zjy2


class TaskRunner:
    def __init__(self, bus, on_finish=None):
        self.bus = bus
        self.on_finish = on_finish
        self.thread = None
        self.browser = None          # 仅当前任务线程内有效
        self._edge_started = False   # 本进程是否拉起过 Edge

    # ---------- 控制 ----------
    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.bus.request_stop()
        self.bus.log("已请求停止，等待当前步骤收尾…", "warn")

    def start(self, fn, *args, **kwargs):
        if self.is_running():
            self.bus.log("已有任务在运行", "warn")
            return False
        self.bus.reset_stop()
        self.thread = threading.Thread(target=self._wrap, args=(fn, args, kwargs), daemon=True)
        self.thread.start()
        return True

    def _wrap(self, fn, args, kwargs):
        ok = True
        summary = ""
        try:
            summary = fn(*args, **kwargs) or ""
        except Exception as e:
            ok = False
            summary = f"出错：{e}"
            self.bus.log(self._explain(e), "error")
            self.bus.log(traceback.format_exc(), "error")
        finally:
            # 关键：任务线程结束前必须断开本线程的连接，否则下一次复用会 TargetClosedError
            if self.browser is not None:
                try:
                    self.browser.release_guards()   # 结算「自动关弹窗/静音」日志并解除强制静音
                except Exception:
                    pass
                try:
                    self.browser.detach()
                except Exception:
                    pass
                self.browser = None
            if self.on_finish:
                try:
                    self.on_finish()
                except Exception:
                    pass
            self.bus.done(ok, str(summary))

    @staticmethod
    def _explain(e):
        """把 Playwright 的英文异常翻译成用户能看懂的提示。"""
        msg = str(e)
        if "TargetClosedError" in msg or "Playwright connection closed" in msg:
            return ("浏览器连接已断开（可能 Edge 被手动关闭）。已自动清理，请重新点「① 启动浏览器 / 登录」。")
        if "Executable doesn't exist" in msg:
            return "找不到 Edge 可执行文件，请确认本机已安装 Microsoft Edge。"
        if "ECONNREFUSED" in msg or "connect_over_cdp" in msg:
            return "无法连接到浏览器调试端口，请重新点「① 启动浏览器 / 登录」。"
        if "Timeout" in msg and "goto" in msg:
            return "页面加载超时，请检查网络或稍后重试。"
        return msg

    # ---------- 浏览器 ----------
    def ensure_browser(self, attach=False):
        """确保**当前任务线程**有一个可用的浏览器连接。

        每次调用都检查连接健康度；本任务内复用，跨任务绝不复用。
        """
        if self.browser is not None and self.browser.alive():
            return self.browser
        if self.browser is not None:
            self.browser.detach()
            self.browser = None

        br = Browser(self.bus)
        br.connect(launch=True, log=None)
        self._edge_started = True
        self.browser = br
        return br

    def close_browser(self):
        """关闭受控 Edge（用户主动点「关闭浏览器」，故允许连带关掉复用的窗口）。"""
        if self.browser is not None:
            try:
                self.browser.detach()
            except Exception:
                pass
            self.browser = None
        EdgeProc.kill(force_any=True)
        self._edge_started = False
        self.bus.log("浏览器已关闭")

    def shutdown(self, wait=8.0):
        """退出程序时的收尾（v1.4.1）。

        和 `close_browser()` 的关键区别：**绝不去 detach `self.browser`**。
        那个 Playwright 对象属于任务线程，从别的线程（尤其退出时的主线程）去
        `pw.stop()` 会卡住甚至报错；正确做法是只发停止信号，让任务线程在
        `_wrap()` 的 finally 里自己收尾。

        步骤：请求停止 → 最多等 wait 秒让任务线程收尾 → 关 Edge 进程。
        Edge 优雅关闭（先 WM_CLOSE 让 cookie 落盘）本身要几秒，
        所以调用方必须放到后台线程里跑，不能占着 GUI 主线程。
        """
        try:
            self.bus.request_stop()
        except Exception:
            pass
        t = self.thread
        if t is not None and t.is_alive():
            t.join(timeout=wait)
        self.browser = None          # 不碰别人线程的对象，交给进程退出去清理
        EdgeProc.kill(grace=3, force_any=True)
        self._edge_started = False

    # ---------- 业务动作（供 GUI 调用） ----------
    def action_open_site(self, site_home, attach=False):
        br = self.ensure_browser()
        br.goto(site_home)
        br.wait(2500)
        br.shot("open_site")
        self.bus.log(f"已打开：{site_home}")
        return "已打开页面"

    def action_read_time(self, platform="ai"):
        br = self.ensure_browser()
        plat = AiIcve(br, self.bus) if platform == "ai" else Zjy2(br, self.bus)
        if platform == "ai":
            h, c = plat.read_watch()
            if h is None:
                self.bus.log("未能读取到观看时长，请确认已登录且页面正常", "warn")
                return "读取失败"
            self.bus.log(f"观看时长 {h} 时，学习次数 {c}")
            return f"观看时长 {h} 时 / {c} 次"
        return "zjy2 无观看时长统计"

    def action_brush_course(self, platform="ai", course_name="大学生心理健康"):
        br = self.ensure_browser()
        br.install_guards(prefer_resume=True)
        self.bus.log("刷课期间：播放器自动静音，续播弹窗自动点「继续播放」")

        def cb(i, n, name):
            self.bus.progress(int(i / max(1, n) * 100), f"{i}/{n} {name[:30]}")

        if platform == "ai":
            plat = AiIcve(br, self.bus)
            if not plat.enter_course(course_name):
                self.bus.log("进入课程失败", "error")
                return "进入课程失败"
            res = plat.brush_all(only_undone=True, progress_cb=cb)
        else:
            plat = Zjy2(br, self.bus)
            res = plat.brush_all(progress_cb=cb)

        if res.get("todo", 0) == 0:
            # 全部已完成：这是正常情况，不能报成「处理 0 项」让人以为失败了
            self.bus.progress(100, "无需刷课")
            self.bus.log(f"该课程资源已全部完成（{res.get('done_after')}/{res.get('total')}），无需刷课")
            return f"已全部完成 {res.get('done_after')}/{res.get('total')}"

        self.bus.progress(100, "刷课完成")
        quiz = res.get("quiz") or []
        if quiz:
            self.bus.log(f"有 {len(quiz)} 项测验/作业未处理：这类要自己答题，拖进度条不算完成")
        done_after = res.get("done_after")
        total_after = res.get("total_after")
        if done_after is not None:
            self.bus.log(f"完成：本次处理 {res['ok']}/{res['todo']}，目录完成 {done_after}/{total_after}")
            return f"本次 {res['ok']}/{res['todo']}，目录完成 {done_after}/{total_after}"
        self.bus.log(f"完成：共 {res['total']} 项，处理 {res['ok']}/{res['todo']}")
        return f"共 {res['total']} 项，处理 {res['ok']}/{res['todo']}"

    def action_watch_time(self, target_hours=5.0, max_minutes=300):
        br = self.ensure_browser()
        br.install_guards(prefer_resume=False)
        self.bus.log("刷时长期间：播放器自动静音，续播弹窗自动点「重新开始」")
        plat = AiIcve(br, self.bus)

        def cb(pct, text):
            self.bus.progress(pct, text)

        res = plat.brush_watch_time(target_hours=target_hours,
                                    max_minutes=max_minutes, progress_cb=cb)
        self.bus.progress(100, "刷时长结束")

        if res.get("error"):
            # 例如「进入课程失败」「未找到视频」——之前既不提示也不解释，看着像白跑一趟
            self.bus.log(f"未能开始播放：{res['error']}", "error")
            return f"未能开始：{res['error']}"

        start, end = res.get("start"), res.get("end")
        mins = res.get("minutes") or 0
        mins_txt = "不足 1 分钟" if mins < 1 else f"{mins:.0f} 分钟"
        if end is None:
            # v1.0.6：绝不再显示 "5.16 → None 时"。说明白「播放了多少 / 数字为什么没读到 / 怎么办」
            self.bus.log(
                f"结束：本次真实播放 {mins_txt}；最终数字这次没读到（页面未渲染或掉登录）。"
                "已播放的学时会由平台记录，重新点「② 读取观看时长」即可看到最新数字", "warn")
            return f"本次播放 {mins_txt}（最终数字未读取，点②可刷新）"
        self.bus.log(f"结束：{start} → {end} 时（本次真实播放 {mins_txt}）")
        if res.get("reached"):
            self.bus.log(f"已达成目标 {target_hours} 时")
        elif end == start:
            self.bus.log("时长数字没变化：平台按分钟结算，播放满 1 分钟后才会刷新，稍后点②再看", "warn")
        return f"{start} → {end} 时"

    def action_answer_quiz(self, platform="ai", course_name="大学生心理健康", dry_run=False):
        """自动答题：软件自己调大模型读题作答（Key 来自「⚙ 大模型设置」）。"""
        from config import llm_config
        from core.llm import LLMClient
        from core.platforms.quiz_ai import QuizAI, NAV

        cfg = llm_config()
        br = self.ensure_browser()
        br.install_guards(prefer_resume=True)

        if platform != "ai":
            self.bus.log("自动答题目前只支持「AI 优课」。zjy2 的测验页结构还没适配，"
                         "先别用，免得乱提交", "warn")
            return "zjy2 暂不支持自动答题"

        llm = LLMClient(cfg.get("base"), cfg.get("key"), cfg.get("model"),
                        cfg.get("timeout", 90))
        if not llm.ready():
            self.bus.log("还没配置大模型：点左下角「⚙ 大模型设置」，填接口地址 / Key / 模型名", "error")
            return "未配置大模型"

        if not AiIcve(br, self.bus).enter_course(course_name):
            self.bus.log(f"进入课程「{course_name}」失败，无法开始答题", "error")
            return "进入课程失败"

        kinds = ["work", "test"]
        if cfg.get("include_exam"):
            kinds.append("exam")
        auto_submit = bool(cfg.get("auto_submit", True)) and not dry_run

        quiz = QuizAI(br, self.bus, llm)
        quiz.course = course_name

        def cb(i, n, name):
            self.bus.progress(int(i / max(1, n) * 100), f"{i}/{n} {name}")

        self.bus.log(
            f"自动答题：{'试运行（只解析不提交）' if dry_run else '正式作答'}"
            f" · 范围 {' / '.join(NAV[k] for k in kinds)}"
            f" · {'自动提交' if auto_submit else '填完不提交，留给你确认'}")

        res = quiz.run(kinds=tuple(kinds), auto_submit=auto_submit,
                       dry_run=dry_run, progress_cb=cb)
        self.bus.progress(100, "答题结束")

        if dry_run:
            n = len(res.get("items") or [])
            if not n:
                self.bus.log("没有需要作答的条目（作业/测验都已全部完成）")
                return "无需作答"
            self.bus.log(f"试运行结束：预览了 {n} 项，页面上未做任何点选和提交")
            return f"试运行 {n} 项"

        if not res.get("todo"):
            self.bus.log("没有需要作答的条目（作业/测验都已全部完成）")
            return "无需作答"
        self.bus.log(f"答题结束：提交 {res.get('ok', 0)}/{res['todo']} 项")
        return f"提交 {res.get('ok', 0)}/{res['todo']} 项"

    def action_verify(self, platform="ai", course_name="大学生心理健康"):
        br = self.ensure_browser()
        br.install_guards(prefer_resume=True)
        if platform == "ai":
            plat = AiIcve(br, self.bus)
            # v1.0.6：必须先进入课程再展开目录。否则停在首页时 list_resources 拿到 0 项，
            # 日志就出现「复核：0/0 已完成」，看起来像全部没完成。
            if not plat.enter_course(course_name):
                self.bus.log(f"进入课程「{course_name}」失败，无法复核。"
                             "请确认课程名和平台选项是否正确", "error")
                return "进入课程失败"
            res = plat.verify()
            if res["total"] == 0:
                self.bus.log("目录里没读到任何资源节点：可能课程未展开或页面还在加载", "warn")
                return "0/0（未读到资源）"
            self.bus.log(f"复核：{res['done']}/{res['total']} 已完成")
            return f"{res['done']}/{res['total']} 完成"
        plat = Zjy2(br, self.bus)
        res = plat.verify()
        if res["total"] == 0:
            self.bus.log("目录里没读到任何资源节点：可能课程未展开或页面还在加载", "warn")
            return "0/0（未读到资源）"
        self.bus.log(f"复核：{res['done']}/{res['total']} 已完成")
        return f"{res['done']}/{res['total']} 完成"
