"""真机验证手机远控：从桌面弹窗读出地址与配对码，用无头浏览器模拟手机操作。

不是跑测试替身 —— 直接连到 StudyBuddy 界面上已经启动的服务，
验证登录、状态读取、按钮下发这条链路在真实进程间是通的。
"""
import json
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright

LOG = os.path.join(BASE, "shots", "uia_log.txt")


def read_address():
    text = open(LOG, encoding="utf-8").read()
    m = re.findall(r"手机远控已启动: (http://[^\s]+)", text)
    return m[-1] if m else None


def main():
    address = read_address()
    if not address:
        address = sys.argv[2] if len(sys.argv) > 2 else None
    if not address:
        print("NO_ADDRESS")
        return 1
    print("address =", address)
    # 从界面截图上人工读到的配对码（每次启动都换，验证时以参数传入）
    code = sys.argv[1] if len(sys.argv) > 1 else ""
    if not code:
        print("NO_CODE")
        return 2

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True,
                                executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        pg = br.new_page(viewport={"width": 390, "height": 844})
        pg.goto(address, wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(1200)
        print("title =", pg.title())
        print("login visible =", pg.locator("#login").is_visible())
        # 自动填码已由 URL hash 完成；若没有则手动填入
        if pg.locator("#login").is_visible():
            pg.fill("#code", code)
            pg.click("#login button")
            pg.wait_for_timeout(1200)
        print("after login visible =", pg.locator("#login").is_visible())
        pg.screenshot(path=os.path.join(BASE, "shots", "live_phone.png"), full_page=True)

        # 读页面上的状态与课程
        print("state text =", pg.locator("body").inner_text()[:400].replace("\n", " | "))

        # 点一个只读按钮：读学时（不启动刷课，避免真机任务被打断）
        try:
            with pg.expect_response(lambda r: "/api/action" in r.url, timeout=12000):
                pg.get_by_text("读学时", exact=False).first.click()
            print("clicked 读学时, action sent")
        except Exception as e:
            print("click action failed:", str(e)[:200])
        pg.wait_for_timeout(1800)
        pg.screenshot(path=os.path.join(BASE, "shots", "live_phone_after.png"), full_page=True)
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
