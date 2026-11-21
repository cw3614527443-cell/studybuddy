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
from core.platforms.chaoxing_adapter import ChaoxingSuite


def _login_expired(br):
    """浏览器是否停在平台登录页（登录态过期）。兼容不支持该方法的老对象/测试桩。"""
    try:
        return bool(br.on_login_page())
    except Exception:
        return False


class TaskRunner:
    def __init__(self, bus, on_finish=None):
        self.bus = bus
        self.on_finish = on_finish
        self.thread = None
        self.browser = None          # 仅当前任务线程内有效
        self._edge_started = False   # 本进程是否拉起过 Edge
        self.cx = None               # 学习通用独立浏览器驱动，不持有 Playwright 对象

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

    def chaoxing(self):
        """学习通用自己常驻的浏览器驱动，和 Edge CDP 那套完全隔离。"""
        if self.cx is None:
            self.cx = ChaoxingSuite(self.bus)
        return self.cx

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
        if self.cx is not None:
            try:
                self.cx.close()
            except Exception:
                pass
            self.cx = None
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
        if self.cx is not None:
            try:
                self.cx.close()
            except Exception:
                pass
            self.cx = None
        EdgeProc.kill(grace=3, force_any=True)
        self._edge_started = False

    # ---------- 业务动作（供 GUI 调用） ----------
    def action_open_site(self, site_home, attach=False, platform="ai"):
        if platform == "cx":
            self.bus.log("学习通使用独立浏览器窗口（有头运行，平台要求不能失焦）")
            return self.chaoxing().login()
        br = self.ensure_browser()
        br.goto(site_home)
        br.wait(2500)
        br.shot("open_site")
        self.bus.log(f"已打开：{site_home}")
        return "已打开页面"

    def action_list_courses(self, platform="ai"):
        """扫描「我的课程」页，逐门检测有无可刷资源，回传带标注的列表。

        可刷 = 教学内容里有视频/文档（quiz 不算）；无课 = 教学内容为空。
        检测失败的不硬塞进列表，单独说明，避免误导用户。

        学习通走套件自己的浏览器与个人空间课程卡片，不借用 Edge CDP。
        """
        if platform == "cx":
            self.bus.progress(10, "正在打开学习通个人空间…")
            items = self.chaoxing().list_courses()
            self.bus.courses(items, platform)
            self.bus.progress(100, f"识别完成：{len(items)} 门")
            msg = (f"学习通：识别 {len(items)} 门"
                   f"（可刷 {sum(1 for x in items if x['brushable'])} / "
                   f"无课 {sum(1 for x in items if not x['brushable'])}）")
            self.bus.log(msg, "ok")
            return msg
        br = self.ensure_browser()
        plat = AiIcve(br, self.bus) if platform == "ai" else Zjy2(br, self.bus)
        pname = "AI 优课" if platform == "ai" else "zjy2"
        # 扫描会连点十几门课，先把历史遗留的多余标签页收掉，避免页面选择混乱
        try:
            br.close_extra_tabs()
        except Exception:
            pass
        self.bus.progress(5, "正在打开「我的课程」…")
        names = plat.list_course_names()
        if names is None or _login_expired(br):
            self.bus.log("登录状态已失效，请点「① 启动浏览器 / 登录」重新扫码后再扫描", "error")
            self.bus.courses([], platform)
            return "未登录"
        if not names:
            self.bus.log("没读到课程列表，可能页面还在加载或平台改版了", "warn")
            self.bus.courses([], platform)
            return "未读到课程"
        self.bus.log(f"{pname}：识别到 {len(names)} 门课，正在逐门检测有无可刷内容…")

        items = []
        unknown = []
        total = len(names)
        for i, name in enumerate(names, 1):
            if self.bus.should_stop():
                self.bus.log("已停止扫描", "warn")
                break
            self.bus.progress(5 + int(90 * i / total), f"检测 {i}/{total}：{name}")
            self.bus.log(f"  [{i}/{total}] 检测「{name}」…")
            result = plat.probe_course(name)
            if result is True:
                items.append({"name": name, "brushable": True, "note": "可刷"})
                self.bus.log(f"  [{i}/{total}] 「{name}」→ 可刷", "ok")
            elif result is False:
                items.append({"name": name, "brushable": False, "note": "无课"})
                self.bus.log(f"  [{i}/{total}] 「{name}」→ 无课")
            else:
                unknown.append(name)
                self.bus.log(f"  [{i}/{total}] 「{name}」检测失败，已跳过", "warn")

        self.bus.courses(items, platform)
        self.bus.progress(100, f"识别完成：{len(items)} 门")
        msg = f"识别 {len(items)} 门（可刷 {sum(1 for x in items if x['brushable'])} / 无课 {sum(1 for x in items if not x['brushable'])}）"
        if unknown:
            msg += f"，{len(unknown)} 门检测失败"
        self.bus.log(msg, "ok")
        return msg

    def _course_progress(self, platform="ai", course_name="大学生心理健康", br=None):
        """统计课程完成度：已完成项 / 总项。

        用于「没有观看时长统计」的课程（zjy2 全平台，以及 AI 优课里不计学时的
        课程）。返回 (done, total, err)；读不到时 done 为 None。
        """
        br = br or self.ensure_browser()
        if platform == "ai":
            plat = AiIcve(br, self.bus)
            # 必须先进入课程再展开目录，否则停在首页只会数到 0 项
            if not plat.enter_course(course_name):
                if _login_expired(br):
                    return None, 0, "登录状态已失效（当前停在登录页）"
                return None, 0, f"进入课程「{course_name}」失败"
        else:
            plat = Zjy2(br, self.bus)
        self.bus.progress(30, "统计目录完成度…")
        res = plat.verify()
        if self.bus.should_stop():
            return None, 0, "已请求停止"
        total = res.get("total", 0)
        done = res.get("done", 0)
        if total == 0:
            if _login_expired(br):
                return None, 0, "登录状态已失效（当前停在登录页）"
            return None, 0, "目录里没读到任何资源节点（课程未展开或页面还在加载）"
        pct = int(round(done / total * 100))
        self.bus.log(f"课程完成度 {done}/{total}（{pct}%）")
        self.bus.progress(100, f"完成度 {pct}%")
        return done, total, ""

    def action_read_time(self, platform="ai", course_name="大学生心理健康"):
        """读学习进度：有观看时长就读时长，没有就读刷课完成度。

        v1.4.2：以前读不到「观看时长」就直接报失败。但有相当一部分课程不计学时
        （zjy2 / 学习通都没有这项统计），这时该给的是「已完成 x/y」。现在统一回退，
        按钮也从「读取观看时长」改名「读取学习进度」。
        """
        if platform == "cx":
            self.bus.progress(20, "正在打开课程目录…")
            done, total, err = self.chaoxing().read_progress(course_name)
            if done is None:
                self.bus.log(f"学习通进度没读到：{err}", "warn")
                return "读取失败"
            pct = int(round(done / total * 100)) if total else 0
            self.bus.progress(100, f"完成度 {pct}%")
            if err:
                self.bus.log(err, "warn")
            self.bus.log(f"学习通完成度 {done}/{total}（{pct}%）")
            return f"完成度 {done}/{total}（{pct}%）"
        br = self.ensure_browser()
        if platform == "ai":
            plat = AiIcve(br, self.bus)
            h, c = plat.read_watch(quiet=True)
            if h is not None:
                self.bus.log(f"观看时长 {h} 时，学习次数 {c}")
                return f"观看时长 {h} 时 / {c} 次"
            if _login_expired(br):
                # read_watch 已经报了「登录状态已失效」，这里补一句怎么办
                self.bus.log("请点「① 启动浏览器 / 登录」重新扫码后再试", "error")
                return "未登录"
            self.bus.log("这门课没有「观看时长」统计（部分课程不计学时），改为读取刷课完成度…",
                         "warn")
        else:
            if _login_expired(br):
                self.bus.log("登录状态已失效：当前停在平台登录页，"
                             "请点「① 启动浏览器 / 登录」重新扫码后再试", "error")
                return "未登录"
            self.bus.log("zjy2 没有学时统计，读取刷课完成度…")

        done, total, err = self._course_progress(platform, course_name, br)
        if done is None:
            if err == "已请求停止":
                self.bus.log("已停止统计完成度", "warn")
                return "已停止"
            if "登录" in err:
                self.bus.log("登录状态已失效：请点「① 启动浏览器 / 登录」重新扫码后再试",
                             "error")
                return "未登录"
            self.bus.log(f"完成度也没读到：{err}。请确认已登录、课程名正确、页面正常", "warn")
            return "读取失败"
        pct = int(round(done / total * 100))
        return f"完成度 {done}/{total}（{pct}%）"

    def action_brush_course(self, platform="ai", course_name="大学生心理健康"):
        if platform == "cx":
            self.bus.log("学习通：视频必须 1 倍速播到结尾，平台才认任务点；"
                         "脚本会静音并自动处理失焦/续播弹窗")
            res = self.chaoxing().brush_course(course_name)
            return self._brush_summary(res)
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

        return self._brush_summary(res)

    def _brush_summary(self, res):
        """三个平台共用的刷课结果播报，避免学习通接入后三套重复文案。"""
        if res.get("todo", 0) == 0:
            self.bus.progress(100, "无需刷课")
            self.bus.log(f"该课程资源已全部完成（{res.get('done_after')}/{res.get('total')}），无需刷课")
            return f"已全部完成 {res.get('done_after')}/{res.get('total')}"

        if res.get("aborted"):
            self.bus.progress(100, "已中止")
            self.bus.log(f"已提前中止：只确认 {res['ok']}/{res['todo']} 项。"
                         "请先处理弹窗 / 登录问题再重试③，不要直接接着刷", "error")
            return f"已中止（确认 {res['ok']}/{res['todo']}）"

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

    def action_watch_time(self, target_hours=5.0, max_minutes=300, platform="ai"):
        if platform == "cx":
            self.bus.log("学习通刷视频必须真实播到结尾，没有独立的「刷观看时长」入口", "warn")
            return "学习通不支持刷观看时长"
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
                "已播放的学时会由平台记录，重新点「② 读取学习进度」即可看到最新数字", "warn")
            return f"本次播放 {mins_txt}（最终数字未读取，点②可刷新）"
        self.bus.log(f"结束：{start} → {end} 时（本次真实播放 {mins_txt}）")
        if res.get("reached"):
            self.bus.log(f"已达成目标 {target_hours} 时")
        elif end == start:
            self.bus.log("时长数字没变化：平台按分钟结算，播放满 1 分钟后才会刷新，稍后点②再看", "warn")
        return f"{start} → {end} 时"

    def action_answer_quiz(self, platform="ai", course_name="大学生心理健康", dry_run=False):
        """自动答题：软件自己调大模型读题作答（Key 来自「⚙ 大模型设置」）。"""
        if platform == "cx":
            from config import llm_config
            cfg = llm_config()
            res = self.chaoxing().answer_quiz(
                course_name, dry_run=dry_run,
                auto_submit=bool(cfg.get("auto_submit", True)) and not dry_run)
            if res.get("error"):
                self.bus.log(res["error"], "error")
                return "答题失败"
            if res.get("stopped"):
                return "已停止答题"
            if not res.get("todo"):
                self.bus.log("没有需要作答的题目（测验都已完成或没采到题）")
                return "无需作答"
            if dry_run:
                self.bus.log(f"试运行结束：预览 {res['todo']} 题，页面上未点选、未提交")
                return f"试运行 {res['todo']} 题"
            self.bus.log(f"学习通答题结束：提交 {res.get('ok', 0)}/{res['todo']} 套题")
            return f"提交 {res.get('ok', 0)}/{res['todo']} 套题"
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
        if platform == "cx":
            done, total, err = self.chaoxing().read_progress(course_name)
            if done is None or not total:
                self.bus.log(f"复核失败：{err or '目录里没读到资源'}", "warn")
                return "0/0（未读到资源）"
            self.bus.log(f"复核：{done}/{total} 已完成")
            return f"{done}/{total} 完成"
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
