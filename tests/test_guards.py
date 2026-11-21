"""页面内看门狗回归测试（v1.2.3）。

背景：用户实测「刷课时有声音」「续播弹窗没人关」。
原来的实现只在 Python 的某几个步骤里 set 一次 `muted`、查一次弹窗，
而平台随时会把 muted 改回 false、弹窗也可能在两次检查之间冒出来。

现在改成往页面里注入常驻脚本（core/browser.py::_GUARD_JS），0.5 秒一轮：
- 强制静音（含新插入的 player、播放前的抢静音、平台改回来再静音）；
- 识别「上次播放 / 是否继续」这类浮层，自动点「继续播放」或「重新开始」；
- 只碰这一类对话框，普通提示框（确定/公告）绝不能误点。

跑法：python tests/test_guards.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.browser import Browser, install_guard  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
  body{margin:0;font:14px/1.5 sans-serif}
  video{width:360px;height:200px;background:#000}
  .dlg{position:fixed;background:#fff;border:1px solid #ccc;padding:10px;z-index:999}
  #resume{left:38%;top:26%;width:340px}
  #notice{left:42%;top:62%;width:280px;z-index:998}
</style></head><body>
<video id="v1"></video>

<div id="resume" class="dlg">
  <div>提示</div>
  <div>检测到上次播放至8分21秒，是否继续？</div>
  <button id="btn-restart">重新开始</button>
  <button id="btn-resume">继续播放</button>
</div>

<div id="notice" class="dlg">
  <div>系统公告</div>
  <div>今晚 23:00 维护，请提前保存</div>
  <button id="btn-ok">确定</button>
</div>

<script>
  window.__clicks = [];
  for (const id of ['btn-restart', 'btn-resume', 'btn-ok']) {
    document.getElementById(id).addEventListener('click', () => {
      window.__clicks.push(id);
      if (id !== 'btn-ok') document.getElementById('resume').style.display = 'none';
    });
  }
</script>
</body></html>"""


class StubBus:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def should_stop(self):
        return False


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "sb_fixture_guard.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HTML)
    url = "file:///" + tmp.replace("\\", "/")

    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=EDGE if os.path.exists(EDGE) else None, headless=True
        )

        # ---------- 1. 刷课模式（优先「继续播放」） ----------
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto(url)
        page.wait_for_timeout(200)
        ok &= check("装看门狗前视频没被静音", page.evaluate("() => document.getElementById('v1').muted") is False)

        install_guard(page, prefer_resume=True)
        page.wait_for_timeout(1500)

        ok &= check("已有播放器立刻被静音",
                    page.evaluate("() => document.getElementById('v1').muted") is True)
        ok &= check("续播弹窗被自动点掉（继续播放）",
                    page.evaluate("() => window.__clicks") == ["btn-resume"],
                    str(page.evaluate("() => window.__clicks")))
        ok &= check("计数里记下了这一次", page.evaluate("() => window.__sbGuard.modal") >= 1)
        ok &= check("普通公告弹窗没被误点",
                    "btn-ok" not in page.evaluate("() => window.__clicks"))

        # 新插入的播放器（切资源时平台会新建 video）
        page.evaluate("""() => {
            const v = document.createElement('video');
            v.id = 'v2';
            document.body.appendChild(v);
        }""")
        page.wait_for_timeout(900)
        ok &= check("新插入的播放器也会被静音",
                    page.evaluate("() => document.getElementById('v2').muted") is True)

        # 平台在播放前把 muted 改回来
        page.evaluate("() => { document.getElementById('v1').muted = false; }")
        page.wait_for_timeout(900)
        ok &= check("平台改回有声后又被静音",
                    page.evaluate("() => document.getElementById('v1').muted") is True)

        # play() 之前抢静音
        got = page.evaluate("""() => {
            const v = document.getElementById('v2');
            v.muted = false;
            try { v.play().catch(() => {}); } catch (e) {}
            return v.muted;
        }""")
        ok &= check("调用 play() 的瞬间就已经静音", got is True, str(got))

        # ---------- 2. 刷时长模式（优先「重新开始」） ----------
        page2 = browser.new_page(viewport={"width": 1200, "height": 800})
        page2.goto(url)
        page2.wait_for_timeout(200)
        install_guard(page2, prefer_resume=False)
        page2.wait_for_timeout(1500)
        ok &= check("刷时长模式点的是「重新开始」",
                    page2.evaluate("() => window.__clicks") == ["btn-restart"],
                    str(page2.evaluate("() => window.__clicks")))

        # ---------- 3. 导航后仍然生效 + Browser 层封装 ----------
        page3 = browser.new_page(viewport={"width": 1200, "height": 800})
        page3.goto(url)
        bus = StubBus()
        br = Browser(bus)
        br.page = page3
        br.install_guards(prefer_resume=True)
        page3.wait_for_timeout(1200)

        stats = br.guard_stats()
        ok &= check("guard_stats 读到静音/弹窗计数",
                    stats["modal"] >= 1 and stats["muted"] >= 1, str(stats))
        br.guard_flush()          # 刷课循环里就是这么结算的
        logged = [m for _, m in bus.logs]
        ok &= check("日志里写了「自动关闭续播弹窗」", any("自动关闭续播弹窗" in m for m in logged), str(logged))
        ok &= check("日志里写了「自动静音」", any("自动静音" in m for m in logged), str(logged))
        ok &= check("重复结算不会重复刷日志", br.guard_flush() == 0 and len(bus.logs) == len(logged),
                    str(len(bus.logs)))

        # 重新加载（SPA 里 reload / 换课程都会发生）后脚本应自动再注入
        page3.reload()
        page3.wait_for_timeout(800)
        ok &= check("导航后看门狗仍在（init script 生效）",
                    page3.evaluate("() => !!window.__sbGuard") is True)
        ok &= check("导航后弹窗照样被关掉",
                    page3.evaluate("() => window.__clicks") == ["btn-resume"],
                    str(page3.evaluate("() => window.__clicks")))

        # 任务结束：解除强制静音，别再跟用户抢音量
        br.release_guards()
        page3.evaluate("() => { document.getElementById('v1').muted = false; }")
        page3.wait_for_timeout(1200)
        ok &= check("释放后不再强制静音（用户能自己开声音）",
                    page3.evaluate("() => document.getElementById('v1').muted") is False)

        browser.close()

    print("\n" + ("全部通过" if ok else "有用例失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
