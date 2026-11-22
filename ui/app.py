"""StudyBuddy 主界面。

v1.6.0 重做设计语言：从「功能列表」改成「按流程做事」。

设计原则（真人易用性优先）：
1. 主内容区就是操作顺序：选平台 → 选课程 → 看进度，不让人猜下一步；
2. 平台用卡片而不是下拉，域名和能力边界一眼可见，选错的成本前置消除；
3. 扫描课程是主按钮，课程没扫描时明确显示空态，杜绝默认课程名的假象；
4. 不适用当前平台的参数直接隐藏，减少无效信息；
5. 停止按钮跟着进度走，运行中永远在视野里；
6. 左侧保留 ①~⑥ 快捷入口，但用「快速上手」卡把真实顺序讲清楚。

所有视觉元素由 ui/widgets.py 自绘，按屏幕 DPI 缩放，不依赖 ttk 系统主题。
"""
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from config import (APP_NAME, APP_VERSION, AI_ICVE_HOME, DEFAULT_TARGET_HOURS,
                    save_account)
from core.events import EventBus
from core.tasks import TaskRunner
from ui import widgets as W
from ui.login_dialog import LoginDialog


class App:
    # 平台说明：光写 "AI 优课 / zjy2" 用户看不懂，补上域名和界面特征，
    # 让人能照着浏览器地址栏自己判断该选哪个。
    PLATFORM_INFO = {
        "ai": {
            "host": "ai.icve.com.cn",
            "feature": "卡片式首页，有观看时长统计",
            "tags": "刷课 · 学时 · 答题",
            "desc": "刷课＝视频拖到末尾、文档翻到末页；刷时长＝真实播放累计学时。\n"
                    "个别课程不计学时，点②会自动改读目录完成度。",
        },
        "zjy2": {
            "host": "zjy2.icve.com.cn",
            "feature": "章节资源树，资源后带百分比",
            "tags": "刷课 · 进度复核",
            "desc": "没有学时统计，点②读的是目录完成度（已完成 / 总数）。\n"
                    "网速慢时会先等页面加载完再动手，日志里会显示「继续等…」。",
        },
        "cx": {
            "host": "v8.chaoxing.com · mooc2",
            "feature": "独立浏览器，视频要真实播完",
            "tags": "刷课 · 答题 · 有头窗口",
            "desc": "①可选账号密码登录（滑块需人工拖一次）；窗口不要最小化。\n"
                    "视频必须 1 倍速播到结尾平台才认任务点，耗时≈视频总时长。",
        },
    }
    PLATFORM_ORDER = (("ai", "AI 优课"), ("zjy2", "zjy2"), ("cx", "学习通"))

    def __init__(self, root):
        self.root = root
        self.bus = EventBus()
        self.runner = TaskRunner(self.bus, on_finish=self._on_finish)
        self._last_shot = None
        self._icon_img = None
        self._nav = {}
        self._closing = False          # 正在退出：窗口已收起，等后台收尾
        self._shutdown_done = None
        self._shutdown_deadline = 0.0
        self._destroyed = False
        self._pump_started = False

        W.init_scale(root)                      # 必须在建控件前定下缩放比
        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.configure(bg=W.BG)
        try:
            from config import ICON_PATH
            if os.path.exists(ICON_PATH):
                root.iconbitmap(default=ICON_PATH)
        except Exception:
            pass

        # 设计尺寸 1120×780，按 DPI 放大；屏幕放不下时按比例收缩，避免超出可视区
        w, h = W.s(1120), W.s(780)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(w, int(sw * 0.94)), min(h, int(sh * 0.90))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - W.s(20))
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(min(W.s(1000), w), min(W.s(700), h))

        self._build_ui()
        self._append_log({"time": "",
                          "msg": "就绪。首次使用请先点 ① 启动浏览器 / 登录。",
                          "level": "muted"})
        self._poll_bus()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self._build_sidebar()
        self._build_content()

    def _build_sidebar(self):
        p = W.s
        bar = tk.Frame(self.root, bg=W.CARD, width=p(238))
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=W.LINE, width=1).place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # 品牌
        brand = tk.Frame(bar, bg=W.CARD)
        brand.pack(fill="x", padx=p(18), pady=(p(16), p(12)))
        self._build_logo(brand)
        texts = tk.Frame(brand, bg=W.CARD)
        texts.pack(side="left")
        tk.Label(texts, text="StudyBuddy", bg=W.CARD, fg=W.TEXT,
                 font=W.font(13, "bold")).pack(anchor="w")
        tk.Label(texts, text="智慧职教 · 学习助手", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", pady=(p(2), 0))

        # 快速上手：真实操作顺序写在最显眼的位置，避免对着 ①~⑥ 猜流程
        guide = W.RoundedCanvas(bar, radius=10, fill=W.ACCENT_SOFT, outline="",
                                line_width=0, pad=(12, 10), height=112)
        guide.pack(fill="x", padx=p(12), pady=(0, p(12)))
        tk.Label(guide.body, text="快速上手", bg=W.ACCENT_SOFT, fg=W.ACCENT,
                 font=W.font(9, "bold")).pack(anchor="w")
        for line in ("1  选平台 → 看域名", "2  点①登录浏览器",
                     "3  扫描课程 → 选课", "4  点③刷课 → ②验收"):
            tk.Label(guide.body, text=line, bg=W.ACCENT_SOFT, fg=W.TEXT_SUB,
                     font=W.font(8), anchor="w").pack(anchor="w", pady=(p(2), 0))

        tk.Label(bar, text="常用操作", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", padx=p(18), pady=(0, p(5)))

        items = [
            ("open", "启动浏览器 / 登录", 1, self.on_open_site),
            ("read", "读取学习进度", 2, self.on_read_time),
            ("brush", "刷课（一键过完）", 3, self.on_brush),
            ("watch", "刷观看时长", 4, self.on_watch),
            ("verify", "复核完成情况", 5, self.on_verify),
            ("quiz", "自动答题（大模型）", 6, self.on_quiz),
        ]
        for key, label, num, cmd in items:
            b = W.NavButton(bar, text=label, number=num, command=cmd, height=38)
            b.pack(fill="x", padx=p(12), pady=p(1))
            self._nav[key] = b

        W.divider(bar, pady=(10, 8), padx=14)

        self.btn_stop = W.NavButton(bar, text="■  停止当前任务", kind="danger",
                                    command=self.on_stop, height=38)
        self.btn_stop.pack(fill="x", padx=p(12), pady=p(1))
        self.btn_stop.config(state="disabled")

        self.btn_close = W.NavButton(bar, text="关闭浏览器", kind="ghost",
                                     command=self.on_close_browser, height=34)
        self.btn_close.pack(fill="x", padx=p(12), pady=p(1))

        bottom = tk.Frame(bar, bg=W.CARD)
        bottom.pack(side="bottom", fill="x", padx=p(12), pady=(0, p(10)))
        self.btn_settings = W.NavButton(bottom, text="⚙ 大模型设置", kind="link",
                                        command=self.on_settings, height=28)
        self.btn_settings.pack(fill="x")
        self.btn_help = W.NavButton(bottom, text="功能说明", kind="link",
                                    command=self.on_help, height=28)
        self.btn_help.pack(fill="x")
        self.btn_about = W.NavButton(bottom, text=f"关于此软件 · v{APP_VERSION}",
                                     kind="link", command=self.on_about, height=28)
        self.btn_about.pack(fill="x")
        tk.Label(bottom, text="关闭浏览器后要重新登录；已完成的课时不会回退。",
                 bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8), wraplength=p(212),
                 justify="left", anchor="w").pack(anchor="w", pady=(p(6), 0))

    def _build_logo(self, parent):
        """优先用打包进来的 icon.png，失败则画个方块兜底。"""
        p = W.s
        holder = tk.Frame(parent, bg=W.CARD, width=p(36), height=p(36))
        holder.pack(side="left", padx=(0, p(10)))
        holder.pack_propagate(False)
        try:
            from PIL import Image, ImageTk
            from config import resource_path
            path = resource_path(os.path.join("assets", "icon.png"))
            if os.path.exists(path):
                im = Image.open(path).convert("RGBA").resize((p(36), p(36)), Image.LANCZOS)
                self._icon_img = ImageTk.PhotoImage(im)
                tk.Label(holder, image=self._icon_img, bg=W.CARD, bd=0).place(
                    x=0, y=0, width=p(36), height=p(36))
                return
        except Exception:
            pass
        c = tk.Canvas(holder, width=p(36), height=p(36), bg=W.CARD, highlightthickness=0)
        c.pack()
        c.create_polygon(W.rounded_points(p(2), p(2), p(34), p(34), p(10)),
                         fill=W.ACCENT, outline="")
        c.create_text(p(18), p(18), text="S", fill="#FFFFFF", font=W.font(13, "bold"))

    def _build_content(self):
        p = W.s
        content = tk.Frame(self.root, bg=W.BG)
        content.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(content, bg=W.BG)
        inner.pack(fill="both", expand=True, padx=p(24), pady=(p(16), p(14)))

        # ---- 顶部 ----
        head = tk.Frame(inner, bg=W.BG)
        head.pack(fill="x")
        left = tk.Frame(head, bg=W.BG)
        left.pack(side="left")
        tk.Label(left, text="工作台", bg=W.BG, fg=W.TEXT,
                 font=W.font(17, "bold")).pack(anchor="w")
        tk.Label(left, text="按下面三步走，不用记按钮顺序", bg=W.BG, fg=W.TEXT_MUT,
                 font=W.font(9)).pack(anchor="w", pady=(p(3), 0))
        self.chip = W.StatusChip(head, text="就绪")
        self.chip.pack(side="right", pady=(p(4), 0))

        # ---- ① 平台：卡片一眼可见域名与能力 ----
        s1 = tk.Frame(inner, bg=W.BG)
        s1.pack(fill="x", pady=(p(13), 0))
        tk.Label(s1, text="①  选择平台", bg=W.BG, fg=W.TEXT,
                 font=W.font(11, "bold")).pack(side="left")
        tk.Label(s1, text="照浏览器地址栏的域名选", bg=W.BG, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(side="right", pady=(p(3), 0))

        self.platform_var = tk.StringVar(value="ai")
        opts = [(key, name, self.PLATFORM_INFO[key]["host"],
                 self.PLATFORM_INFO[key]["feature"], self.PLATFORM_INFO[key]["tags"])
                for key, name in self.PLATFORM_ORDER]
        self.seg = W.PlatformCards(inner, options=opts, value="ai",
                                   command=self._on_platform_change, height=112)
        self.seg.pack(fill="x", pady=(p(7), 0))
        self.note_label = tk.Label(inner, text="", bg=W.BG, fg=W.TEXT_SUB,
                                   font=W.font(8), justify="left", anchor="w")
        self.note_label.pack(fill="x", anchor="w", pady=(p(6), 0))
        inner.bind("<Configure>",
                   lambda e: self.note_label.config(
                       wraplength=max(p(400), e.width - p(48))), add="+")

        # ---- ② 课程：扫描是主按钮，空态说清楚 ----
        s2 = W.Card(inner, height=96, pad=(16, 12))
        s2.pack(fill="x", pady=(p(12), 0))
        row = tk.Frame(s2.body, bg=W.CARD)
        row.pack(fill="x")
        tk.Label(row, text="②  选择课程", bg=W.CARD, fg=W.TEXT,
                 font=W.font(11, "bold")).pack(side="left")
        self.course_hint = tk.Label(row, text="还没扫描", bg=W.CARD,
                                    fg=W.TEXT_MUT, font=W.font(8))
        self.course_hint.pack(side="right", pady=(p(3), 0))

        form = tk.Frame(s2.body, bg=W.CARD)
        form.pack(fill="x", pady=(p(9), 0))
        self.course_var = tk.StringVar(value="")   # 空态：不伪造一个默认课程
        cf = tk.Frame(form, bg=W.CARD)
        cf.pack(side="left", fill="x", expand=True)
        self.btn_scan = W.NavButton(cf, text="扫描课程", kind="primary",
                                    height=38, width=108, command=self.on_scan_courses)
        self.btn_scan.pack(side="left", padx=(0, p(10)))
        self.course_picker = W.CoursePicker(
            cf, textvariable=self.course_var, on_refresh=self.on_scan_courses,
            height=38, placeholder="点「扫描课程」，再从列表里选")
        self.course_picker.pack(side="left", fill="x", expand=True)

        self._params = tk.Frame(form, bg=W.CARD)
        self._params.pack(side="left", padx=(p(12), 0))
        self.hours_var = tk.StringVar(value=str(DEFAULT_TARGET_HOURS))
        self.maxmin_var = tk.StringVar(value="300")
        self._field(self._params, "目标时长", self.hours_var, "时", 88)
        self._field(self._params, "最长挂机", self.maxmin_var, "分", 88)
        self._courses = []

        # ---- ③ 进度与停止：停止按钮跟着进度走 ----
        s3 = W.Card(inner, height=86, pad=(16, 12))
        s3.pack(fill="x", pady=(p(10), 0))
        prow = tk.Frame(s3.body, bg=W.CARD)
        prow.pack(fill="x")
        tk.Label(prow, text="③  运行中", bg=W.CARD, fg=W.TEXT,
                 font=W.font(11, "bold")).pack(side="left")
        self.btn_stop2 = W.NavButton(prow, text="■ 停止", kind="danger",
                                     height=30, width=88, command=self.on_stop)
        self.btn_stop2.pack(side="right")
        self.btn_stop2.config(state="disabled")
        self.pct_label = tk.Label(prow, text="0%", bg=W.CARD, fg=W.TEXT_MUT,
                                  font=W.font(9, "bold"))
        self.pct_label.pack(side="right", padx=(0, p(12)))
        self.prog = W.Progress(s3.body, height=10)
        self.prog.pack(fill="x", pady=(p(8), p(5)))
        self.prog_var = tk.StringVar(value="空闲中 — 选好平台与课程后点左侧操作")
        tk.Label(s3.body, textvariable=self.prog_var, bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(9), anchor="w", justify="left").pack(fill="x")

        # 平台说明 / 参数可见性 / 按钮状态：等所有控件建完再应用一次
        self._on_platform_change("ai")

        # ---- 日志 + 最新画面 ----
        bottom = tk.Frame(inner, bg=W.BG)
        bottom.pack(fill="both", expand=True, pady=(p(10), 0))
        lcard = W.Card(bottom, pad=(14, 12))
        lcard.pack(side="left", fill="both", expand=True)
        lhead = tk.Frame(lcard.body, bg=W.CARD)
        lhead.pack(fill="x")
        tk.Label(lhead, text="运行日志", bg=W.CARD, fg=W.TEXT,
                 font=W.font(10, "bold")).pack(side="left")
        tk.Label(lhead, text="实时输出", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(side="left", padx=(p(8), 0), pady=(p(2), 0))
        W.NavButton(lhead, text="清空", kind="mini", command=self._clear_log,
                    height=26, radius=8, width=54).pack(side="right")
        dark = W.RoundedCanvas(lcard.body, radius=10, fill=W.LOG_BG, outline="",
                               line_width=0, pad=(13, 10))
        dark.pack(fill="both", expand=True, pady=(p(9), 0))
        self.log = tk.Text(dark.body, bg=W.LOG_BG, fg=W.LOG_TEXT, bd=0, relief="flat",
                           highlightthickness=0, font=("Consolas", 9), wrap="word",
                           padx=0, pady=0, spacing1=2, spacing3=2, state="disabled",
                           insertbackground="#FFFFFF", selectbackground="#2A3A5A",
                           selectforeground="#FFFFFF")
        self.log.pack(fill="both", expand=True)
        for tag, color in (("time", "#6B7A93"), ("info", W.LOG_TEXT),
                           ("warn", "#FBBF24"), ("error", "#F87171"),
                           ("ok", "#4ADE80"), ("accent", "#93B4FF"),
                           ("muted", "#6B7A93")):
            self.log.tag_config(tag, foreground=color)

        scard = W.Card(bottom, width=200, pad=(12, 10))
        scard.pack(side="right", fill="y", padx=(p(10), 0))
        tk.Label(scard.body, text="最新画面", bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(9, "bold")).pack(anchor="w")
        self.shot_hint = tk.Label(scard.body, text="扫码 / 登录时看这里",
                                  bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8))
        self.shot_hint.pack(anchor="w", pady=(p(2), p(6)))
        self.shot_card = W.ShotCard(scard.body, width=176, height=104)
        self.shot_card.pack()

    def _field(self, parent, label, var, suffix="", width=88):
        """紧凑表单列：小标题 + 圆角输入框。"""
        p = W.s
        f = tk.Frame(parent, bg=W.CARD)
        f.pack(side="left")
        tk.Label(f, text=label, bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", pady=(0, p(3)))
        e = W.RoundEntry(f, textvariable=var, suffix=suffix,
                         width=width, height=34)
        e.pack()
        return e

    # ------------------------------------------------------------ 事件循环
    def _poll_bus(self):
        if self._closing:
            self._start_pump()
            return
        try:
            for kind, data in self.bus.poll():
                if kind == "log":
                    self._append_log(data)
                elif kind == "progress":
                    v = data.get("value")
                    if v is None:
                        self.prog.start_indeterminate()
                        self.pct_label.config(text="…")
                    else:
                        self.prog.set_value(v)
                        self.pct_label.config(text=f"{int(v)}%")
                    if data.get("text"):
                        self.prog_var.set(data["text"])
                elif kind == "status":
                    self._set_status(data.get("text", ""), "run")
                elif kind == "shot":
                    self._show_shot(data.get("path"))
                elif kind == "courses":
                    self._on_courses(data)
                elif kind == "done":
                    self._on_task_done(data)
        finally:
            self.root.after(110, self._poll_bus)

    def _append_log(self, d):
        self.log.config(state="normal")
        t = d.get("time") or ""
        if t:
            self.log.insert("end", f"{t}  ", "time")
        self.log.insert("end", d["msg"] + "\n", d.get("level", "info"))
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _show_shot(self, path):
        if not path or not os.path.exists(path):
            return
        self.shot_card.set_image(path)
        self._last_shot = path

    def _set_status(self, text, kind="idle"):
        palette = {
            "idle": (W.TEXT_SUB, "#EEF1F8"),
            "run": (W.ACCENT, W.ACCENT_SOFT),
            "ok": (W.GREEN, W.GREEN_SOFT),
            "err": (W.RED, W.RED_SOFT),
        }
        color, soft = palette.get(kind, palette["idle"])
        self.chip.set(text, color, soft)

    def _on_finish(self):
        pass

    def _on_platform_change(self, key=None):
        """切换平台：刷新说明、隐藏不适用参数、清空别的平台的课程列表。"""
        if key:
            self.platform_var.set(key)
            if self.seg.get() != key:          # 从代码切换时同步平台卡
                self.seg.set(key)
        plat = self.platform_var.get()
        info = self.PLATFORM_INFO.get(plat, {})
        self.note_label.config(text="注意：" + info.get("desc", ""))

        # 只有 AI 优课有单独的「刷观看时长」入口；其他平台藏掉免得误填
        if plat == "ai":
            self._params.pack(side="left", padx=(W.s(12), 0))
        else:
            self._params.pack_forget()

        # 课程列表按平台区分：换平台必须重新扫描，不能拿旧的课程名去跑
        if getattr(self, "_courses_platform", None) != plat:
            self._courses = []
            self._courses_platform = None
            self.course_var.set("")
            picker = getattr(self, "course_picker", None)
            if picker is not None:
                picker.set_items([])
        self._refresh_course_hint()

        if not self.runner.is_running():
            ai = plat == "ai"
            # zjy2 没适配答题；学习通视频必须播到结尾，没有单独的刷时长入口
            self._nav["watch"].config(state="normal" if ai else "disabled")
            self._nav["quiz"].config(state="normal" if plat in ("ai", "cx") else "disabled")

    def _refresh_course_hint(self):
        """课程区右侧实时说明：让用户知道现在选的是哪批扫描结果。"""
        if not hasattr(self, "course_hint"):
            return
        plat = getattr(self, "platform_var", None)
        plat = plat.get() if plat else ""
        if getattr(self, "_courses_platform", None) != plat or not self._courses:
            self.course_hint.config(text="还没扫描", fg=W.TEXT_MUT)
            return
        n = len(self._courses)
        brush = sum(1 for x in self._courses if x.get("brushable"))
        self.course_hint.config(text=f"识别 {n} 门 · 可刷 {brush}", fg=W.GREEN)

    def _on_courses(self, data):
        """后台扫描完课程列表，回填下拉框。"""
        items = data.get("items") or []
        self._courses = items
        self._courses_platform = data.get("platform")
        # 可刷课程 ≥2 门时，列表顶部加一条「全部可刷课程」：
        # 替别人刷课常常一账号十几门，一门门点太费事。
        n_brush = sum(1 for x in items if x.get("brushable"))
        picker_items = list(items)
        if n_brush >= 2:
            picker_items.insert(0, {
                "name": "__all__",
                "label": f"★ 全部可刷课程（{n_brush} 门）",
                "brushable": True, "tag": "批量", "note": "全部",
            })
        self.course_picker.set_items(picker_items)
        # 若当前选中的课不在新列表里，自动选中第一门具体课程
        # （不默认选「全部可刷」，误点③一下刷十几门课代价太大）
        cur = self.course_var.get()
        names = [x.get("name") for x in picker_items]
        if picker_items and cur not in names:
            first = next((x for x in picker_items
                          if x.get("brushable") and x.get("name") != "__all__"),
                         picker_items[-1])
            self.course_var.set(first["name"])
            self.course_picker._render()
        self._refresh_course_hint()

    def on_scan_courses(self):
        if self.runner.is_running():
            return
        platform = self.platform_var.get()
        self.course_hint.config(text="扫描中…", fg=W.ACCENT)
        self._run(lambda: self.runner.action_list_courses(platform), nav=None)
        self._set_status("扫描课程中…", "run")

    def on_settings(self):
        from ui.llm_settings import LLMSettingsDialog
        LLMSettingsDialog(self.root, on_saved=self._on_llm_saved)

    def on_about(self):
        """关于窗口：版本、开发信息、更新日志。任务运行中也允许打开。"""
        from ui.about import AboutDialog
        existing = getattr(self, "_about", None)
        if existing is not None:
            try:
                if existing.top.winfo_exists():     # 已经开着就提到前面，不再开第二个
                    existing.top.deiconify()
                    existing.top.lift()
                    existing.top.focus_force()
                    return
            except Exception:
                pass
        self._about = AboutDialog(self.root)

    def _on_llm_saved(self, cfg):
        self._append_log({"time": "", "msg": f"大模型设置已保存：{cfg.get('model', '')}",
                          "level": "ok"})

    def on_quiz(self):
        from config import llm_config
        cfg = llm_config()
        if not (cfg.get("base") and cfg.get("key") and cfg.get("model")):
            if messagebox.askyesno(
                    "还没配置大模型",
                    "自动答题是软件自己去调大模型，需要你填接口地址和 API Key。\n"
                    "现在去填吗？"):
                self.on_settings()
            return
        ans = messagebox.askyesnocancel(
            "自动答题",
            "是：正式作答，并按设置提交\n"
            "否：试运行（只解析题目、显示答案，不点选也不提交）\n\n"
            "第一次用建议先试运行，核对题目和答案能对上再正式跑。")
        if ans is None:
            return
        dry = (ans is False)
        # tkinter 变量只能在主线程读：先取值再丢给后台线程
        platform, course = self.platform_var.get(), self.course_var.get()
        self._run(lambda: self.runner.action_answer_quiz(
            platform, course, dry_run=dry), nav="quiz")

    def on_help(self):
        messagebox.showinfo(
            "功能说明 · StudyBuddy",
            "① 启动浏览器 / 登录\n"
            "    点开会先问登录方式，两种都支持：\n"
            "    · 账号密码：自动填表提交。三个平台通用，替别人刷课时不用找本人手机；\n"
            "      平台会弹一次滑块拼图，请在弹出的浏览器窗口里拖一下，拖完程序自动继续。\n"
            "      勾选「记住账号密码」后，下次点①直接登录（明文存本机，公用电脑别勾）。\n"
            "    · 扫码 / 手动：在弹出的窗口里自己扫码或输账号密码。\n"
            "    登录一次后，只要不点「关闭浏览器」，下次打开仍在登录状态。\n"
            "    学习通使用独立的 Chrome/Edge 窗口（平台要求有头、不能失焦）。\n\n"
            "【扫描课程】选好平台后，点任务参数里的「扫描课程」\n"
            "    自动读取你在该平台的全部课程，列成下拉框；每项旁边标着\n"
            "    「可刷」（教学内容里有视频/文档）或「无课」（教学内容是空的）。\n"
            "    点课程名即可选中，不用再手打（打错字会找不到课程）。\n"
            "    换平台后列表会清空，需要重新扫描一次。\n\n"
            "② 读取学习进度\n"
            "    AI 优课先读首页的「观看时长 / 学习次数」；如果这门课不计学时\n"
            "    （页面上没有这项数字），会自动改为统计目录里的刷课完成度。\n"
            "    zjy2 / 学习通没有学时统计，直接读任务点完成度（已完成 / 总数）。\n\n"
            "③ 刷课（一键过完）\n"
            "    把目录里未完成的资源逐个打开：视频直接把进度条拖到末尾，\n"
            "    文档翻到最后一页。不耗真实观看时间，速度快。\n"
            "    网速慢时会先等页面真的加载完再动手，日志里会写「继续等…」，\n"
            "    这是在等加载，不是卡住；每项都会确认进度真的到 100% 才报完成。\n"
            "    若该课程已全部完成，会直接提示「无需刷课」。\n"
            "    · 全程自动静音，浏览器不会出声（任务结束后声音控制权还给你）；\n"
            "    · 视频的「检测到上次播放…是否继续？」和文档的「上次观看到第X页…」\n"
            "      两类续播弹窗都会自动点掉，日志会记一行；\n"
            "    · 连续 2 项进度都没涨会自动停止，不会在 0% 状态下白刷整门课；\n"
            "    · 目录里的测验 / 作业 / 考试不会自动作答，会列出来提醒你自己做。\n"
            "    · 想一次刷完账号下所有课：课程下拉选「★ 全部可刷课程」，再点③。\n"
            "      无课自动跳过，每门刷完有小结，最后汇总失败清单；停止按钮门与门之间生效。\n"
            "    学习通：视频不能拖进度条，必须 1 倍速播到结尾（耗时≈视频时长）；\n"
            "    脚本会静音、防失焦暂停、自动关续播弹窗，并只认视频上的绿标，\n"
            "    不把右边目录的橙色误判成没刷完。窗口别最小化。\n\n"
            "④ 刷观看时长（仅 AI 优课）\n"
            "    按目标学时真实播放视频（静音播放）。这一步不能拖进度条，拖了平台不计时长。\n"
            "    浏览器窗口要保持开着（可以最小化），关掉就中断。\n"
            "    学习通没有单独学时入口：③ 刷课本身就是播到结尾真实计时。\n\n"
            "⑤ 复核完成情况\n"
            "    统计目录里已完成 / 总数，用来确认刷课结果。\n\n"
            "⑥ 自动答题（大模型）\n"
            "    自动进「作业 / 测验 / 考试」，读题 → 大模型作答 → 点选填空 → 提交，\n"
            "    提交后会把得分记进日志。需要先在「⚙ 大模型设置」里填接口和 Key（用你自己的）。\n"
            "    第一次用请选「试运行」：只解析题目和答案、不点不交，核对无误再正式跑。\n"
            "    考试默认不参与（限时且交卷不可改），要跑考试需在设置里勾选。\n"
            "    学习通：题目来自课程自身的测验页（含章节内嵌测验），软件抓题后自己调\n"
            "    大模型生成答案，再按题号逐题填答；缺答案会整卷跳过，不会空卷提交。\n\n"
            "【平台怎么选】看浏览器地址栏：\n"
            "    ai.icve.com.cn    → 选「AI 优课」。卡片式首页，有学时统计。\n"
            "    zjy2.icve.com.cn  → 选「zjy2」。树形目录，资源后面带百分比。\n"
            "    v8.chaoxing.com   → 选「学习通」。独立浏览器，扫码登录 mooc2 课程。\n"
            "    三者页面结构完全不同，选错会提示找不到元素。\n\n"
            "【停止当前任务】随时可中止，已完成的资源不会回退。",
        )

    def _on_task_done(self, data):
        self.prog.stop_indeterminate()
        self._set_busy(False)
        self._set_status("完成" if data.get("ok") else "已停止 / 出错",
                         "ok" if data.get("ok") else "err")
        if data.get("summary"):
            self._append_log({"time": "", "msg": f"→ {data['summary']}", "level": "accent"})

    # ------------------------------------------------------------ 忙闲控制
    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in self._nav.values():
            b.config(state=state)
        self.btn_close.config(state=state)
        self.btn_help.config(state=state)
        self.btn_settings.config(state=state)
        self.btn_scan.config(state=state)
        self.course_picker.config(state=state)
        self.btn_stop.config(state="normal" if busy else "disabled")
        self.btn_stop2.config(state="normal" if busy else "disabled")
        if not busy:
            for b in self._nav.values():
                b.set_selected(False)
            self._on_platform_change()      # zjy2 时重新禁用「刷观看时长」

    # -------------------------------------------------------------- 动作
    def _run(self, fn, nav=None):
        if self.runner.start(fn):
            self._set_busy(True)
            for k, b in self._nav.items():
                b.set_selected(k == nav)
            self._set_status("运行中…", "run")
            self.prog_var.set("")
            self.pct_label.config(text="0%")
            self.prog.set_value(0)

    def on_open_site(self):
        """①：先问清「账号密码」还是「扫码 / 手动」，再动手。

        替别人刷课时账号主往往不在旁边，扫码不现实，所以登录方式要能选。
        """
        if self.runner.is_running():
            return
        platform = self.platform_var.get()
        LoginDialog(self.root, platform,
                    on_submit=lambda choice: self._start_login(platform, choice)).show()

    def _start_login(self, platform, choice):
        if self.runner.is_running():        # 弹窗开着时可能已经点了别的任务
            return
        login = None
        if choice.get("mode") == "password":
            user, pwd = choice["user"], choice["pwd"]
            remember = bool(choice.get("remember"))
            save_account(platform, user, pwd, remember=remember)
            login = {"user": user, "pwd": pwd}
            if remember:
                self.bus.log("已记住该账号，下次打开可直接登录（密码明文存于本机设置文件）")
            else:
                self.bus.log("本次不保存账号密码，关闭浏览器后需重新输入")
        else:
            self.bus.log("手动登录：在弹出的浏览器窗口里扫码或用账号密码登录")

        def job():
            if platform == "cx":
                return self.runner.action_open_site("", platform="cx", login=login)
            self.bus.log("启动受控浏览器…")
            return self.runner.action_open_site(AI_ICVE_HOME, attach=False,
                                                platform=platform, login=login)
        self._run(job, nav="open")

    def on_read_time(self):
        platform, course = self.platform_var.get(), self.course_var.get()
        self._run(lambda: self.runner.action_read_time(platform, course), nav="read")

    def on_brush(self):
        name = self.course_var.get()
        if not name:
            messagebox.showinfo("先选课程", "还没选课程。点「扫描课程」识别后再选一门，"
                                            "或选「全部可刷课程」批量刷。")
            return
        courses = list(self._courses) if name == "__all__" else None
        if name == "__all__" and not any(c.get("brushable") for c in (courses or [])):
            messagebox.showinfo("没有可刷课程", "扫描结果里没有可刷课程，先点「扫描课程」重新识别。")
            return
        platform = self.platform_var.get()
        self._run(lambda: self.runner.action_brush_course(platform, name, courses),
                  nav="brush")

    def on_watch(self):
        try:
            h = float(self.hours_var.get())
            m = float(self.maxmin_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "目标时长与最长挂机需为数字")
            return
        platform = self.platform_var.get()
        self._run(lambda: self.runner.action_watch_time(h, m, platform=platform),
                  nav="watch")

    def on_verify(self):
        platform, course = self.platform_var.get(), self.course_var.get()
        self._run(lambda: self.runner.action_verify(platform, course), nav="verify")

    def on_stop(self):
        self.runner.stop()

    def on_close_browser(self):
        def job():
            self.runner.close_browser()
            return "浏览器已关闭"
        self._run(job)

    def on_close(self):
        """退出程序（v1.4.1）。

        旧的实现直接在 GUI 主线程里 `close_browser()`：它要等 Edge 优雅退出
        （先 WM_CLOSE 让 cookie 落盘，再等端口消失，必要时强杀），总共几秒到
        十几秒。主线程被占住，Windows 就把窗口标成「未响应」，然后才消失 ——
        用户看到的就是「点×卡住，过一会儿自己关了」。

        现在改成：先 `withdraw()` 把窗口收起（视觉上立刻关闭），
        收尾放到后台线程，主线程继续用 `after` 轮询，完成后再真正 destroy。
        代价是进程会在后台多活几秒（无窗口），换来点击即可关闭的观感。
        """
        if self._closing:
            return
        if self.runner.is_running():
            if not messagebox.askyesno(
                    "确认退出",
                    "任务正在运行，确定退出吗？\n"
                    "未完成的资源会保持原状，下次可以接着刷。"):
                return
        self._closing = True
        self._shutdown_done = threading.Event()
        self._shutdown_deadline = time.time() + 25      # 兜底：卡住也要退出去
        try:
            self._set_status("正在退出…", "run")
            self.root.withdraw()
        except Exception:
            pass
        threading.Thread(target=self._shutdown_worker, daemon=True).start()
        self._start_pump()

    def _shutdown_worker(self):
        """后台收尾：停任务 → 关 Edge。UI 线程绝不碰 Playwright 对象。"""
        try:
            self.runner.shutdown()
        except Exception:
            pass
        finally:
            if self._shutdown_done is not None:
                self._shutdown_done.set()

    def _start_pump(self):
        """只起一条轮询链：先 destroy 再被排队的回调，会报「invalid command name」。"""
        if self._pump_started or self._destroyed:
            return
        self._pump_started = True
        self.root.after(120, self._pump_shutdown)

    def _pump_shutdown(self):
        if self._destroyed:
            return
        done = self._shutdown_done is None or self._shutdown_done.is_set()
        if done or time.time() >= self._shutdown_deadline:
            self._destroyed = True
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        self.root.after(120, self._pump_shutdown)
