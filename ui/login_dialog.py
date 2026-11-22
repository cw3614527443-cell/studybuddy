"""登录方式弹窗。

两种登录方式：

- 账号密码：程序自动填表提交，登录态由浏览器 profile 记住，下次不用再登。
  平台会弹一次滑块，需要人在浏览器窗口里拖一下（脚本绕不过，也不该绕）。
- 扫码 / 手动：保持原来的行为，在弹出的窗口里用手机扫码，或自己输账号。

设计取舍：不把「账号密码」做成默认无脑执行，因为替别人刷课时账号密码是敏感信息；
弹窗里把「记住账号密码」的后果写清楚（明文存在本机），由使用者决定要不要勾。
"""
import tkinter as tk

import ui.widgets as W
from config import load_account


PLATFORM_NAMES = {"ai": "AI 优课", "zjy2": "zjy2", "cx": "学习通"}


class LoginDialog:
    """登录方式选择弹窗。

    用法：
        LoginDialog(root, "ai", on_submit=cb, on_manual=cb2).show()

    on_submit({"mode": "password", "user": ..., "pwd": ..., "remember": bool})
    on_submit({"mode": "manual"})
    """

    def __init__(self, master, platform, on_submit=None):
        self.master = master
        self.platform = platform
        self.on_submit = on_submit
        self.name = PLATFORM_NAMES.get(platform, platform)
        self._saved = load_account(platform)
        self._mode = "password" if self._saved.get("user") else "manual"
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        p = W.s
        top = tk.Toplevel(self.master)
        self.top = top
        top.title(f"登录 · {self.name}")
        top.configure(bg=W.BG)
        top.resizable(False, False)
        top.transient(self.master)

        body = tk.Frame(top, bg=W.BG)
        body.pack(fill="both", expand=True, padx=p(24), pady=(p(20), p(18)))

        tk.Label(body, text=f"登录{self.name}", bg=W.BG, fg=W.TEXT,
                 font=W.font(14, "bold")).pack(anchor="w")
        tk.Label(body, text="登录一次即记住，之后打开就能直接用",
                 bg=W.BG, fg=W.TEXT_MUT, font=W.font(9)).pack(anchor="w", pady=(p(3), p(12)))

        self.seg = W.Segmented(
            body, options=[("password", "账号密码"), ("manual", "扫码 / 手动")],
            value=self._mode, command=self._on_mode, width=340, height=36)
        self.seg.pack(anchor="w")

        # 两个面板共用这个容器：pack_forget 后再 pack 会跑到父容器末尾，
        # 直接挂在固定容器里就不会把按钮挤到上面去。
        self.panel_box = tk.Frame(body, bg=W.BG)
        self.panel_box.pack(fill="x", pady=(p(12), 0))

        # ---- 账号密码面板 ----
        self.pwd_panel = W.Card(self.panel_box, height=232, pad=(16, 14))
        pw = self.pwd_panel.body

        self.user_var = tk.StringVar(value=self._saved.get("user", ""))
        self.pwd_var = tk.StringVar(value=self._saved.get("pwd", ""))
        self.remember_var = tk.BooleanVar(value=bool(self._saved.get("user")))

        tk.Label(pw, text="账号（手机号 / 用户名）", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w")
        self.user_entry = W.RoundEntry(pw, textvariable=self.user_var, height=38,
                              field_bg="#F8F9FD")
        self.user_entry.pack(fill="x", pady=(p(4), p(8)))

        tk.Label(pw, text="密码", bg=W.CARD, fg=W.TEXT_MUT,
                 font=W.font(8)).pack(anchor="w")
        self.pwd_entry = W.RoundEntry(pw, textvariable=self.pwd_var, height=38,
                              field_bg="#F8F9FD")
        self.pwd_entry.pack(fill="x", pady=(p(4), p(8)))
        self.pwd_entry.entry.config(show="●")

        row = tk.Frame(pw, bg=W.CARD)
        row.pack(fill="x")
        chk = tk.Checkbutton(
            row, text="记住账号密码", variable=self.remember_var, bg=W.CARD,
            fg=W.TEXT_SUB, activebackground=W.CARD, activeforeground=W.TEXT,
            selectcolor=W.CARD, bd=0, highlightthickness=0, font=W.font(8),
            cursor="hand2")
        chk.pack(side="left")
        W.NavButton(row, text="清除已记账号", kind="link", height=20,
                    command=self._forget).pack(side="right")

        self.hint = tk.Label(
            pw, text="首次登录平台会弹滑块，请在弹出的浏览器窗口里拖一下；拖完程序自动继续。\n"
                     "勾选「记住」时密码明文存在本机设置文件里，公用电脑请不要勾。",
            bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8), justify="left",
            wraplength=p(320))
        self.hint.pack(anchor="w", pady=(p(8), 0))

        # ---- 手动画板 ----
        self.manual_panel = W.Card(self.panel_box, height=88, pad=(16, 14))
        self.manual_text = tk.Label(
            self.manual_panel.body,
            text="点下面的按钮后，在弹出的浏览器窗口里用手机扫码，\n"
                 "或用账号密码自己登录。登录态同样会保留。",
            bg=W.CARD, fg=W.TEXT_SUB, font=W.font(9), justify="left",
            wraplength=p(330))
        self.manual_text.pack(anchor="w")

        self.err = tk.Label(body, text="", bg=W.BG, fg=W.RED, font=W.font(9),
                            justify="left", wraplength=p(340))
        self.err.pack(anchor="w", pady=(p(10), 0))

        foot = tk.Frame(body, bg=W.BG)
        foot.pack(fill="x", pady=(p(6), 0))
        self.btn_ok = W.NavButton(foot, text="登录并打开", kind="primary",
                                  command=self._submit, height=40, width=150)
        self.btn_ok.pack(side="right")
        W.NavButton(foot, text="取消", kind="ghost", command=self.close,
                    height=40, width=84).pack(side="right", padx=(0, p(8)))

        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Return>", lambda e: self._submit())
        self._apply_mode()

    # ---------------------------------------------------------------- 行为
    def _on_mode(self, key):
        self._mode = key
        self._apply_mode()

    def _apply_mode(self):
        if self._mode == "password":
            self.manual_panel.pack_forget()
            self.pwd_panel.pack(fill="x")
            self.btn_ok.config(text="登录并打开")
            self._fit(self.pwd_panel)
        else:
            self.pwd_panel.pack_forget()
            self.manual_panel.pack(fill="x")
            self.btn_ok.config(text="打开页面")
            self._fit(self.manual_panel)
        self.err.config(text="")
        self._resize()

    @staticmethod
    def _fit(card):
        """按内容撑开卡片高度。

        卡片是固定高度的画布，内容在不同 DPI / 字号下高度不一样；
        写死高度不是文字被切就是留一大片空白，所以按内容量一次。
        """
        try:
            card.update_idletasks()
            pad = int(W.s(14))
            need = card.body.winfo_reqheight() + pad * 2 + 6
            card.configure(height=max(need, int(W.s(72))))
            card.refresh()
        except Exception:
            pass

    def _resize(self):
        try:
            self.top.update_idletasks()
            w = self.top.winfo_reqwidth()
            h = self.top.winfo_reqheight()
            self.top.geometry(f"{w}x{h}")
            self.top.minsize(w, h)
            m = self.master
            if m.winfo_viewable():
                x = m.winfo_rootx() + max(0, (m.winfo_width() - w) // 2)
                y = m.winfo_rooty() + max(0, (m.winfo_height() - h) // 3)
                self.top.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _forget(self):
        self.user_var.set("")
        self.pwd_var.set("")
        self.remember_var.set(False)
        self.err.config(text="已清空，本次不会保存账号")

    def _submit(self):
        if self._mode != "password":
            self._close_and({"mode": "manual"})
            return
        user = self.user_var.get().strip()
        pwd = self.pwd_var.get()
        if not user:
            self.err.config(text="请填账号（手机号或平台用户名）")
            return
        if not pwd:
            self.err.config(text="请填密码")
            return
        self._close_and({"mode": "password", "user": user, "pwd": pwd,
                         "remember": bool(self.remember_var.get())})

    def _close_and(self, payload):
        cb = self.on_submit
        self.close()
        if cb:
            cb(payload)

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass

    # --------------------------------------------------------------------
    def show(self):
        self.top.grab_set()
        self.top.focus_force()
        return self
