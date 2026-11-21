"""StudyBuddy 主界面。

v1.2.0 重做视觉：自绘圆角卡片 + 左侧导航 + 深色日志面板，并按屏幕 DPI 缩放，
在 125% / 150% 缩放下文字不再被系统拉伸糊掉。

布局：
- 左侧 246px 导航栏：品牌、①~⑤ 操作、停止/关闭、说明与版本
- 右侧内容区：标题与状态、平台卡、参数卡、日志卡、进度与截图
所有视觉元素由 ui/widgets.py 自绘，不依赖 ttk 系统主题。
"""
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from config import APP_NAME, APP_VERSION, AI_ICVE_HOME, DEFAULT_TARGET_HOURS
from core.events import EventBus
from core.tasks import TaskRunner
from ui import widgets as W


class App:
    # 平台说明：光写 "AI 优课 / zjy2" 用户看不懂，补上域名和界面特征，
    # 让人能照着浏览器地址栏自己判断该选哪个。
    PLATFORM_INFO = {
        "ai": {
            "host": "ai.icve.com.cn",
            "desc": "卡片式首页，卡片上显示「观看时长 / 学习次数」。\n"
                    "刷课＝视频拖到末尾、文档翻到末页；刷时长＝真实播放累计学时。\n"
                    "刷课时自动静音，续播弹窗自动关；测验/作业需自己作答。",
        },
        "zjy2": {
            "host": "zjy2.icve.com.cn",
            "desc": "左侧是「章节 → 小节 → 资源」树形目录，资源后面跟着百分比。\n"
                    "只支持刷课（视频拖到末尾 / 文档翻到末页），这个平台没有学时统计。\n"
                    "网速慢时会先等页面加载完再动手，日志里会显示「继续等…」。\n"
                    "刷课时自动静音，续播弹窗自动关；测验/作业需自己作答。",
        },
    }

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
        bar = tk.Frame(self.root, bg=W.CARD, width=p(246))
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=W.LINE, width=1).place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # 品牌区
        brand = tk.Frame(bar, bg=W.CARD)
        brand.pack(fill="x", padx=p(20), pady=(p(22), p(18)))
        self._build_logo(brand)
        texts = tk.Frame(brand, bg=W.CARD)
        texts.pack(side="left")
        tk.Label(texts, text="StudyBuddy", bg=W.CARD, fg=W.TEXT,
                 font=W.font(13, "bold")).pack(anchor="w")
        tk.Label(texts, text="智慧职教 · 学习助手", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", pady=(p(2), 0))

        tk.Label(bar, text="操作", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", padx=p(22), pady=(0, p(6)))

        items = [
            ("open", "启动浏览器 / 登录", 1, self.on_open_site),
            ("read", "读取观看时长", 2, self.on_read_time),
            ("brush", "刷课（一键过完）", 3, self.on_brush),
            ("watch", "刷观看时长", 4, self.on_watch),
            ("verify", "复核完成情况", 5, self.on_verify),
            ("quiz", "自动答题（大模型）", 6, self.on_quiz),
        ]
        for key, label, num, cmd in items:
            b = W.NavButton(bar, text=label, number=num, command=cmd, height=42)
            b.pack(fill="x", padx=p(12), pady=p(2))
            self._nav[key] = b

        W.divider(bar, pady=(14, 10), padx=18)

        self.btn_stop = W.NavButton(bar, text="停止当前任务", kind="danger",
                                    command=self.on_stop, height=40)
        self.btn_stop.pack(fill="x", padx=p(12), pady=p(2))
        self.btn_stop.config(state="disabled")

        self.btn_close = W.NavButton(bar, text="关闭浏览器", kind="ghost",
                                     command=self.on_close_browser, height=40)
        self.btn_close.pack(fill="x", padx=p(12), pady=p(2))

        # 底部
        bottom = tk.Frame(bar, bg=W.CARD)
        bottom.pack(side="bottom", fill="x", padx=p(12), pady=(0, p(14)))
        self.btn_settings = W.NavButton(bottom, text="⚙ 大模型设置", kind="link",
                                        command=self.on_settings, height=30)
        self.btn_settings.pack(fill="x")
        self.btn_help = W.NavButton(bottom, text="看不懂？功能说明", kind="link",
                                    command=self.on_help, height=30)
        self.btn_help.pack(fill="x")
        # 版本号兼作入口：点开「关于此软件」看开发信息与更新日志
        self.btn_about = W.NavButton(bottom, text=f"关于此软件 · v{APP_VERSION}",
                                     kind="link", command=self.on_about, height=30)
        self.btn_about.pack(fill="x")

        # 侧栏留白太多，用一块淡色提示填空，顺带交代两条容易踩的坑
        tip = W.RoundedCanvas(bar, radius=10, fill="#F7F8FC", outline="", line_width=0,
                              pad=(13, 11), height=88)
        tip.pack(side="bottom", fill="x", padx=p(14), pady=(0, p(8)))
        tk.Label(tip.body, text="小提示", bg="#F7F8FC", fg=W.TEXT_SUB,
                 font=W.font(8, "bold")).pack(anchor="w")
        tk.Label(tip.body, text="关闭浏览器后要重新登录；\n已完成的课时不会回退。",
                 bg="#F7F8FC", fg=W.TEXT_MUT, font=W.font(8),
                 justify="left").pack(anchor="w", pady=(p(4), 0))

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
        inner.pack(fill="both", expand=True, padx=p(26), pady=(p(20), p(18)))

        # ---- 顶部标题 + 状态 ----
        head = tk.Frame(inner, bg=W.BG)
        head.pack(fill="x")
        left = tk.Frame(head, bg=W.BG)
        left.pack(side="left")
        tk.Label(left, text="工作台", bg=W.BG, fg=W.TEXT,
                 font=W.font(16, "bold")).pack(anchor="w")
        tk.Label(left, text="选好平台后，点左侧任一操作开始", bg=W.BG, fg=W.TEXT_MUT,
                 font=W.font(9)).pack(anchor="w", pady=(p(3), 0))
        self.chip = W.StatusChip(head, text="就绪")
        self.chip.pack(side="right", pady=(p(4), 0))

        # ---- 平台卡 ----
        pcard = W.Card(inner, height=178, pad=(18, 14))
        pcard.pack(fill="x", pady=(p(14), 0))
        body = pcard.body
        row = tk.Frame(body, bg=W.CARD)
        row.pack(fill="x")
        tk.Label(row, text="学习平台", bg=W.CARD, fg=W.TEXT,
                 font=W.font(10, "bold")).pack(side="left")
        tk.Label(row, text="按浏览器地址栏的域名选", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(side="right", pady=(p(2), 0))

        self.platform_var = tk.StringVar(value="ai")
        self.seg = W.Segmented(body, options=[("ai", "AI 优课"), ("zjy2", "zjy2")],
                               value="ai", command=self._on_platform_change,
                               width=300, height=36)
        self.seg.pack(anchor="w", pady=(p(10), p(10)))

        self.host_label = tk.Label(body, text="", bg=W.CARD, fg=W.ACCENT,
                                   font=W.font(9, "bold"))
        self.host_label.pack(anchor="w")
        self.note_label = tk.Label(body, text="", bg=W.CARD, fg=W.TEXT_SUB,
                                   font=W.font(9), justify="left", anchor="w")
        self.note_label.pack(anchor="w", pady=(p(3), 0), fill="x")

        # 平台说明随窗口宽度换行
        pcard.bind("<Configure>",
                   lambda e: self.note_label.config(
                       wraplength=max(p(320), e.width - p(60))), add="+")
        self._on_platform_change("ai")

        # ---- 参数卡 ----
        ccard = W.Card(inner, height=120, pad=(18, 14))
        ccard.pack(fill="x", pady=(p(12), 0))
        cb = ccard.body
        tk.Label(cb, text="任务参数", bg=W.CARD, fg=W.TEXT,
                 font=W.font(10, "bold")).pack(anchor="w")

        form = tk.Frame(cb, bg=W.CARD)
        form.pack(fill="x", pady=(p(8), 0))
        form.columnconfigure(0, weight=1)

        self.course_var = tk.StringVar(value="大学生心理健康")
        self.hours_var = tk.StringVar(value=str(DEFAULT_TARGET_HOURS))
        self.maxmin_var = tk.StringVar(value="300")

        self._field(form, 0, "课程名", self.course_var, suffix="")
        self._field(form, 1, "目标时长", self.hours_var, suffix="时", width=104,
                    padx=(p(14), 0))
        self._field(form, 2, "最长挂机", self.maxmin_var, suffix="分", width=104,
                    padx=(p(14), 0))

        # ---- 日志卡 ----
        lcard = W.Card(inner, height=180, pad=(16, 14))
        lcard.pack(fill="both", expand=True, pady=(p(12), 0))
        lb = lcard.body
        lhead = tk.Frame(lb, bg=W.CARD)
        lhead.pack(fill="x")
        tk.Label(lhead, text="运行日志", bg=W.CARD, fg=W.TEXT,
                 font=W.font(10, "bold")).pack(side="left")
        tk.Label(lhead, text="实时输出", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(side="left", padx=(p(8), 0), pady=(p(2), 0))
        W.NavButton(lhead, text="清空", kind="mini", command=self._clear_log,
                    height=26, radius=8, width=54).pack(side="right")

        dark = W.RoundedCanvas(lb, radius=10, fill=W.LOG_BG, outline="", line_width=0,
                               pad=(14, 10), height=140)
        dark.pack(fill="both", expand=True, pady=(p(10), 0))
        self.log = tk.Text(dark.body, bg=W.LOG_BG, fg=W.LOG_TEXT, bd=0, relief="flat",
                           highlightthickness=0, font=("Consolas", 9), wrap="word",
                           padx=0, pady=0, spacing1=2, spacing3=2, state="disabled",
                           insertbackground="#FFFFFF", selectbackground="#2A3A5A",
                           selectforeground="#FFFFFF")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("time", foreground="#5D6B85")
        self.log.tag_config("info", foreground=W.LOG_TEXT)
        self.log.tag_config("warn", foreground="#FBBF24")
        self.log.tag_config("error", foreground="#F87171")
        self.log.tag_config("ok", foreground="#4ADE80")
        self.log.tag_config("accent", foreground="#93B4FF")
        self.log.tag_config("muted", foreground="#6B7A93")
        self.log.tag_config("muted", foreground="#6B7A93")

        # ---- 底部：进度 + 截图 ----
        foot = tk.Frame(inner, bg=W.BG, height=p(124))
        foot.pack(fill="x", pady=(p(14), 0))
        foot.pack_propagate(False)

        pbox = tk.Frame(foot, bg=W.BG)
        pbox.pack(side="left", fill="both", expand=True, pady=(p(6), 0))
        prow = tk.Frame(pbox, bg=W.BG)
        prow.pack(fill="x")
        tk.Label(prow, text="任务进度", bg=W.BG, fg=W.TEXT_SUB,
                 font=W.font(9)).pack(side="left")
        self.pct_label = tk.Label(prow, text="0%", bg=W.BG, fg=W.TEXT_MUT,
                                  font=W.font(9, "bold"))
        self.pct_label.pack(side="right")
        self.prog = W.Progress(pbox, height=8)
        self.prog.pack(fill="x", pady=(p(9), p(9)))
        self.prog_var = tk.StringVar(value="空闲中")
        tk.Label(pbox, textvariable=self.prog_var, bg=W.BG, fg=W.TEXT_MUT,
                 font=W.font(9), justify="left", anchor="w").pack(fill="x")

        sbox = tk.Frame(foot, bg=W.BG)
        sbox.pack(side="right", padx=(p(20), 0), pady=(p(6), 0))
        tk.Label(sbox, text="最新画面", bg=W.BG, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", pady=(0, p(5)))
        self.shot_card = W.ShotCard(sbox, width=196, height=96)
        self.shot_card.pack()

    def _field(self, parent, col, label, var, suffix="", width=0, padx=(0, 0)):
        """表单列：小标题 + 圆角输入框。col=0 那列自动占满剩余宽度。"""
        p = W.s
        f = tk.Frame(parent, bg=W.CARD)
        if col == 0:
            f.grid(row=0, column=col, sticky="ew", padx=padx)
        else:
            f.grid(row=0, column=col, sticky="w", padx=padx)
        tk.Label(f, text=label, bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w", pady=(0, p(4)))
        e = W.RoundEntry(f, textvariable=var, suffix=suffix,
                         width=width or 120, height=38)
        if width:
            e.pack()
        else:
            e.pack(fill="x")
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
        """切换平台：刷新说明、同步分段控件、按平台禁用不适用功能。"""
        if key:
            self.platform_var.set(key)
            if self.seg.get() != key:          # 从代码切换时同步分段控件
                self.seg.set(key)
        info = self.PLATFORM_INFO.get(self.platform_var.get(), {})
        self.host_label.config(text=info.get("host", ""))
        self.note_label.config(text=info.get("desc", ""))
        if not self.runner.is_running():
            ai = self.platform_var.get() == "ai"
            # zjy2 没有学时统计、题目页也还没适配 → 这两项在 zjy2 下置灰
            self._nav["watch"].config(state="normal" if ai else "disabled")
            self._nav["quiz"].config(state="normal" if ai else "disabled")

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
        self._run(lambda: self.runner.action_answer_quiz(
            self.platform_var.get(), self.course_var.get(), dry_run=dry), nav="quiz")

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
            "    网速慢时会先等页面真的加载完再动手，日志里会写「继续等…」，\n"
            "    这是在等加载，不是卡住；每项都会确认进度真的到 100% 才报完成。\n"
            "    若该课程已全部完成，会直接提示「无需刷课」。\n"
            "    · 全程自动静音，浏览器不会出声（任务结束后声音控制权还给你）；\n"
            "    · 「检测到上次播放…是否继续？」这类弹窗会自动点掉，日志会记一行；\n"
            "    · 目录里的测验 / 作业 / 考试**不会自动作答**，会列出来提醒你自己做。\n\n"
            "④ 刷观看时长\n"
            "    按目标学时真实播放视频（静音播放）。这一步不能拖进度条，拖了平台不计时长。\n"
            "    浏览器窗口要保持开着（可以最小化），关掉就中断。\n\n"
            "⑤ 复核完成情况\n"
            "    统计目录里已完成 / 总数，用来确认刷课结果。\n\n"
            "⑥ 自动答题（大模型）\n"
            "    自动进「作业 / 测验 / 考试」，读题 → 大模型作答 → 点选填空 → 提交，\n"
            "    提交后会把得分记进日志。需要先在「⚙ 大模型设置」里填接口和 Key（用你自己的）。\n"
            "    第一次用请选「试运行」：只解析题目和答案、不点不交，核对无误再正式跑。\n"
            "    考试默认不参与（限时且交卷不可改），要跑考试需在设置里勾选。\n\n"
            "【平台怎么选】看浏览器地址栏：\n"
            "    ai.icve.com.cn    → 选「AI 优课」。卡片式首页，有学时统计。\n"
            "    zjy2.icve.com.cn  → 选「zjy2」。树形目录，资源后面带百分比。\n"
            "    两者都是智慧职教旗下的不同界面，选错会提示找不到元素。\n\n"
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
        self.btn_stop.config(state="normal" if busy else "disabled")
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
        def job():
            self.bus.log("启动受控浏览器（首次需在弹出的窗口里登录）…")
            return self.runner.action_open_site(AI_ICVE_HOME, attach=False)
        self._run(job, nav="open")

    def on_read_time(self):
        self._run(lambda: self.runner.action_read_time(self.platform_var.get()), nav="read")

    def on_brush(self):
        self._run(lambda: self.runner.action_brush_course(self.platform_var.get(),
                                                          self.course_var.get()), nav="brush")

    def on_watch(self):
        try:
            h = float(self.hours_var.get())
            m = float(self.maxmin_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "目标时长与最长挂机需为数字")
            return
        self._run(lambda: self.runner.action_watch_time(h, m), nav="watch")

    def on_verify(self):
        self._run(lambda: self.runner.action_verify(self.platform_var.get(),
                                                    self.course_var.get()), nav="verify")

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
