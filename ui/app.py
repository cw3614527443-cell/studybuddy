"""StudyBuddy 主界面（tkinter）。

布局：
- 顶部：标题 + 浏览器状态
- 左侧：功能按钮组（连接/登录、刷课、刷时长、复核、关闭浏览器）
- 右侧上部：参数与操作区
- 右侧中部：实时日志
- 底部：进度条 + 最新截图预览
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from config import APP_NAME, APP_VERSION, AI_ICVE_HOME, DEFAULT_TARGET_HOURS, SHOTS_DIR
from core.events import EventBus
from core.tasks import TaskRunner


class App:
    # 平台说明（v1.0.5）：光写 "AI 优课 / zjy2" 用户看不懂，补上域名和界面特征，
    # 让人能照着浏览器地址栏自己判断该选哪个。
    PLATFORM_INFO = {
        "ai": (
            "AI 优课 · ai.icve.com.cn",
            "课程卡片式首页，卡片上显示「观看时长 / 学习次数」。\n"
            "刷课＝把视频进度条拖到末尾、文档翻到最后一页；刷观看时长＝真实播放累计学时。",
        ),
        "zjy2": (
            "zjy2 · zjy2.icve.com.cn",
            "左侧是「章节 → 小节 → 资源」树形目录，每个资源后面跟着百分比进度。\n"
            "只支持刷课（2 倍速播完视频 / 文档翻到末页），这个平台没有学时统计。",
        ),
    }

    def __init__(self, root):
        self.root = root
        self.bus = EventBus()
        self.runner = TaskRunner(self.bus, on_finish=self._on_finish)
        self._last_shot = None
        self._photo = None

        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("1080x720")
        root.minsize(960, 640)

        self._build_ui()
        self._poll_bus()

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # 顶部标题栏
        header = ttk.Frame(self.root, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}", foreground="#888").pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(header, textvariable=self.status_var, foreground="#2a7").pack(side="right")

        body = ttk.Frame(self.root, padding=(14, 0))
        body.pack(fill="both", expand=True)

        # 左侧功能栏
        left = ttk.LabelFrame(body, text="功能", padding=10)
        left.pack(side="left", fill="y", padx=(0, 10))

        self.btn_login = ttk.Button(left, text="① 启动浏览器 / 登录", command=self.on_open_site, width=22)
        self.btn_login.pack(pady=4, fill="x")

        self.btn_read = ttk.Button(left, text="② 读取观看时长", command=self.on_read_time, width=22)
        self.btn_read.pack(pady=4, fill="x")

        self.btn_brush = ttk.Button(left, text="③ 刷课（一键过完）", command=self.on_brush, width=22)
        self.btn_brush.pack(pady=4, fill="x")

        self.btn_watch = ttk.Button(left, text="④ 刷观看时长", command=self.on_watch, width=22)
        self.btn_watch.pack(pady=4, fill="x")

        self.btn_verify = ttk.Button(left, text="⑤ 复核完成情况", command=self.on_verify, width=22)
        self.btn_verify.pack(pady=4, fill="x")

        self.btn_help = ttk.Button(left, text="❓ 看不懂？功能说明", command=self.on_help, width=22)
        self.btn_help.pack(pady=(10, 4), fill="x")

        ttk.Separator(left).pack(pady=10, fill="x")

        self.btn_stop = ttk.Button(left, text="停止当前任务", command=self.on_stop, width=22, state="disabled")
        self.btn_stop.pack(pady=4, fill="x")

        self.btn_close = ttk.Button(left, text="关闭浏览器", command=self.on_close_browser, width=22)
        self.btn_close.pack(pady=4, fill="x")

        # 右侧
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        # 参数区
        params = ttk.LabelFrame(right, text="参数", padding=10)
        params.pack(fill="x")

        r1 = ttk.Frame(params); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text="平台：").pack(side="left")
        self.platform_var = tk.StringVar(value="ai")
        ttk.Radiobutton(r1, text="AI 优课（ai.icve.com.cn）", variable=self.platform_var,
                        value="ai", command=self._on_platform_change).pack(side="left")
        ttk.Radiobutton(r1, text="zjy2（zjy2.icve.com.cn）", variable=self.platform_var,
                        value="zjy2", command=self._on_platform_change).pack(side="left", padx=(12, 0))

        # wraplength 需要留足余量：Windows 缩放 125% 时 Tk 会把该值放大渲染，
        # 660 实测已经溢出到窗口外，520 才能稳定落在右侧面板内。
        self.platform_note = ttk.Label(params, text="", foreground="#555",
                                       wraplength=520, justify="left")
        self.platform_note.pack(fill="x", pady=(2, 6))
        self._on_platform_change()

        r2 = ttk.Frame(params); r2.pack(fill="x", pady=3)
        ttk.Label(r2, text="课程名：").pack(side="left")
        self.course_var = tk.StringVar(value="大学生心理健康")
        ttk.Entry(r2, textvariable=self.course_var, width=28).pack(side="left", padx=4)

        r3 = ttk.Frame(params); r3.pack(fill="x", pady=3)
        ttk.Label(r3, text="目标时长(时)：").pack(side="left")
        self.hours_var = tk.StringVar(value=str(DEFAULT_TARGET_HOURS))
        ttk.Entry(r3, textvariable=self.hours_var, width=8).pack(side="left", padx=4)
        ttk.Label(r3, text="最长挂机(分)：").pack(side="left", padx=(16, 0))
        self.maxmin_var = tk.StringVar(value="300")
        ttk.Entry(r3, textvariable=self.maxmin_var, width=8).pack(side="left", padx=4)

        # 日志区
        logf = ttk.LabelFrame(right, text="运行日志", padding=6)
        logf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = scrolledtext.ScrolledText(logf, height=16, font=("Consolas", 9), state="disabled",
                                             wrap="word", bg="#fbfbfb")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("warn", foreground="#c60")
        self.log.tag_config("error", foreground="#c00")
        self.log.tag_config("info", foreground="#222")

        # 底部进度 + 截图
        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=(8, 10))

        pf = ttk.Frame(bottom); pf.pack(side="left", fill="x", expand=True)
        self.prog = ttk.Progressbar(pf, mode="determinate", maximum=100)
        self.prog.pack(fill="x")
        self.prog_var = tk.StringVar(value="")
        ttk.Label(pf, textvariable=self.prog_var, foreground="#555").pack(anchor="w")

        self.shot_label = ttk.Label(bottom, text="（截图预览）", width=24, anchor="center",
                                    relief="groove")
        self.shot_label.pack(side="right", padx=(10, 0))

    # ---------- 事件循环 ----------
    def _poll_bus(self):
        try:
            for kind, data in self.bus.poll():
                if kind == "log":
                    self._append_log(data)
                elif kind == "progress":
                    v = data.get("value")
                    if v is None:
                        self.prog.config(mode="indeterminate"); self.prog.start(12)
                    else:
                        self.prog.stop(); self.prog.config(mode="determinate"); self.prog["value"] = v
                    if data.get("text"):
                        self.prog_var.set(data["text"])
                elif kind == "status":
                    self.status_var.set(data.get("text", ""))
                elif kind == "shot":
                    self._show_shot(data.get("path"))
                elif kind == "done":
                    self._on_task_done(data)
        finally:
            self.root.after(120, self._poll_bus)

    def _append_log(self, d):
        self.log.config(state="normal")
        self.log.insert("end", f"[{d['time']}] {d['msg']}\n", d.get("level", "info"))
        self.log.see("end")
        self.log.config(state="disabled")

    def _show_shot(self, path):
        if not path or not os.path.exists(path):
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((190, 120))
            self._photo = ImageTk.PhotoImage(im)
            self.shot_label.config(image=self._photo, text="")
            self._last_shot = path
        except Exception:
            self.shot_label.config(text=os.path.basename(path)[:28])

    def _on_finish(self):
        pass

    def _on_platform_change(self):
        """切换平台时刷新说明文字，并禁用不适用的功能。"""
        title, desc = self.PLATFORM_INFO.get(self.platform_var.get(), ("", ""))
        self.platform_note.config(text=f"当前选中：{title}\n{desc}")
        if not self.runner.is_running():
            ai = self.platform_var.get() == "ai"
            self.btn_watch.config(state="normal" if ai else "disabled")

    def on_help(self):
        messagebox.showinfo(
            "功能说明 · StudyBuddy",
            "① 启动浏览器 / 登录\n"
            "    拉起一个受控的 Edge 窗口。第一次用要在里面扫码登录学习平台；\n"
            "    登录一次后，只要不点「关闭浏览器」，下次打开仍在登录状态。\n\n"
            "② 读取观看时长\n"
            "    读取首页的「观看时长 / 学习次数」（仅 AI 优课有这项统计）。\n\n"
            "③ 刷课（一键过完）\n"
            "    把目录里未完成的资源逐个打开：视频直接把进度条拖到末尾，\n"
            "    文档翻到最后一页。不耗真实观看时间，速度快。\n"
            "    若该课程已全部完成，会直接提示「无需刷课」。\n\n"
            "④ 刷观看时长\n"
            "    按目标学时真实播放视频。这一步不能拖进度条，拖了平台不计时长。\n"
            "    浏览器窗口要保持开着（可以最小化），关掉就中断。\n\n"
            "⑤ 复核完成情况\n"
            "    统计目录里已完成 / 总数，用来确认刷课结果。\n\n"
            "【平台怎么选】看浏览器地址栏：\n"
            "    ai.icve.com.cn    → 选「AI 优课」。卡片式首页，有学时统计。\n"
            "    zjy2.icve.com.cn  → 选「zjy2」。树形目录，资源后面带百分比。\n"
            "    两者都是智慧职教旗下的不同界面，选错会提示找不到元素。\n\n"
            "【停止当前任务】随时可中止，已完成的资源不会回退。",
        )

    def _on_task_done(self, data):
        self.prog.stop()
        self._set_busy(False)
        self.status_var.set("完成" if data.get("ok") else "已停止/出错")
        if data.get("summary"):
            self._append_log({"time": "", "msg": f"→ {data['summary']}", "level": "info"})

    # ---------- 忙闲控制 ----------
    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in (self.btn_login, self.btn_read, self.btn_brush, self.btn_watch,
                  self.btn_verify, self.btn_close, self.btn_help):
            b.config(state=state)
        self.btn_stop.config(state="normal" if busy else "disabled")
        if not busy:
            self._on_platform_change()   # zjy2 时重新禁用「刷观看时长」

    # ---------- 动作 ----------
    def _run(self, fn, *a, **kw):
        if self.runner.start(fn, *a, **kw):
            self._set_busy(True)
            self.status_var.set("运行中…")
            self.prog_var.set("")

    def on_open_site(self):
        def job():
            self.bus.log("启动受控浏览器（首次需在弹出的窗口里登录）…")
            return self.runner.action_open_site(AI_ICVE_HOME, attach=False)
        self._run(job)

    def on_read_time(self):
        self._run(lambda: self.runner.action_read_time(self.platform_var.get()))

    def on_brush(self):
        self._run(lambda: self.runner.action_brush_course(self.platform_var.get(), self.course_var.get()))

    def on_watch(self):
        try:
            h = float(self.hours_var.get())
            m = float(self.maxmin_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "目标时长与最长挂机需为数字")
            return
        self._run(lambda: self.runner.action_watch_time(h, m))

    def on_verify(self):
        self._run(lambda: self.runner.action_verify(self.platform_var.get(), self.course_var.get()))

    def on_stop(self):
        self.runner.stop()

    def on_close_browser(self):
        def job():
            self.runner.close_browser()
            return "浏览器已关闭"
        self._run(job)

    def on_close(self):
        if self.runner.is_running():
            if not messagebox.askyesno("确认退出", "任务正在运行，确定退出吗？"):
                return
            self.runner.stop()
        try:
            self.runner.close_browser()
        except Exception:
            pass
        self.root.destroy()
