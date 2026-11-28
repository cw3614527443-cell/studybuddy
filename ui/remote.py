"""「手机远控」窗口：把地址、配对码、二维码摆出来，并控制服务开关。

用户视角只有三步：点开这个窗口 → 手机扫二维码（或手输地址）→ 输入 6 位配对码。
所以窗口里必须一眼看到这三样，其余（端口占用、防火墙、是否同一网段）都收进提示区。

安全相关的取舍：
- 二维码里带 `#c=配对码`，手机打开会自动填进输入框，但**不自动登录** ——
  自动登录等于「拿到链接就能用」，一旦截图外传就失守；预填只省一次手输。
- 「换一个配对码」会让所有已连接的手机立刻掉线，用于怀疑被人连上时自救。
"""
import tkinter as tk

import ui.widgets as W


def _qr_photo(text, px):
    """生成二维码图；没装 qrcode 库就返回 None，由调用方降级成纯文字地址。"""
    try:
        import qrcode
        from PIL import ImageTk
        qr = qrcode.QRCode(box_size=6, border=2,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        im = qr.make_image(fill_color="#111827", back_color="#FFFFFF").convert("RGB")
        im = im.resize((px, px))
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


class RemoteDialog:
    def __init__(self, root, hub, server):
        self.root = root
        self.hub = hub
        self.server = server
        self.p = W.s
        self._qrimg = None

        self.top = tk.Toplevel(root)
        self.top.title("手机远控")
        self.top.configure(bg=W.BG)
        self.top.transient(root)
        self.top.resizable(False, False)

        w, h = self.p(560), self.p(512)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(w, int(sw * 0.92)), min(h, int(sh * 0.90))
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        self.top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        card = W.Card(self.top, height=h - self.p(24), pad=(self.p(20), self.p(16)))
        card.pack(fill="both", expand=True, padx=self.p(12), pady=self.p(12))
        body = card.body

        self._build_head(body)
        self._build_main(body)
        self._build_tips(body)
        self._sync()

        self.top.bind("<Escape>", lambda e: self.close())
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    # ---------------------------------------------------------------- 顶部
    def _build_head(self, parent):
        p = self.p
        head = tk.Frame(parent, bg=W.CARD)
        head.pack(fill="x")
        tk.Label(head, text="手机远控", bg=W.CARD, fg=W.TEXT,
                 font=W.font(13, "bold")).pack(side="left")
        self.chip = tk.Label(head, text="", bg=W.TEXT_MUT, fg="#FFFFFF",
                             font=W.font(8, "bold"), padx=p(8), pady=p(2))
        self.chip.pack(side="left", padx=(p(8), 0))
        self.state_lbl = tk.Label(head, text="", bg=W.CARD, fg=W.TEXT_SUB,
                                  font=W.font(8))
        self.state_lbl.pack(side="right")

    # ---------------------------------------------------------------- 主体
    def _build_main(self, parent):
        p = self.p
        row = tk.Frame(parent, bg=W.CARD)
        row.pack(fill="x", pady=(p(12), 0))

        # 左：二维码
        left = tk.Frame(row, bg=W.CARD, width=p(196), height=p(196))
        left.pack(side="left", anchor="n")
        left.pack_propagate(False)
        self.qr_holder = tk.Label(left, bg=W.CARD, bd=0)
        self.qr_holder.pack()

        # 右：地址 + 配对码 + 开关
        right = tk.Frame(row, bg=W.CARD)
        right.pack(side="left", fill="both", expand=True, padx=(p(16), 0))

        tk.Label(right, text="手机浏览器打开这个地址", bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(8)).pack(anchor="w")
        self.url_lbl = tk.Label(right, text="—", bg=W.ACCENT_SOFT, fg=W.ACCENT,
                                font=W.font(10, "bold"), padx=p(8), pady=p(5),
                                anchor="w", cursor="hand2")
        self.url_lbl.pack(fill="x", pady=(p(4), 0))
        self.url_lbl.bind("<Button-1>", lambda e: self._copy())

        tk.Label(right, text="6 位配对码", bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(8)).pack(anchor="w", pady=(p(12), 0))
        code_row = tk.Frame(right, bg=W.CARD)
        code_row.pack(fill="x", pady=(p(4), 0))
        self.code_lbl = tk.Label(code_row, text="------", bg=W.BG, fg=W.TEXT,
                                 font=("Consolas", 17, "bold"), padx=p(10), pady=p(4))
        self.code_lbl.pack(side="left")
        W.NavButton(code_row, text="换一个", kind="link", height=26,
                    command=self._rotate).pack(side="left", padx=(p(8), 0))

        self.btn_toggle = W.NavButton(right, text="启动服务", kind="primary",
                                      height=36, command=self._toggle)
        self.btn_toggle.pack(fill="x", pady=(p(14), 0))
        W.NavButton(right, text="复制地址", kind="link", height=26,
                    command=self._copy).pack(anchor="w", pady=(p(4), 0))

    # ---------------------------------------------------------------- 提示
    def _build_tips(self, parent):
        p = self.p
        box = tk.Frame(parent, bg=W.ACCENT_SOFT)
        box.pack(fill="x", pady=(p(14), 0))
        tips = [
            "首次启动会弹 Windows 防火墙询问，请点「允许」（只勾专用网络就够），"
            "否则手机连不上。",
            "手机和电脑要在同一个 WiFi（或同一个 Tailscale 网段）下。",
            "手机打开后点浏览器菜单里的「添加到主屏幕」，以后就像 App 一样一点即开。",
            "配对码每次启动都会换；点「换一个」会让已连上的手机立刻掉线。",
            "服务只在本机监听，不上传任何数据；不要把这个端口映射到公网。",
        ]
        for i, t in enumerate(tips):
            tk.Label(box, text="· " + t, bg=W.ACCENT_SOFT, fg=W.TEXT_SUB,
                     font=W.font(8), justify="left", anchor="w",
                     wraplength=p(470)).pack(fill="x", padx=p(10),
                                             pady=(p(6) if i == 0 else p(2),
                                                   p(6) if i == len(tips) - 1 else 0))

    # ---------------------------------------------------------------- 行为
    def _url(self):
        code = self.hub.code if self.server.running else ""
        base = "http://%s:%d" % (self.hub.snapshot()["ip"], self.server.port)
        return base + ("/#c=" + code if code else ""), base

    def _toggle(self):
        if self.server.running:
            self.server.stop()
            self.state_lbl.config(text="服务已停止，手机将无法连接", fg=W.TEXT_MUT)
        else:
            ok = self.server.start()
            if not ok:
                self.state_lbl.config(text=self.server.error or "启动失败", fg=W.RED)
            else:
                self.state_lbl.config(text="服务运行中", fg=W.GREEN)
        self._sync()

    def _rotate(self):
        self.hub.rotate_code()
        self._sync()

    def _copy(self):
        _, base = self._url()
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(base)
            self.state_lbl.config(text="地址已复制", fg=W.GREEN)
        except Exception:
            pass

    def _sync(self):
        running = self.server.running
        full, base = self._url()
        self.chip.config(text="运行中" if running else "未启动",
                         bg=W.GREEN if running else W.TEXT_MUT)
        self.url_lbl.config(text=base if running else "先点下面的「启动服务」")
        self.code_lbl.config(text=self.hub.code if running else "------")
        self.btn_toggle.config(text="停止服务" if running else "启动服务")
        if running:
            self._qrimg = _qr_photo(full, self.p(190))
            if self._qrimg is not None:
                self.qr_holder.config(image=self._qrimg, text="")
            else:
                self.qr_holder.config(image="", text="（未安装二维码组件，\n请手输上面地址）",
                                      fg=W.TEXT_MUT, font=W.font(8),
                                      justify="center", width=22, height=8)
        else:
            self.qr_holder.config(image="", text="服务未启动", fg=W.TEXT_MUT,
                                  font=W.font(9), width=22, height=8)

    def close(self):
        """关窗口不停服务：用户可能只是想收起窗口继续用手机操作。"""
        try:
            self.top.destroy()
        except Exception:
            pass
