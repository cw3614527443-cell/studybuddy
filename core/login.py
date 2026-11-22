"""账号密码登录（AI 优课 / zjy2 / 学习通）。

为什么需要
----------
扫码登录要求账号本人的手机在手边。替别人刷课时，账号主通常不在旁边，
所以必须支持「网页账号 + 密码」登录。

真实约束（2026-11 实测，别以为是程序没写好）
--------------------------------------------
两个平台在提交账号密码后都会弹滑块拼图验证：

- 智慧职教统一认证 sso.icve.com.cn：腾讯 / 阿里两套滑块；
- 学习通 v8.chaoxing.com：超星 u-captcha 滑块。

滑块是反自动化手段，脚本绕过既不可靠也不应该做。因此这里的流程是：

1. 自动点开「账号密码登录」标签、填账号密码、勾协议、提交；
2. 一旦出现滑块，写一条明确日志并提示「请在浏览器窗口里拖一下」，
   程序继续轮询登录结果（默认最多等 3 分钟）；
3. 登录成功后由浏览器 profile 持久化，之后几个月都不必再登录。

也就是说：操作的人只要在电脑前拖一次滑块，不需要账号主的手机。
"""
import re
import time

# 智慧职教统一认证的账号密码表单
ICVE_SELECTORS = {
    "account": 'input[placeholder="请输入账号"]',
    "password": 'input[placeholder="请输入密码"]',
    "agree_box": '.agreement input[type="checkbox"]',
    "agree_click": '.agreement .el-checkbox',
    "submit": '.el-form.demo-ruleForm .login',
    "tab_text": "账号密码登录",
}

# 出现这些就说明被拦在滑块上
CAPTCHA_SELECTORS = (
    ".tencent-captcha-dy__content",
    ".tencent-captcha-dy__warp",
    ".aliyunCaptcha-top",
    ".aliyun-captcha",
    ".u-captcha",
    ".cx_slider_tip",
)

CAPTCHA_HINTS = ("拖动滑块", "请完成安全验证", "完成拼图", "拖动下方拼图")

ERROR_PATTERNS = (
    r"账号或密码(错误|不正确)",
    r"密码(错误|不正确)",
    r"账号(不存在|未注册)",
    r"用户(不存在|名或密码)",
    r"手机号(未注册|不存在)",
    r"验证码(错误|不正确|已失效)",
)

LOGIN_HINTS = ("欢迎登录", "请扫码", "账号密码登录")


def platform_label(platform):
    return {"ai": "AI 优课", "zjy2": "zjy2", "cx": "学习通"}.get(platform, platform)


# ---------------------------------------------------------------------------
# 页面判定
# ---------------------------------------------------------------------------
def is_login_url(url):
    """当前 URL 是否还停在登录/认证页。"""
    u = (url or "").lower()
    return ("sso.icve.com.cn" in u or "auth_v2" in u or "/login" in u
            or "passport2" in u or "v8.chaoxing.com" in u)


def _visible(page, selector):
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        return bool(el.is_visible())
    except Exception:
        return False


def has_captcha(page):
    for sel in CAPTCHA_SELECTORS:
        if _visible(page, sel):
            return True
    try:
        body = page.inner_text("body") or ""
    except Exception:
        return False
    return any(h in body for h in CAPTCHA_HINTS)


def error_message(page):
    try:
        body = page.inner_text("body") or ""
    except Exception:
        return ""
    for pat in ERROR_PATTERNS:
        m = re.search(pat, body)
        if m:
            return m.group(0)
    return ""


def _type_into(page, selector, value, log=None):
    """填一个输入框。

    这些页面是 Vue/React 受控输入，直接改 value 不会触发校验逻辑，
    所以优先用 fill（会派发 input 事件）；填完再核对一次，
    万一框架把值清掉了，退回「点进去逐字输入」这种最笨但有效的办法。
    """
    page.click(selector)
    try:
        page.fill(selector, value)
    except Exception:
        pass
    try:
        got = page.input_value(selector)
    except Exception:
        got = ""
    if got != value:
        page.click(selector)
        try:
            page.fill(selector, "")
        except Exception:
            pass
        page.type(selector, value, delay=20)
        try:
            got = page.input_value(selector)
        except Exception:
            got = value
    if got != value and log:
        log("账号/密码没填进输入框（页面可能改版），可改为手动登录", "warn")
    return got == value


# ---------------------------------------------------------------------------
# 智慧职教（AI 优课 / zjy2 共用统一认证）
# ---------------------------------------------------------------------------
def login_icve(page, user, password, log=None, wait_seconds=180,
               should_stop=None, shot=None):
    """在 sso.icve.com.cn 上完成账号密码登录。

    返回 {"ok": bool, "reason": str}；被用户停止时 reason="stopped"。
    """
    def emit(msg, level="info"):
        if log:
            log(msg, level)

    if not is_login_url(page.url):
        emit(f"当前没有停在登录页（{(page.url or '')[:70]}），不需要再登录")
        return {"ok": True, "reason": "already"}

    # 1) 切到「账号密码登录」标签。页面默认停在扫码页，且会异步切回，
    #    所以点完要确认输入框真的出现了。
    for _ in range(3):
        if _visible(page, ICVE_SELECTORS["account"]):
            break
        try:
            page.click(f'text="{ICVE_SELECTORS["tab_text"]}"', timeout=8000)
        except Exception:
            try:
                page.get_by_text(ICVE_SELECTORS["tab_text"], exact=True).first.click(timeout=8000)
            except Exception:
                pass
        page.wait_for_timeout(1200)
    if not _visible(page, ICVE_SELECTORS["account"]):
        return {"ok": False, "reason": "找不到账号密码登录表单（页面可能改版）"}

    # 2) 填表
    if not _type_into(page, ICVE_SELECTORS["account"], user, log):
        return {"ok": False, "reason": "账号没填进输入框"}
    if not _type_into(page, ICVE_SELECTORS["password"], password, log):
        return {"ok": False, "reason": "密码没填进输入框"}

    # 3) 勾选「我已阅读并同意」（不勾提交没反应）
    try:
        box = page.query_selector(ICVE_SELECTORS["agree_box"])
        if box is not None and not box.is_checked():
            page.click(ICVE_SELECTORS["agree_click"], timeout=5000)
            emit("已勾选用户协议")
    except Exception:
        pass

    # 4) 提交
    try:
        page.click(ICVE_SELECTORS["submit"], timeout=10000)
        emit("已提交账号密码，等待平台响应…")
    except Exception as e:
        return {"ok": False, "reason": f"点不动登录按钮：{e}"}

    # 5) 等结果
    return _wait_result(page, log=log, wait_seconds=wait_seconds,
                        should_stop=should_stop, shot=shot)


def _wait_result(page, log=None, wait_seconds=180, should_stop=None, shot=None):
    """轮询登录结果；出现滑块时提示一次并继续等用户拖动。"""
    def emit(msg, level="info"):
        if log:
            log(msg, level)

    t0 = time.time()
    captcha_told = False
    while time.time() - t0 < wait_seconds:
        if should_stop and should_stop():
            return {"ok": False, "reason": "stopped"}
        try:
            url = page.url
        except Exception:
            return {"ok": False, "reason": "浏览器窗口已关闭"}

        if not is_login_url(url):
            page.wait_for_timeout(1500)
            emit(f"登录成功，已进入：{url}", "ok")
            return {"ok": True, "reason": "ok"}

        if not captcha_told and has_captcha(page):
            captcha_told = True
            emit("平台要求滑块验证：请在弹出的浏览器窗口里向右拖动滑块完成拼图，"
                 "拖完不用管，程序会自动继续（最多等 %d 秒）" % wait_seconds, "warn")
            if shot:
                try:
                    shot()
                except Exception:
                    pass

        err = error_message(page)
        if err and not has_captcha(page):
            return {"ok": False, "reason": err}

        page.wait_for_timeout(1200)

    return {"ok": False, "reason": "等待登录超时（%d 秒）" % wait_seconds}


# ---------------------------------------------------------------------------
# 学习通：登录动作在 chaoxing/run.py:cmd_login_password 里（走套件驱动），
# 这里只保留滑块文案用于提示，避免两处各写一套判定。
# ---------------------------------------------------------------------------
CX_CAPTCHA_HINTS = ("滑块", "安全验证", "拼图")
