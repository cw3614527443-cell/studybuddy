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
    @staticmethod
    def _login_entry(platform, site_home):
        """账号密码登录的落地页：必须是「未登录就会跳统一认证」的页面。

        zjy2 用「我的课程」，AI 优课用自己的个人空间首页（本来就要求登录）。
        """
        return {"zjy2": "https://zjy2.icve.com.cn/study/v2/course"}.get(platform, site_home)

    def action_open_site(self, site_home, attach=False, platform="ai", login=None):
        """打开平台并（可选）用账号密码登录。

        login={"user":..., "pwd":..., "wait":180}
        传了 login 就走自动填表；不传就保持原来的「打开页面 / 扫码」行为。
        扫码要求账号主手机在手边，替别人刷课时账号密码才是可行路径。
        """
        user = (login or {}).get("user") or ""
        password = (login or {}).get("pwd") or ""

        if platform == "cx":
            if user:
                # 滑块要人工拖，等待时间必须可以由调用方给（默认 3 分钟太短，
                # 账号主不在电脑前时很容易超时）。
                return self.chaoxing().login_with_password(
                    user, password, wait_seconds=int((login or {}).get("wait") or 180))
            self.bus.log("学习通使用独立浏览器窗口（有头运行，平台要求不能失焦）")
            return self.chaoxing().login()

        br = self.ensure_browser()
        # v1.7.1：登录必须落在「不登录就看不见」的页面。
        # www.icve.com.cn 首页允许匿名浏览，只看 URL 永远像「已登录」，
        # 结果账号密码根本没送出去，后面扫描才报「登录失效」。
        entry = self._login_entry(platform, site_home) if user else site_home
        br.goto(entry)
        br.wait(2500)

        if user:
            from core.login import login_icve
            self.bus.log(f"正在用账号 {user} 登录…（自动化登录，仅本机使用）")
            res = login_icve(
                br.page, user, password,
                log=self.bus.log,
                wait_seconds=int((login or {}).get("wait") or 180),
                should_stop=self.bus.should_stop,
                shot=lambda: self.bus.shot(br.shot("login-captcha")),
            )
            reason = res.get("reason") or ""
            if reason == "stopped":
                return "已停止登录"
            if not res.get("ok"):
                raise RuntimeError(f"账号密码登录没成功：{reason}。"
                                   "可改用「扫码 / 手动登录」，在弹出的窗口里自己登一次")
            br.wait(1500)
            br.goto(site_home)          # 登录后回到工作页，避免停在跳转页
            br.wait(2500)
            # 真的登进去了吗：统一认证可能在「提交成功」后又被踢回来，
            # 只看一次 URL 很容易报假成功，必须回头访问一个要登录的页面确认。
            br.goto(entry)
            br.wait(2500)
            if _login_expired(br):
                raise RuntimeError("账号密码已提交，但仍停在统一认证登录页："
                                   "可能是账号/密码不对，或滑块验证没通过。"
                                   "可改用「扫码 / 手动登录」")
            br.goto(site_home)
            br.wait(2000)
            br.shot("open_site")
            self.bus.log("账号密码登录成功", "ok")
            return "已登录（账号密码）"

        br.shot("open_site")
        self.bus.log(f"已打开：{site_home}")
        return "已打开页面"

    def action_list_courses(self, platform="ai"):
        """扫描「我的课程」页，逐门检测有无可刷资源，回传带标注的列表。

        可刷 = 教学内容里有视频/文档（quiz 不算）；无课 = 教学内容为空；
        未确认 = 检测失败（网络/页面结构），**仍然留在列表里**，
        只是标注成「未确认」——以前直接丢掉，用户会以为平台漏扫了课程。

        学习通走套件自己的浏览器与个人空间课程卡片，不借用 Edge CDP。
        """
        if platform == "cx":
            self.bus.progress(10, "正在打开学习通个人空间…")
            items = self.chaoxing().list_courses()
            self.bus.courses(items, platform)
            self.bus.progress(100, f"识别完成：{len(items)} 门")
            msg = (f"学习通：识别 {len(items)} 门"
                   f"（可刷 {sum(1 for x in items if x.get('brushable'))} / "
                   f"无课 {sum(1 for x in items if not x.get('brushable'))}）")
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
            info = None
            probe_info = getattr(plat, "probe_course_info", None)
            if callable(probe_info):
                info = probe_info(name)
            else:                                   # 兼容只实现了 probe_course 的实现
                r = plat.probe_course(name)
                if r is not None:
                    info = {"brushable": bool(r), "done": 0, "total": 0}
            if info is None:
                unknown.append(name)
                items.append({"name": name, "brushable": True, "note": "未确认",
                              "unknown": True, "done": 0, "total": 0, "finished": False})
                self.bus.log(f"  [{i}/{total}] 「{name}」没检测出来，先留在列表里（标「未确认」）",
                             "warn")
                continue
            n_total = int(info.get("total") or 0)
            n_done = int(info.get("done") or 0)
            brushable = bool(info.get("brushable"))
            finished = bool(n_total and n_done >= n_total)
            note = "刷完" if finished else ("可刷" if brushable else "无课")
            items.append({"name": name, "brushable": brushable, "note": note,
                          "done": n_done, "total": n_total, "finished": finished})
            extra = f"（{n_done}/{n_total}）" if n_total else ""
            self.bus.log(f"  [{i}/{total}] 「{name}」→ {note}{extra}",
                         "ok" if brushable else "info")
            # 每门课都会新开一个标签页。扫 12 门就堆 12 个，越扫越卡，
            # 还会让后面的页面选择变乱 —— 每门做完就收一次，只留列表页。
            try:
                keep = getattr(plat, "MY_COURSES_URL", None)
                br.close_extra_tabs(keep)
                if br.page is not plat.p:
                    plat.p = br.page
            except Exception:
                pass
        # 未知（检测失败）的那几门也要收一次，否则同样会留一堆页
        try:
            br.close_extra_tabs(getattr(plat, "MY_COURSES_URL", None))
        except Exception:
            pass

        self.bus.courses(items, platform)
        self.bus.progress(100, f"识别完成：{len(items)} 门")
        n_fin = sum(1 for x in items if x.get("finished"))
        msg = (f"识别 {len(items)} 门（已刷完 {n_fin} / 可刷 "
               f"{sum(1 for x in items if x.get('brushable') and not x.get('finished'))} / 无课 "
               f"{sum(1 for x in items if not x.get('brushable'))}）")
        if unknown:
            msg += f"，{len(unknown)} 门未确认"
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
            # v1.7.0：以前 zjy2 不进入课程，直接统计「当前打开的那个页面」，
            # 换一门课就会读到上一门课的目录（甚至 0 项）。必须按课程名进入。
            if not plat.enter_course(course_name):
                if _login_expired(br):
                    return None, 0, "登录状态已失效（当前停在登录页）"
                return None, 0, f"进入课程「{course_name}」失败"
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
            # v1.8.0：学习通一次就把**全部课程**的进度都读出来。
            # 以前只能一门一门选着读，账号十几门课时要重复点十几次，
            # 而且课程列表上的「可刷 / 刷完」也不会跟着更新。
            self.bus.progress(15, "正在读取学习通全部课程进度…")
            items = self.chaoxing().list_courses()
            if not items:
                self.bus.log("学习通没读到课程列表：可能还没登录，或课程卡还在加载", "warn")
                return "读取失败"
            self.bus.courses(items, "cx")
            for it in items:
                mark = "刷完" if it.get("finished") else (it.get("progress") or "无任务点")
                level = "ok" if it.get("finished") else "info"
                self.bus.log(f"  · {it.get('name')}  {mark}", level)
            n_fin = sum(1 for x in items if x.get("finished"))
            d = sum(int(x.get("done") or 0) for x in items)
            t = sum(int(x.get("total") or 0) for x in items)
            pct = int(round(d / t * 100)) if t else 0
            self.bus.progress(100, f"已读 {len(items)} 门，刷完 {n_fin} 门")
            self.bus.log(f"学习通：共 {len(items)} 门，已刷完 {n_fin} 门；"
                         f"任务点合计 {d}/{t}（{pct}%）", "ok")
            return f"{len(items)} 门，刷完 {n_fin} 门（{d}/{t}）"
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

    def action_brush_course(self, platform="ai", course_name="", courses=None):
        """刷课。course_name="__all__" 时把扫描到的可刷课程逐门刷一遍。

        替别人刷课时往往一账号十几门课，一门门手点不现实；
        但同时不能瞎刷：只刷扫描结果里标了「可刷」的，无课的直接跳过。
        """
        if course_name == "__all__":
            return self._brush_all_courses(platform, courses or [])
        if not (course_name or "").strip():
            return "请先扫描课程并选一门（或用「全部可刷课程」）"
        return self._brush_summary(self._brush_one(platform, course_name))

    def _brush_all_courses(self, platform, courses):
        items = [c for c in courses if c.get("brushable") and c.get("name")]
        if not items:
            self.bus.log("扫描结果里没有可刷课程，先点「扫描课程」确认", "warn")
            return "没有可刷课程"
        total = len(items)
        okn, failed, skipped = 0, [], 0
        self.bus.log(f"批量刷课：共 {total} 门可刷课程", "ok")
        for i, it in enumerate(items, 1):
            if self.bus.should_stop():
                self.bus.log(f"已停止：完成 {okn}/{total} 门", "warn")
                break
            name = it["name"]
            self.bus.log(f"=== [{i}/{total}] {name} ===", "ok")
            self.bus.progress(int((i - 1) / total * 100), f"第 {i}/{total} 门：{name[:24]}")
            try:
                res = self._brush_one(platform, name)
            except Exception as e:
                failed.append(f"{name}（{str(e)[:40]}）")
                self.bus.log(f"「{name}」失败：{e}", "error")
                continue
            if res.get("aborted"):
                failed.append(f"{name}（中途中止）")
            elif res.get("error"):
                failed.append(f"{name}（{res['error']}）")
            else:
                okn += 1
                if res.get("todo", 0) == 0:
                    skipped += 1
        self.bus.progress(100, "批量刷课结束")
        self.bus.log(f"批量结果：完成 {okn}/{total} 门，失败 {len(failed)} 门", 
                     "ok" if not failed else "warn")
        if failed:
            self.bus.log("失败清单：" + "；".join(failed), "warn")
        return f"{okn}/{total} 门完成" + (f"，{len(failed)} 门失败" if failed else "")

    def _brush_one(self, platform, course_name):
        """刷单门课程。返回结果字典（学习通/ai/zjy2 结构一致）。"""
        if platform == "cx":
            self.bus.log("学习通：视频必须 1 倍速播到结尾，平台才认任务点；"
                         "脚本会静音并自动处理失焦/续播弹窗")
            return self.chaoxing().brush_course(course_name)
        br = self.ensure_browser()
        br.install_guards(prefer_resume=True)
        self.bus.log("刷课期间：播放器自动静音，续播弹窗自动点「继续播放」")

        def cb(i, n, name):
            self.bus.progress(int(i / max(1, n) * 100), f"{i}/{n} {name[:30]}")

        if platform == "ai":
            plat = AiIcve(br, self.bus)
            if not plat.enter_course(course_name):
                self.bus.log("进入课程失败", "error")
                return {"total": 0, "todo": 0, "ok": 0, "aborted": False,
                        "error": "进入课程失败", "failed": []}
            res = plat.brush_all(only_undone=True, progress_cb=cb)
        else:
            plat = Zjy2(br, self.bus)
            if not plat.enter_course(course_name):
                self.bus.log(f"进入课程「{course_name}」失败（课程名不对或页面没加载完）", "error")
                return {"total": 0, "todo": 0, "ok": 0, "error": "进入课程失败",
                        "failed": [], "aborted": False}
            res = plat.brush_all(progress_cb=cb)

        return res

    def _brush_summary(self, res):
        """三个平台共用的刷课结果播报，避免学习通接入后三套重复文案。"""
        if res.get("error"):
            # 「进入课程失败」这种不能报成「已全部完成 0/0」，会让人以为刷完了
            self.bus.log(f"刷课失败：{res['error']}", "error")
            return f"失败：{res['error']}"
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

    def action_watch_time(self, target_hours=5.0, max_minutes=300, platform="ai", course_name=""):
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
                                    max_minutes=max_minutes, progress_cb=cb,
                                    course_name=course_name)
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
        # v1.7.1：复核也要先进入课程，否则读到的是上一门课的目录（甚至 0 项）
        if not plat.enter_course(course_name):
            if _login_expired(br):
                self.bus.log("登录状态已失效（当前停在登录页）", "error")
                return "未登录"
            self.bus.log(f"进入课程「{course_name}」失败，无法复核", "error")
            return "进入课程失败"
        res = plat.verify()
        if res["total"] == 0:
            self.bus.log("目录里没读到任何资源节点：可能课程未展开或页面还在加载", "warn")
            return "0/0（未读到资源）"
        self.bus.log(f"复核：{res['done']}/{res['total']} 已完成")
        return f"{res['done']}/{res['total']} 完成"
