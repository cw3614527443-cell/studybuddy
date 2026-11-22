"""账号密码登录（v1.7.0）离线回归测试。

背景：扫码要求账号主手机在手边，替别人刷课时不现实，所以加了账号密码登录。
真实平台提交后会弹滑块，脚本无法绕过——这里用仿造页面把「能自动做的部分」
和「必须人工的部分」都钉住，避免以后改坏了没人发现：

1. 能自动切到账号密码 Tab、填账号密码、勾协议、提交；
2. 不勾协议时点登录不生效（说明我们确实勾了，而不是碰巧成功）；
3. 出现滑块时提示要人工拖，并且继续等待，拖完能正确判成功；
4. 密码错误能立刻报出来，不会傻等 3 分钟；
5. 已登录状态不重复登录；
6. 记住账号 / 不记住账号的落盘行为。

跑法：python tests/test_login.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.login import (is_login_url, has_captcha, error_message,  # noqa: E402
                        login_icve, _wait_result)

ROOT = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(ROOT, "fixtures")
LOGIN_HTML = os.path.join(FIX, "login.html")

ok_count = 0
bad = []


def check(name, cond, extra=""):
    global ok_count
    if cond:
        ok_count += 1
        print("  ok   " + name)
    else:
        bad.append(name)
        print("  FAIL " + name + ((" :: " + str(extra)) if extra else ""))
    return cond


def url_of(name):
    return "file:///" + os.path.join(FIX, name).replace("\\", "/")


def open_page(page):
    page.goto(url_of("login.html"))
    page.wait_for_selector('input[placeholder="请输入账号"]')


def test_pure():
    print("\n== 纯函数判定 ==")
    check("sso 认证页判为登录页", is_login_url("https://sso.icve.com.cn/sso/auth_v2?x=1"))
    check("学习通 v8 判为登录页", is_login_url("https://v8.chaoxing.com/"))
    check("登录后工作页不算登录页", not is_login_url("https://ai.icve.com.cn/app/my-excellent-home"))
    check("zjy2 课程页不算登录页", not is_login_url("https://zjy2.icve.com.cn/study/v2/course"))


def test_basic_login(page):
    print("\n== 正常登录（自动填表 + 勾协议 + 提交） ==")
    logs = []
    open_page(page)
    page.evaluate("window.NEED_CAPTCHA = false")
    res = login_icve(page, "13800138000", "good-pass", log=lambda m, l="info": logs.append(m),
                     wait_seconds=15)
    check("登录成功", res.get("ok") is True, res)
    check("跳到了工作页", "home.html" in page.url, page.url)
    check("确实勾了用户协议（否则页面会拦）",
          any("协议" in x for x in logs), logs)


def test_captcha_wait(page):
    print("\n== 出现滑块：提示人工拖，并继续等到成功 ==")
    logs = []
    open_page(page)
    page.evaluate("window.NEED_CAPTCHA = true")
    res = login_icve(page, "13800138000", "good-pass", log=lambda m, l="info": logs.append(m),
                     wait_seconds=20)
    check("拖完滑块后判成功", res.get("ok") is True, res)
    check("出现过滑块提示", any("滑块" in x for x in logs), logs)
    check("提示里说明拖完会自动继续", any("自动" in x for x in logs), logs)


def test_wrong_password(page):
    print("\n== 密码错误：立即报错，不空等 ==")
    logs = []
    open_page(page)
    page.evaluate("window.NEED_CAPTCHA = false")
    t0 = __import__("time").time()
    res = login_icve(page, "13800138000", "bad", log=lambda m, l="info": logs.append(m),
                     wait_seconds=60)
    cost = __import__("time").time() - t0
    check("判为失败", res.get("ok") is False, res)
    check("原因里带「密码错误」", "密码错误" in (res.get("reason") or ""), res)
    check("没有等满 60 秒（%.1fs）" % cost, cost < 12, cost)


def test_need_manual_agree(page):
    print("\n== 不勾协议时页面确实会拦（证明上面的成功不是碰巧） ==")
    open_page(page)
    page.click('.el-form.demo-ruleForm .login')
    page.wait_for_timeout(600)
    check("页面给出「先同意协议」提示",
          "协议" in (page.inner_text("#msg") or ""), page.inner_text("#msg"))
    check("没有跳走", "login.html" in page.url, page.url)


def test_already_logged_in(page):
    print("\n== 已是登录态时不做多余动作 ==")
    page.goto(url_of("home.html"))
    res = login_icve(page, "u", "p", wait_seconds=5)
    check("直接返回 ok(already)", res.get("ok") is True and res.get("reason") == "already", res)


def test_detectors(page):
    print("\n== 滑块 / 错误文案识别 ==")
    page.goto("about:blank")
    page.set_content("<html><body><div class='u-captcha'>请完成安全验证</div></body></html>")
    check("识别 .u-captcha 滑块", has_captcha(page))
    page.set_content("<html><body><div>账号或密码不正确，请重试</div></body></html>")
    check("识别密码错误文案", "密码" in error_message(page), error_message(page))
    page.set_content("<html><body><div>欢迎登录</div></body></html>")
    check("普通页面不误判为滑块", not has_captcha(page))
    check("普通页面不误判为错误", error_message(page) == "", error_message(page))


def test_remember_account():
    print("\n== 记住账号的落盘行为 ==")
    import config
    tmp = tempfile.mkdtemp(prefix="sb_cfg_")
    old = config.SETTINGS_PATH
    try:
        config.SETTINGS_PATH = os.path.join(tmp, "settings.json")
        config.save_account("ai", "13800138000", "p@ss", remember=True)
        got = config.load_account("ai")
        check("记住后能读回账号", got.get("user") == "13800138000", got)
        check("记住后能读回密码", got.get("pwd") == "p@ss", got)

        cfg = config.load_settings()
        cfg["llm"]["model"] = "x-model"
        config.save_settings(cfg)
        check("改写大模型设置不会丢账号",
              config.load_account("ai").get("user") == "13800138000")

        config.save_account("ai", "13800138000", "p@ss", remember=False)
        check("不记住时确实清掉了", config.load_account("ai") == {}, config.load_account("ai"))

        config.save_account("cx", "u2", "p2", remember=True)
        check("按平台分开存", config.load_account("cx").get("user") == "u2"
              and config.load_account("ai") == {})
    finally:
        config.SETTINGS_PATH = old


def main():
    test_pure()
    test_remember_account()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="msedge", headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            test_basic_login(page)
            test_captcha_wait(page)
            test_wrong_password(page)
            test_need_manual_agree(page)
            test_already_logged_in(page)
            test_detectors(page)
        finally:
            browser.close()

    print("\n通过 %d 项，失败 %d 项" % (ok_count, len(bad)))
    if bad:
        print("失败：", bad)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
