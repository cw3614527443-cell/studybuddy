"""自绘现代控件库（零第三方依赖，仅用 tkinter Canvas）。

为什么要自绘：tkinter 自带的 ttk 控件在 Windows 上是 2000 年代的系统外观
（凸起边框、灰色底、方形按钮），没法做出圆角、扁平色块、悬停反馈。
这里用 Canvas 画圆角矩形 + 内嵌原生控件，得到接近现代 Web 的设计语言，
同时保持单文件打包、不引入 customtkinter 之类的额外依赖。

尺寸约定：本文件内所有数字都是“设计像素”（按 96dpi 设计），
实际像素 = 设计像素 × SCALE。SCALE 由 main.py 打开 DPI 感知后计算，
这样 125% / 150% 缩放下界面不会被系统整体拉伸导致发虚。
字号用 pt，Tk 会按 DPI 自动换算，不用手动乘。
"""
import math
import os
import tkinter as tk
import tkinter.font as tkfont

# ---------- 设计令牌（浅色主题） ----------
BG = "#F4F6FB"          # 应用底色
CARD = "#FFFFFF"
LINE = "#E7EBF3"
LINE_STRONG = "#DCE2EE"
TEXT = "#111827"
TEXT_SUB = "#5B6478"
TEXT_MUT = "#98A2B3"
ACCENT = "#4C6FFF"
ACCENT_HOVER = "#3B5BE0"
ACCENT_SOFT = "#EDF1FF"
NAV_HOVER = "#F5F7FC"
RED = "#E5484D"
RED_SOFT = "#FDF1F1"
GREEN = "#0E9F6E"
GREEN_SOFT = "#E9F8F2"
TRACK = "#EDF0F7"
LOG_BG = "#0F172A"
LOG_TEXT = "#C7D2E5"

FONT_FAMILY = "Microsoft YaHei UI"

SCALE = 1.0
_FONTS = {}


def init_scale(root):
    """按屏幕 DPI 计算缩放比（96dpi → 1.0，125% → 1.25）。

    环境变量 SB_UI_SCALE 可强制指定（开发时用来验证 100% / 150% 下的排版）。
    """
    global SCALE
    env = os.environ.get("SB_UI_SCALE")
    if env:
        try:
            SCALE = max(0.8, min(3.0, float(env)))
            # 字号也要一起对齐，否则会出现“小框大字”的假象
            root.tk.call("tk", "scaling", 96.0 / 72.0 * SCALE)
            return SCALE
        except ValueError:
            pass
    try:
        SCALE = max(1.0, round(root.winfo_fpixels("1i") / 96.0, 2))
    except Exception:
        SCALE = 1.0
    return SCALE


def s(v):
    """设计像素 → 实际像素。"""
    return int(round(float(v) * SCALE))


def font(size=10, weight="normal", family=FONT_FAMILY):
    key = (family, size, weight)
    f = _FONTS.get(key)
    if f is None:
        f = tkfont.Font(family=family, size=size, weight=weight)
        _FONTS[key] = f
    return f


def _bg_of(widget):
    """取父容器背景色，让画布融进父容器，避免出现方形灰边。"""
    try:
        return widget.cget("bg")
    except Exception:
        return BG


def rounded_points(x1, y1, x2, y2, r):
    """圆角矩形顶点（真圆弧，非 smooth 近似）。"""
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = []
    steps = 8
    corners = ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
               (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180))
    for cx, cy, a0 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + 90.0 * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


class RoundedCanvas(tk.Canvas):
    """圆角画布基类：背景圆角矩形 + 可选内嵌 body Frame。"""

    def __init__(self, master, radius=12, fill=CARD, outline=LINE, line_width=1,
                 with_body=True, pad=(0, 0), height=40, width=None, bg=None, **kw):
        super().__init__(master, bg=bg or _bg_of(master), highlightthickness=0, bd=0,
                         height=s(height), width=s(width) if width else None, **kw)
        self._radius = s(radius)
        self._fill = fill
        self._outline = outline
        self._line_width = line_width
        self._pad = (s(pad[0]), s(pad[1]))
        self._last_size = (0, 0)
        self.body = tk.Frame(self, bg=fill) if with_body else None
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, _e=None):
        size = (self.winfo_width(), self.winfo_height())
        if size == self._last_size or size[0] <= 1:
            return
        self._last_size = size
        self._render()

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        ins = self._line_width / 2.0
        self.create_polygon(rounded_points(ins, ins, w - ins, h - ins, self._radius),
                            fill=self._fill,
                            outline=self._outline if self._line_width else "",
                            width=self._line_width)
        if self.body is not None:
            px, py = self._pad
            self.create_window(px, py, window=self.body, anchor="nw",
                               width=max(1, w - px * 2), height=max(1, h - py * 2))

    def refresh(self):
        self._last_size = (0, 0)
        self._on_configure()


class Card(RoundedCanvas):
    """白色圆角卡片。高度固定，横向填满父容器。"""

    def __init__(self, master, radius=14, pad=(18, 15), height=120, **kw):
        super().__init__(master, radius=radius, fill=CARD, outline=LINE,
                         line_width=1, pad=pad, height=height, **kw)


class NavButton(RoundedCanvas):
    """操作项。

    kind:
        nav    —— 带序号徽标的导航项，选中时整行填充主色
        ghost  —— 无徽标的次要按钮
        danger —— 危险操作（停止任务）
        mini   —— 卡片右上角的小按钮
        link   —— 文字链接
        primary—— 主色实心按钮（对话框里的「保存」这类）
    """

    def __init__(self, master, text="", command=None, number=None, kind="nav",
                 height=42, radius=10, width=None, **kw):
        super().__init__(master, radius=radius, fill=kw.pop("fill", _bg_of(master)),
                         outline="", line_width=0, with_body=False,
                         height=height, width=width, **kw)
        self._text = text
        self._command = command
        self._number = number
        self._kind = kind
        self._state = "normal"
        self._hover = False
        self._selected = False
        self.configure(cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # --- 兼容 tk 的 config(state=...) 用法 ---
    def configure(self, cnf=None, **kw):
        changed = False
        if "state" in kw:
            self._state = kw.pop("state")
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            changed = True
        if "text" in kw:
            self._text = kw.pop("text")
            changed = True
        if cnf:
            super().configure(cnf)
        if kw:
            super().configure(**kw)
        if changed:
            self._render()

    config = configure

    def set_selected(self, value):
        self._selected = bool(value)
        self._render()

    def _color(self):
        sel, dis, hov = self._selected, self._state == "disabled", self._hover
        if self._kind == "nav":
            if sel:                       # 运行中的那一项即使被置灰也要保持高亮
                return ACCENT, "#FFFFFF"
            if dis:
                return None, "#C6CCD8"
            if hov:
                return NAV_HOVER, TEXT
            return None, TEXT
        if self._kind == "danger":
            if dis:
                return None, "#C6CCD8"
            return (RED_SOFT if hov else None), RED
        if self._kind == "primary":
            if dis:
                return "#C6CCD8", "#FFFFFF"
            return (ACCENT_HOVER if hov else ACCENT), "#FFFFFF"
        if self._kind == "link":
            return None, (ACCENT_HOVER if hov else ACCENT)
        if self._kind == "mini":
            if dis:
                return None, "#C6CCD8"
            return (LINE if hov else None), (TEXT if hov else TEXT_SUB)
        if dis:
            return None, "#C6CCD8"
        return (NAV_HOVER if hov else None), (TEXT if hov else TEXT_SUB)

    def _on_enter(self, _e):
        if self._state == "disabled":
            return
        self._hover = True
        self._render()

    def _on_leave(self, _e):
        self._hover = False
        self._render()

    def _on_press(self, _e):
        if self._state == "disabled":
            return
        self._hover = False
        self._render()

    def _on_release(self, e):
        if self._state == "disabled":
            return
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._hover = True
            self._render()
            if self._command:
                self._command()

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        bgcolor, fg = self._color()
        if bgcolor:
            self.create_polygon(rounded_points(0, 0, w, h, self._radius),
                                fill=bgcolor, outline="")
        if self._kind == "nav" and self._number is not None:
            cy = h / 2.0
            bx, r = s(27), s(11)
            dis = self._state == "disabled" and not self._selected
            badge_fill = "#FFFFFF" if self._selected else ("#F2F4F9" if dis else ACCENT_SOFT)
            badge_fg = ACCENT if self._selected else ("#C6CCD8" if dis else ACCENT)
            self.create_oval(bx - r, cy - r, bx + r, cy + r, fill=badge_fill, outline="")
            self.create_text(bx, cy - 1, text=str(self._number), fill=badge_fg,
                             font=font(9, "bold"))
            self.create_text(s(48), cy, text=self._text, anchor="w", fill=fg, font=font(10))
        elif self._kind == "mini":
            self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=font(9))
        elif self._kind in ("link", "primary"):
            self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=font(9))
        else:
            self.create_text(s(16), h / 2, text=self._text, anchor="w", fill=fg,
                             font=font(9))


class Segmented(RoundedCanvas):
    """分段选择器：灰底槽 + 白色选中块。"""

    def __init__(self, master, options, value=None, command=None,
                 height=36, radius=10, width=320, **kw):
        super().__init__(master, radius=radius, fill=TRACK, outline="", line_width=0,
                         with_body=False, height=height, width=width, **kw)
        self._opts = list(options)      # [(key, label), ...]
        self._value = value or self._opts[0][0]
        self._command = command
        self.configure(cursor="hand2")
        self.bind("<Button-1>", self._on_click)

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self._render()

    def _on_click(self, e):
        n = max(1, len(self._opts))
        pad = s(3)
        seg = max(1.0, (self.winfo_width() - pad * 2) / n)
        idx = max(0, min(n - 1, int((e.x - pad) / seg)))
        key = self._opts[idx][0]
        if key != self._value:
            self._value = key
            self._render()
            if self._command:
                self._command(key)

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        self.create_polygon(rounded_points(0, 0, w, h, self._radius), fill=TRACK, outline="")
        n = max(1, len(self._opts))
        pad = s(3)
        seg = (w - pad * 2) / n
        for i, (key, label) in enumerate(self._opts):
            x1 = pad + i * seg
            x2 = x1 + seg
            active = key == self._value
            if active:
                self.create_polygon(
                    rounded_points(x1, pad, x2, h - pad, max(1, self._radius - s(3))),
                    fill="#FFFFFF", outline=LINE, width=1)
            self.create_text((x1 + x2) / 2, h / 2, text=label,
                             fill=TEXT if active else TEXT_SUB,
                             font=font(9, "bold" if active else "normal"))


class RoundEntry(RoundedCanvas):
    """圆角输入框：画布画外框，内部放原生 Entry。"""

    def __init__(self, master, textvariable=None, height=38, radius=10, width=140,
                 suffix="", **kw):
        super().__init__(master, radius=radius, fill=CARD, outline=LINE_STRONG,
                         line_width=1, with_body=False, height=height, width=width, **kw)
        self._suffix = suffix
        self._focused = False
        self.entry = tk.Entry(self, textvariable=textvariable, bd=0, relief="flat",
                              bg=CARD, fg=TEXT, insertbackground=ACCENT,
                              highlightthickness=0, font=font(10),
                              disabledbackground="#F7F8FC", disabledforeground=TEXT_MUT)
        self.entry.bind("<FocusIn>", self._on_focus)
        self.entry.bind("<FocusOut>", self._on_blur)

    def _on_focus(self, _e):
        self._focused = True
        self._render()

    def _on_blur(self, _e):
        self._focused = False
        self._render()

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        outline = ACCENT if self._focused else LINE_STRONG
        self.create_polygon(rounded_points(0.5, 0.5, w - 0.5, h - 0.5, self._radius),
                            fill=CARD, outline=outline, width=1)
        right = w - s(12)
        if self._suffix:
            self.create_text(w - s(12), h / 2, text=self._suffix, anchor="e",
                             fill=TEXT_MUT, font=font(9))
            right -= font(9).measure(self._suffix) + s(4)
        self.entry.place(x=s(11), y=1, width=max(10, right - s(11)), height=h - 2)


class StatusChip(tk.Canvas):
    """状态胶囊：彩色圆点 + 文案 + 淡色底。"""

    def __init__(self, master, text="就绪", color=TEXT_SUB, soft="#F2F4F9", height=28):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0,
                         height=s(height))
        self._text = text
        self._color = color
        self._soft = soft
        self._h = s(height)
        self.bind("<Configure>", lambda e: self._render())

    def set(self, text, color=TEXT_SUB, soft="#F2F4F9"):
        self._text = text
        self._color = color
        self._soft = soft
        self._render()

    def _render(self):
        h = self._h
        w = font(9, "bold").measure(self._text) + s(40)
        self.configure(width=w)
        self.delete("all")
        self.create_polygon(rounded_points(0, 0, w, h, h / 2), fill=self._soft, outline="")
        cy = h / 2
        self.create_oval(s(12), cy - s(3.5), s(19), cy + s(3.5), fill=self._color, outline="")
        self.create_text(s(26), cy, text=self._text, anchor="w", fill=self._color,
                         font=font(9, "bold"))


class Progress(tk.Canvas):
    """细长圆角进度条，支持不定长动画。"""

    def __init__(self, master, height=8):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0,
                         height=s(height))
        self._value = 0.0
        self._indet = False
        self._phase = 0
        self._job = None
        self.bind("<Configure>", lambda e: self._render())

    def set_value(self, v):
        self.stop_indeterminate()
        self._value = max(0.0, min(100.0, float(v)))
        self._render()

    def value(self):
        return self._value

    def start_indeterminate(self):
        if self._indet:
            return
        self._indet = True
        self._phase = 0
        self._tick()

    def stop_indeterminate(self):
        self._indet = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._render()

    def _tick(self):
        if not self._indet:
            return
        self._phase = (self._phase + 3) % 140
        self._render()
        self._job = self.after(33, self._tick)

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        self.delete("all")
        r = h / 2
        self.create_polygon(rounded_points(0, 0, w, h, r), fill=TRACK, outline="")
        if self._indet:
            seg = max(s(40), w * 0.28)
            x = -seg + (w + seg) * (self._phase / 140.0)
            x1, x2 = max(0, x), min(w, x + seg)
            if x2 - x1 > 4:
                self.create_polygon(rounded_points(x1, 0, x2, h, r), fill=ACCENT, outline="")
        elif self._value > 0:
            fill_w = max(h, w * self._value / 100.0)
            self.create_polygon(rounded_points(0, 0, fill_w, h, r), fill=ACCENT, outline="")


class ShotCard(RoundedCanvas):
    """截图预览卡：无图时显示占位符。"""

    def __init__(self, master, width=196, height=112, radius=12):
        super().__init__(master, radius=radius, fill=CARD, outline=LINE, line_width=1,
                         with_body=False, width=width, height=height)
        self._photo = None
        self._placeholder = "暂无截图"

    def set_image(self, path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            box = (self.winfo_width() - s(14), self.winfo_height() - s(14))
            im.thumbnail((max(20, box[0]), max(20, box[1])))
            self._photo = ImageTk.PhotoImage(im)
            self._render()
        except Exception:
            self._placeholder = os.path.basename(str(path))[:24]
            self._render()

    def clear(self):
        self._photo = None
        self._render()

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        self.create_polygon(rounded_points(0.5, 0.5, w - 0.5, h - 0.5, self._radius),
                            fill=CARD, outline=LINE, width=1)
        if self._photo is not None:
            self.create_image(w / 2, h / 2, image=self._photo)
        else:
            # 空状态：画一个相框图标，比字符占位符干净
            cx, cy = w / 2, h / 2 - s(9)
            iw, ih = s(26), s(19)
            x1, y1 = cx - iw / 2, cy - ih / 2
            x2, y2 = cx + iw / 2, cy + ih / 2
            self.create_rectangle(x1, y1, x2, y2, outline="#D5DBE7", width=1)
            self.create_oval(x1 + s(4), y1 + s(4), x1 + s(9), y1 + s(9),
                             outline="#D5DBE7", width=1)
            self.create_line(x1 + s(3), y2 - s(3), x1 + s(10), y2 - s(10),
                             x2 - s(2), y2 - s(3), fill="#D5DBE7", width=1)
            self.create_text(w / 2, cy + ih / 2 + s(12), text=self._placeholder,
                             fill=TEXT_MUT, font=font(9))


def divider(master, pady=10, padx=0):
    """1px 分隔线（pady 支持单个数字或 (上, 下) 元组）。"""
    py = tuple(s(v) for v in pady) if isinstance(pady, (tuple, list)) else s(pady)
    f = tk.Frame(master, bg=LINE, height=1)
    f.pack(fill="x", pady=py, padx=s(padx))
    return f


class CoursePicker(RoundedCanvas):
    """课程下拉选择器：显示当前课程名，点开弹出带「可刷/无课」标记的列表。

    风格与 RoundEntry 一致（圆角白底 + 主色高亮），但只读——课程名必须从
    扫描出来的列表里选，避免手输错字导致 enter_course 找不到课程。

    items: [{name, brushable, note}]；通过 set_items() 更新。
    on_select(name) 选中回调；textvariable 同步纯课程名（去掉标记）。
    """

    def __init__(self, master, textvariable=None, items=None, on_select=None,
                 on_refresh=None, height=38, radius=10, width=200, placeholder="点右侧「扫描课程」选择", **kw):
        super().__init__(master, radius=radius, fill=CARD, outline=LINE_STRONG,
                         line_width=1, with_body=False, height=height, width=width, **kw)
        self._var = textvariable
        self._items = list(items or [])
        self._on_select = on_select
        self._on_refresh = on_refresh
        self._placeholder = placeholder
        self._hover = False
        self._popup = None
        self.configure(cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._toggle)

    def _set_hover(self, v):
        self._hover = v
        self._render()

    def set_items(self, items):
        self._items = list(items or [])
        self._render()

    def get_items(self):
        return list(self._items)

    def _current_text(self):
        try:
            return (self._var.get() if self._var else "") or ""
        except Exception:
            return ""

    def _render(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("all")
        outline = ACCENT if self._hover else LINE_STRONG
        self.create_polygon(rounded_points(0.5, 0.5, w - 0.5, h - 0.5, self._radius),
                            fill=CARD, outline=outline, width=1)
        txt = self._current_text()
        if txt:
            # 当前选中的课如果带标记，也显示标记
            mark = ""
            for it in self._items:
                if it.get("name") == txt:
                    mark = "（可刷）" if it.get("brushable") else "（无课）"
                    break
            self.create_text(s(11), h / 2, anchor="w", text=txt + mark,
                             fill=TEXT, font=font(10))
        else:
            self.create_text(s(11), h / 2, anchor="w", text=self._placeholder,
                             fill=TEXT_MUT, font=font(9))
        # 右侧下拉箭头
        ax = w - s(16)
        self.create_polygon([ax - s(4), h / 2 - s(1), ax + s(4), h / 2 - s(1), ax, h / 2 + s(4)],
                            fill=TEXT_SUB)

    def config(self, cnf=None, **kw):
        if "state" in kw:
            self._disabled = (kw.pop("state") == "disabled")
            self.configure(cursor="arrow" if self._disabled else "hand2")
        if cnf:
            super().config(cnf, **kw)
        elif kw:
            super().config(**kw)
        self._render()

    def _toggle(self, _e):
        if getattr(self, "_disabled", False):
            return
        if self._popup is not None:
            self._close_popup()
            return
        if not self._items:
            if self._on_refresh:
                self._on_refresh()
            return
        self._open_popup()

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    def _open_popup(self):
        """弹出无边框列表窗，定位在本控件正下方，宽度与控件一致。"""
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + s(4)
        w = self.winfo_width()
        max_h = s(360)

        pop = tk.Toplevel(self)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        frame = tk.Frame(pop, bg=CARD, highlightthickness=1, highlightbackground=LINE_STRONG)
        frame.pack(fill="both", expand=True)

        # 标题行
        head = tk.Frame(frame, bg=CARD)
        head.pack(fill="x", padx=s(10), pady=(s(9), s(4)))
        tk.Label(head, text="选择课程", bg=CARD, fg=TEXT_SUB,
                 font=font(8, "bold")).pack(side="left")
        refresh = tk.Label(head, text="↻ 重新扫描", bg=CARD, fg=ACCENT,
                           font=font(8), cursor="hand2")
        refresh.pack(side="right")
        refresh.bind("<Button-1>", lambda e: (self._close_popup(),
                                              self._on_refresh() if self._on_refresh else None))

        # 可滚动列表（Canvas + 内部 frame）
        canvas = tk.Canvas(frame, bg=CARD, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True, pady=(0, s(8)))
        inner = tk.Frame(canvas, bg=CARD)
        cw = w - s(20)
        canvas.create_window((0, 0), window=inner, anchor="nw", width=cw)

        def on_row(name):
            if self._var:
                self._var.set(name)
            self._render()
            self._close_popup()
            if self._on_select:
                self._on_select(name)

        cur = self._current_text()
        for it in self._items:
            name = it.get("name", "")
            brush = bool(it.get("brushable"))
            row = tk.Frame(inner, bg=ACCENT_SOFT if name == cur else CARD,
                           height=s(38), cursor="hand2")
            row.pack(fill="x", padx=s(8), pady=s(2))
            row.pack_propagate(False)
            tk.Label(row, text=name, bg=row.cget("bg"),
                     fg=ACCENT if name == cur else TEXT,
                     font=font(9), anchor="w").pack(side="left", padx=(s(10), s(4)))
            tag = "可刷" if brush else "无课"
            tag_bg = GREEN_SOFT if brush else "#F2F4F8"
            tag_fg = GREEN if brush else TEXT_MUT
            tag_lbl = tk.Label(row, text=tag, bg=tag_bg, fg=tag_fg, font=font(8, "bold"))
            tag_lbl.pack(side="right", padx=(s(4), s(10)))
            for wd in (row, tag_lbl):
                wd.bind("<Button-1>", lambda e, n=name: on_row(n))

        inner.update_idletasks()
        need_h = inner.winfo_reqheight() + s(52)
        pop_h = min(max_h, max(s(120), need_h))
        pop.geometry(f"{w}x{pop_h}+{x}+{y}")

        # 滚轮绑定
        def wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind_all("<MouseWheel>", wheel)
        pop.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))
        # 点外面关闭
        def outside(e):
            if pop.winfo_exists() and not (x <= e.x_root <= x + w and y <= e.y_root <= y + pop_h):
                self._close_popup()
        pop.bind("<Button-1>", outside, add="+")

        self._popup = pop
        pop.deiconify()
        pop.lift()
