"""「大模型设置」对话框。

为什么要有这个窗口：自动答题是**软件自己去调大模型**，而 Key 不能内置
（这个工具要发给别人，内置等于公开额度）。所以让每个使用者在自己的机器上填。

支持任意 OpenAI 兼容接口，例如：
    DeepSeek   https://api.deepseek.com/v1        deepseek-chat
    通义千问   https://dashscope.aliyuncs.com/compatible-mode/v1   qwen-plus
    Kimi       https://api.moonshot.cn/v1         moonshot-v1-8k
    豆包       https://ark.cn-beijing.volces.com/api/v3            你的接入点 ID
    OpenAI     https://api.openai.com/v1          gpt-4o-mini
"""
import tkinter as tk

from config import llm_config, save_settings
from core.llm import LLMClient
import ui.widgets as W


class LLMSettingsDialog:
    def __init__(self, root, on_saved=None):
        self.on_saved = on_saved
        self.top = tk.Toplevel(root)
        self.top.title("大模型设置 · 自动答题")
        self.top.configure(bg=W.BG)
        self.top.transient(root)
        self.top.grab_set()
        self.top.resizable(False, False)

        w, h = W.s(560), W.s(430)
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        self.top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        cfg = llm_config()
        p = W.s

        card = W.Card(self.top, height=h - p(28), pad=(p(20), p(18)))
        card.pack(fill="both", expand=True, padx=p(14), pady=p(14))

        tk.Label(card.body, text="大模型设置", bg=W.CARD, fg=W.TEXT,
                 font=W.font(12, "bold")).pack(anchor="w")
        tk.Label(card.body,
                 text="自动答题由软件直接调用你填的接口，Key 只保存在这台电脑上（明文）。",
                 bg=W.CARD, fg=W.TEXT_MUT, font=W.font(8), wraplength=p(470),
                 justify="left").pack(anchor="w", pady=(p(4), p(12)))

        self.e_base = self._field(card.body, "接口地址（OpenAI 兼容）", cfg.get("base", ""))
        self.e_model = self._field(card.body, "模型名", cfg.get("model", ""))
        self.e_key = self._field(card.body, "API Key", cfg.get("key", ""), secret=True)

        self.v_exam = tk.BooleanVar(value=bool((cfg or {}).get("include_exam", False)))
        self.v_submit = tk.BooleanVar(value=bool((cfg or {}).get("auto_submit", True)))
        row = tk.Frame(card.body, bg=W.CARD)
        row.pack(fill="x", pady=(p(8), 0))
        for text, var in (("包含考试（默认不选，考试限时且交卷不可改）", self.v_exam),
                          ("填完自动提交（不勾则停在作答页由你确认）", self.v_submit)):
            cb = tk.Checkbutton(row, text=text, variable=var, bg=W.CARD, fg=W.TEXT_SUB,
                                activebackground=W.CARD, selectcolor="#FFFFFF",
                                font=W.font(8), anchor="w", bd=0, highlightthickness=0)
            cb.pack(anchor="w")

        self.msg = tk.Label(card.body, text="", bg=W.CARD, fg=W.TEXT_MUT,
                            font=W.font(8), wraplength=p(470), justify="left")
        self.msg.pack(anchor="w", pady=(p(8), 0))

        btns = tk.Frame(card.body, bg=W.CARD)
        btns.pack(fill="x", pady=(p(10), 0))
        W.NavButton(btns, text="保存", kind="primary", command=self.save,
                    height=34, width=p(86)).pack(side="right")
        W.NavButton(btns, text="测试连接", command=self.test,
                    height=34, width=p(96)).pack(side="right", padx=(0, p(8)))
        W.NavButton(btns, text="取消", kind="ghost", command=self.close,
                    height=34, width=p(70)).pack(side="right", padx=(0, p(8)))

    # ---------- 组件 ----------
    def _field(self, parent, label, value, secret=False):
        p = W.s
        tk.Label(parent, text=label, bg=W.CARD, fg=W.TEXT_SUB,
                 font=W.font(8)).pack(anchor="w", pady=(p(6), p(2)))
        box = W.RoundedCanvas(parent, radius=8, fill="#F7F8FC", outline="#E3E8F2",
                              line_width=1, pad=(10, 6), height=36)
        box.pack(fill="x")
        e = tk.Entry(box.body, bg="#F7F8FC", fg=W.TEXT, relief="flat", bd=0,
                     font=W.font(9), insertbackground=W.TEXT,
                     show="*" if secret else "")
        e.insert(0, value or "")
        e.pack(fill="both", expand=True)
        return e

    # ---------- 行为 ----------
    def _collect(self):
        cfg = llm_config()
        cfg.update({
            "base": self.e_base.get().strip(),
            "model": self.e_model.get().strip(),
            "key": self.e_key.get().strip(),
            "include_exam": bool(self.v_exam.get()),
            "auto_submit": bool(self.v_submit.get()),
        })
        return cfg

    def test(self):
        cfg = self._collect()
        self.msg.config(text="正在连接…", fg=W.TEXT_MUT)
        self.top.update_idletasks()
        try:
            reply = LLMClient(cfg["base"], cfg["key"], cfg["model"],
                              cfg.get("timeout", 90)).test()
            self.msg.config(text=f"连接成功：{reply.strip()[:60]}", fg="#1A7F4B")
        except Exception as e:
            self.msg.config(text=f"失败：{e}", fg="#C0392B")

    def save(self):
        cfg = self._collect()
        if not (cfg["base"] and cfg["model"] and cfg["key"]):
            self.msg.config(text="接口地址 / 模型名 / Key 三项都要填", fg="#C0392B")
            return
        if save_settings({"llm": cfg}):
            if self.on_saved:
                try:
                    self.on_saved(cfg)
                except Exception:
                    pass
            self.close()
        else:
            self.msg.config(text="写入失败：数据目录不可写", fg="#C0392B")

    def close(self):
        self.top.grab_release()
        self.top.destroy()
