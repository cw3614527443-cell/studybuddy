"""「关于此软件」窗口。

用途：把版本、开发信息、更新日志、数据位置这些「用户偶尔要问一句」的东西
收进一个弹窗，不用让人去翻仓库里的 Markdown。

页面结构：
- 顶部：图标 + 名称 + 当前版本 + 一句话定位
- 分段控件：概览 / 更新日志
- 概览：基本信息、开发信息、数据与隐私、免责声明
- 更新日志：按版本倒序，每条改动带「新增 / 修复 / 优化」标签

两个页面都放在可滚动容器里：小屏（1366×768）或 150% 缩放下内容会超长，
滚动条是自绘的细条，和整体视觉一致（ttk 的滚动条还是 2000 年代的样子）。
"""
import os
import subprocess
import sys
import tkinter as tk

from config import APP_VERSION, BASE_DIR, DATA_DIR
from core.changelog import APP_META, RELEASES
import ui.widgets as W


# 标签配色：新增=主色，修复=绿，优化=紫，变更=琥珀，安全=红
TAG_COLORS = {
    "新增": (W.ACCENT, W.ACCENT_SOFT),
    "修复": (W.GREEN, W.GREEN_SOFT),
    "优化": ("#7C5CE0", "#F3EFFF"),
    "变更": ("#B45309", "#FDF6E7"),
    "安全": (W.RED, W.RED_SOFT),
}


def _open_path(path):
    """用系统默认方式打开路径（目录、网址都走这里）。"""
    try:
        if os.name == "nt":
            os.startfile(path)          # noqa: 只在 Windows 上存在
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class _SlimBar(tk.Canvas):
    """自绘滚动条：细长圆角滑块，可拖动、点轨道跳转，内容不满时自动隐藏。"""

    def __init__(self, master, target, width=6):
        super().__init__(master, bg=master.cget("bg"), highlightthickness=0, bd=0,
                         width=W.s(width))
        self._target = target
        self._first, self._last = 0.0, 1.0
        self._drag = None
        self._thumb = (0, 0)
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    # 由 canvas 的 yscrollcommand 调用
    def on_view(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except (TypeError, ValueError):
            return
        self._render()

    def _render(self):
        h = self.winfo_height()
        if h <= 1:
            return
        self.delete("all")
        frac = self._last - self._first
        if frac >= 0.999:
            self._thumb = (0, 0)
            return
        thumb = max(W.s(26), h * frac)
        y1 = (h - thumb) * (self._first / max(1e-6, 1.0 - frac))
        y2 = y1 + thumb
        self._thumb = (y1, y2)
        self.create_polygon(W.rounded_points(0, y1, W.s(5), y2, W.s(2.5)),
                            fill="#D5DBE7", outline="")

    def _press(self, e):
        frac = self._last - self._first
        if frac >= 0.999:
            return
        y1, y2 = self._thumb
        if y1 <= e.y <= y2:
            self._drag = (e.y, self._first)
            return
        h = max(1, self.winfo_height())
        self._target.yview_moveto(max(0.0, min(1.0 - frac, e.y / h - frac / 2)))

    def _motion(self, e):
        if not self._drag:
            return
        h = max(1, self.winfo_height())
        frac = self._last - self._first
        travel = max(1.0, h - max(W.s(26), h * frac))
        target = self._drag[1] + (e.y - self._drag[0]) / travel
        self._target.yview_moveto(max(0.0, min(1.0 - frac, target)))


class _ScrollArea(tk.Frame):
    """竖向滚动容器：Canvas + 内嵌 Frame + 自绘滚动条。"""

    def __init__(self, master, bg=W.CARD):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar = _SlimBar(self, self.canvas)
        self.bar.pack(side="right", fill="y", padx=(W.s(6), 0))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.on_view)
        self.inner.bind("<Configure>", lambda e: self._sync())
        self.canvas.bind("<Configure>", self._resize)

    def _sync(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, e):
        self.canvas.itemconfigure(self._win, width=max(1, e.width))
        self._sync()


class AboutDialog:
    def __init__(self, root):
        self.root = root
        self.p = W.s
        self._pages = {}
        self._scroll = None
        self._photos = []

        self.top = tk.Toplevel(root)
        self.top.title("关于 StudyBuddy")
        self.top.configure(bg=W.BG)
        self.top.transient(root)
        self.top.grab_set()
        self.top.resizable(True, True)

        w, h = W.s(640), W.s(660)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(w, int(sw * 0.92)), min(h, int(sh * 0.90))
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        self.top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.top.minsize(min(W.s(560), w), min(W.s(460), h))

        card = W.Card(self.top, height=h - W.s(28), pad=(W.s(22), W.s(18)))
        card.pack(fill="both", expand=True, padx=W.s(14), pady=W.s(14))
        body = card.body

        self._build_header(body)
        self._build_tabs(body)

        host = tk.Frame(body, bg=W.CARD)
        host.pack(fill="both", expand=True, pady=(W.s(10), 0))
        self._build_overview(host)
        self._build_changelog(host)
        self._show("about")

        self._build_footer(body)

        self.top.bind("<Escape>", lambda e: self.close())
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<MouseWheel>", self._on_wheel)

    # ------------------------------------------------------------- 顶部
    def _build_header(self, parent):
        p = self.p
        head = tk.Frame(parent, bg=W.CARD)
        head.pack(fill="x")

        logo = self._make_logo(head, 44)
        logo.pack(side="left", padx=(0, p(12)))

        texts = tk.Frame(head, bg=W.CARD)
        texts.pack(side="left", fill="x", expand=True)

        line = tk.Frame(texts, bg=W.CARD)
        line.pack(anchor="w")
        tk.Label(line, text=APP_META["name"], bg=W.CARD, fg=W.TEXT,
                 font=W.font(14, "bold")).pack(side="left")
        # 版本号用淡色胶囊，比正文更醒目又不抢标题
        chip = tk.Label(line, text=f"v{APP_VERSION}", bg=W.ACCENT_SOFT, fg=W.ACCENT,
                        font=W.font(8, "bold"), padx=p(7), pady=p(2))
        chip.pack(side="left", padx=(p(9), 0), pady=(p(4), 0))

        tk.Label(texts, text=APP_META["slogan"], bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(9)).pack(anchor="w", pady=(p(3), 0))
        desc = tk.Label(texts, text=APP_META["desc"], bg=W.CARD, fg=W.TEXT_MUT,
                        font=W.font(8), justify="left", wraplength=p(430))
        desc.pack(anchor="w", pady=(p(3), 0))

        # 窗口可拉宽：简介按实际宽度重排（宽度没变就不动，避免反复触发 Configure）
        def _rewrap(event, lbl=desc):
            if getattr(texts, "_wrap_w", None) == event.width:
                return
            texts._wrap_w = event.width
            lbl.configure(wraplength=max(p(280), event.width - p(8)))

        texts.bind("<Configure>", _rewrap)

    def _make_logo(self, parent, size):
        """优先用 icon.png，读不到就画一个主色圆角块 + S。"""
        p = self.p
        holder = tk.Frame(parent, bg=W.CARD, width=p(size), height=p(size))
        holder.pack_propagate(False)
        try:
            from PIL import Image, ImageTk
            from config import resource_path
            path = resource_path(os.path.join("assets", "icon.png"))
            if os.path.exists(path):
                im = Image.open(path).convert("RGBA").resize(
                    (p(size), p(size)), Image.LANCZOS)
                photo = ImageTk.PhotoImage(im)
                self._photos.append(photo)
                tk.Label(holder, image=photo, bg=W.CARD, bd=0).place(
                    x=0, y=0, width=p(size), height=p(size))
                return holder
        except Exception:
            pass
        c = tk.Canvas(holder, width=p(size), height=p(size), bg=W.CARD,
                      highlightthickness=0)
        c.pack()
        c.create_polygon(W.rounded_points(p(2), p(2), p(size - 2), p(size - 2), p(12)),
                         fill=W.ACCENT, outline="")
        c.create_text(p(size / 2), p(size / 2), text="S", fill="#FFFFFF",
                      font=W.font(16, "bold"))
        return holder

    def _build_tabs(self, parent):
        p = self.p
        tk.Frame(parent, bg=W.LINE, height=1).pack(fill="x", pady=(p(14), p(12)))
        self.seg = W.Segmented(parent,
                               options=[("about", "概览"), ("log", "更新日志")],
                               value="about", command=self._show,
                               width=200, height=32)
        self.seg.pack(anchor="w")

    def _build_footer(self, parent):
        p = self.p
        tk.Frame(parent, bg=W.LINE, height=1).pack(fill="x", pady=(p(12), p(10)))
        row = tk.Frame(parent, bg=W.CARD)
        row.pack(fill="x")
        W.NavButton(row, text="关闭", kind="primary", command=self.close,
                    height=34, width=p(76)).pack(side="right")
        W.NavButton(row, text="项目主页", kind="ghost",
                    command=lambda: _open_path(APP_META["repo"]),
                    height=34, width=p(90)).pack(side="right", padx=(0, p(8)))
        W.NavButton(row, text="打开数据目录", kind="ghost",
                    command=lambda: _open_path(DATA_DIR),
                    height=34, width=p(112)).pack(side="right", padx=(0, p(8)))

    # ------------------------------------------------------------- 分页
    def _show(self, key):
        if key not in self._pages:
            key = "about"
        self.seg.set(key)
        for k, (area, _frame) in self._pages.items():
            if k == key:
                area.pack(fill="both", expand=True)
            else:
                area.pack_forget()
        self._scroll = self._pages[key][0]

    def _on_wheel(self, e):
        area = self._scroll
        if area is None:
            return
        try:
            widget = area.canvas.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        node = widget
        while node is not None and node is not area.canvas:
            node = getattr(node, "master", None)
        if node is None:                    # 指针不在滚动区域内，不抢滚轮
            return
        area.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # --------------------------------------------------------- 概览页
    def _build_overview(self, host):
        p = self.p
        area = _ScrollArea(host, bg=W.CARD)
        frame = area.inner
        self._pages["about"] = (area, frame)

        pad = tk.Frame(frame, bg=W.CARD)
        pad.pack(fill="both", expand=True, padx=(0, p(4)))

        self._section(pad, "基本信息", first=True)
        self._kv(pad, "当前版本", f"v{APP_VERSION}")
        self._kv(pad, "发布日期", APP_META["released"])
        self._kv(pad, "适用平台", APP_META["os"])
        self._kv(pad, "运行环境", APP_META["runtime"])
        self._kv(pad, "界面语言", APP_META["lang"])

        self._section(pad, "开发信息")
        self._kv(pad, "开发者", APP_META["author"])
        self._kv(pad, "开源地址", APP_META["repo"], link=APP_META["repo"])
        self._kv(pad, "问题反馈", APP_META["issues"], link=APP_META["issues"])
        self._kv(pad, "许可证", APP_META["license"], wrap=True)

        self._section(pad, "数据与隐私")
        self._kv(pad, "数据目录", DATA_DIR, link=DATA_DIR, wrap=True)
        self._kv(pad, "程序目录", BASE_DIR, wrap=True)
        tk.Label(pad, text="登录态保存在本机浏览器的独立 profile 里，API Key 明文写在"
                           "数据目录的 settings.json 中；所有操作都在本机完成，"
                           "除答题时调用你自己填的大模型接口外，不上传任何数据。",
                 bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8), justify="left",
                 anchor="w").pack(fill="x", pady=(p(6), 0))

        self._section(pad, "免责声明")
        warn = W.RoundedCanvas(pad, radius=10, fill="#FFF8EC", outline="#F6E3BF",
                               line_width=1, pad=(p(12), p(10)), height=76)
        warn.pack(fill="x", pady=(p(2), p(4)))
        warn_text = tk.Label(warn.body, text=APP_META["disclaimer"], bg="#FFF8EC",
                             fg="#8A5A15", font=W.font(8), justify="left",
                             anchor="w", wraplength=p(470))
        warn_text.pack(fill="x")

        def _rewrap_warn(event, lbl=warn_text):
            if getattr(warn, "_wrap_w", None) == event.width:
                return
            warn._wrap_w = event.width
            lbl.configure(wraplength=max(p(200), event.width - p(26)))

        warn.bind("<Configure>", _rewrap_warn, add="+")     # 别覆盖 RoundedCanvas 自身重绘

        tk.Frame(pad, bg=W.CARD, height=p(6)).pack()        # 底部留白，别让末行贴着分隔线

    def _section(self, parent, title, first=False):
        p = self.p
        tk.Label(parent, text=title, bg=W.CARD, fg=W.TEXT, font=W.font(10, "bold")).pack(
            anchor="w", pady=(p(0) if first else p(16), p(6)))

    def _kv(self, parent, key, value, link=None, wrap=False):
        """一行「键 + 值」；link 给值时整行值变成可点击的链接。"""
        p = self.p
        row = tk.Frame(parent, bg=W.CARD)
        row.pack(fill="x", pady=p(2))

        kbox = tk.Frame(row, bg=W.CARD, width=p(74), height=p(20))
        kbox.pack(side="left", anchor="n")
        kbox.pack_propagate(False)
        tk.Label(kbox, text=key, bg=W.CARD, fg=W.TEXT_MUT, font=W.font(9),
                 anchor="w").pack(fill="x")

        color = W.ACCENT if link else W.TEXT_SUB
        lbl = tk.Label(row, text=value, bg=W.CARD, fg=color, font=W.font(9),
                       anchor="w", justify="left")
        lbl.pack(side="left", fill="x", expand=True)
        if link:
            lbl.configure(cursor="hand2")
            lbl.bind("<Button-1>", lambda e, t=link: _open_path(t))
            lbl.bind("<Enter>", lambda e: lbl.configure(fg=W.ACCENT_HOVER))
            lbl.bind("<Leave>", lambda e: lbl.configure(fg=W.ACCENT))

        # 值可能很长（路径、许可证），按行宽自适应换行；宽度没变就不重复设置，避免抖动
        def _wrap(event, row=row, lbl=lbl):
            if not wrap:
                return
            width = event.width
            if getattr(row, "_wrap_w", None) == width:
                return
            row._wrap_w = width
            lbl.configure(wraplength=max(p(160), width - p(74) - p(4)))

        if wrap:
            row.bind("<Configure>", _wrap)

    # ------------------------------------------------------- 更新日志页
    def _build_changelog(self, host):
        p = self.p
        area = _ScrollArea(host, bg=W.CARD)
        frame = area.inner
        self._pages["log"] = (area, frame)

        pad = tk.Frame(frame, bg=W.CARD)
        pad.pack(fill="both", expand=True, padx=(0, p(4)))
        tk.Label(pad, text="只列主要改动；完整的工程记录见仓库里的 CHANGELOG.md。",
                 bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8), justify="left").pack(
            anchor="w", pady=(0, p(6)))

        for i, rel in enumerate(RELEASES):
            self._release_block(pad, rel)
            if i < len(RELEASES) - 1:
                tk.Frame(pad, bg=W.LINE, height=1).pack(fill="x", pady=p(12))
        tk.Frame(pad, bg=W.CARD, height=p(6)).pack()

    def _release_block(self, parent, rel):
        p = self.p

        head = tk.Frame(parent, bg=W.CARD)
        head.pack(fill="x", pady=(p(6), 0))
        tk.Label(head, text=f"v{rel['version']}", bg=W.CARD, fg=W.TEXT,
                 font=W.font(11, "bold")).pack(side="left")
        if rel.get("latest"):
            tk.Label(head, text="最新", bg=W.GREEN_SOFT, fg=W.GREEN,
                     font=W.font(8, "bold"), padx=p(6), pady=p(1)).pack(
                side="left", padx=(p(8), 0), pady=(p(2), 0))
        tk.Label(head, text=rel["date"], bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(side="right", pady=(p(4), 0))

        tk.Label(parent, text=rel["title"], bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(9, "bold"), justify="left").pack(anchor="w", pady=(p(2), p(5)))

        for tag, text in rel["items"]:
            self._log_item(parent, tag, text)

    def _log_item(self, parent, tag, text):
        p = self.p
        fg, soft = TAG_COLORS.get(tag, (W.TEXT_SUB, "#F2F4F9"))
        row = tk.Frame(parent, bg=W.CARD)
        row.pack(fill="x", pady=p(3))

        chip = tk.Label(row, text=tag, bg=soft, fg=fg, font=W.font(8, "bold"),
                        padx=p(6), pady=p(1))
        chip.pack(side="left", anchor="n", pady=(p(1), 0))

        lbl = tk.Label(row, text=text, bg=W.CARD, fg=W.TEXT_SUB, font=W.font(9),
                       justify="left", anchor="w")
        lbl.pack(side="left", fill="x", expand=True, padx=(p(8), 0))

        def _wrap(event, row=row, lbl=lbl, chip=chip):
            width = event.width
            if getattr(row, "_wrap_w", None) == width:
                return
            row._wrap_w = width
            lbl.configure(wraplength=max(p(140), width - chip.winfo_reqwidth() - p(10)))

        row.bind("<Configure>", _wrap)

    # ------------------------------------------------------------- 关闭
    def close(self):
        try:
            self.top.unbind("<MouseWheel>")
        except Exception:
            pass
        try:
            self.top.grab_release()
            self.top.destroy()
        except Exception:
            pass
