"""zjy2 / ai_icve 的 DOM 判定回归测试（v1.2.1）。

背景：刷课「日志说刷完了、网页还在放上一个视频」，根因是
  1) list_resources 把父节点也当资源（点它只会折叠/展开）；
  2) 取值时抓到上一个资源遗留的隐藏 <video>（早已 ended）；
  3) 点完不校验内容是否真的切换。
这里用一个仿造的课程页把这些判定钉住，不需要登录、不联网。

跑法：python tests/test_zjy2_dom.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.platforms.ai_icve import AiIcve, _PICK_VIDEO  # noqa: E402
from core.platforms.zjy2 import Zjy2  # noqa: E402

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
  body{margin:0;font:14px/1.5 sans-serif;display:flex}
  #tree{width:240px;border-right:1px solid #ccc;padding:8px}
  #content{flex:1;padding:8px}
  video{width:400px;height:240px;background:#000}
  .hidden{display:none}
</style></head><body>
<div id="tree">
  <div class="listItem" id="ch1">
    <!-- 父节点：内部还有资源，不应该被当成资源 -->
    <div class="fwenjianjia" id="folder1">
      <span class="name">第一章</span>
      <div class="fwenjianjia" id="r1">
        <span class="name">第一章助学.docx</span><span class="ziProgress">进度:0%</span>
        <i class="checkmark jd"></i>
      </div>
      <div class="fwenjianjia" id="r2">
        <span class="name">第一章导学.mp4</span><span class="ziProgress">进度:100%</span>
        <i class="checkmark wc"></i>
      </div>
    </div>
  </div>
</div>
<div id="content">
  <div class="tab active">第一章导学.mp4</div>
  <div class="docname">第一章导学.mp4</div>
  <!-- 新的播放器（可见） -->
  <video id="v_new"></video>
  <!-- 上一个资源遗留的播放器（隐藏，仍然存在 DOM 里） -->
  <video id="v_old" class="hidden"></video>
</div>
</body></html>"""


class FakeBr:
    """只提供适配器用到的最小接口。"""

    def __init__(self, page):
        self.page = page
        self.shots = []

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def text(self):
        return self.page.locator("body").inner_text()

    def current_url(self):
        return self.page.url

    def shot(self, tag="x"):
        self.shots.append(tag)

    def click_text(self, label, timeout=500):
        return False


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "sb_fixture_zjy2.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HTML)

    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto("file:///" + tmp.replace("\\", "/"))
        page.wait_for_timeout(300)

        br = FakeBr(page)
        z = Zjy2(br)

        # 1. 只枚举叶子资源，父节点不算
        items = z.list_resources()
        names = [x["name"] for x in items]
        ok &= check("list_resources 只返回叶子节点", names == ["第一章助学.docx", "第一章导学.mp4"], str(names))

        # 2. 进度百分比解析
        pcts = {x["name"]: x["pct"] for x in items}
        ok &= check("进度解析 0% / 100%",
                    pcts.get("第一章助学.docx") == 0 and pcts.get("第一章导学.mp4") == 100, str(pcts))

        # 3. 按 id 回读进度
        ok &= check("progress_of 回读指定节点", z.progress_of("r2") == 100,
                    str(z.progress_of("r2")))

        # 4. 内容区识别：正文里的名字算「已打开」
        ok &= check("content_shows 命中内容区标题", z._content_shows("第一章导学.mp4") is True)
        # 5. 目录树里的名字不算（否则点没点开都判为已打开）
        ok &= check("content_shows 不把目录树当内容区", z._content_shows("第一章") is False)

        # 6. 选中可见播放器，忽略隐藏的旧播放器
        picked = page.evaluate("() => {" + _PICK_VIDEO + "\n const v = pick(); return v ? v.id : null; }")
        ok &= check("_PICK_VIDEO 选中可见且面积大的播放器", picked == "v_new", str(picked))

        # 7. 内容指纹能区分「换没换内容」
        sig1 = z._content_sig()
        page.evaluate("""() => {
            document.querySelector('.tab').textContent = '第一章助学.docx';
            document.querySelector('.docname').textContent = '第一章助学.docx';
        }""")
        sig2 = z._content_sig()
        ok &= check("内容指纹随标签变化", sig1 != sig2)

        # 8. 完成标记回读（ai 平台用它判断「真的刷完了」）
        ai = AiIcve(br)
        ok &= check("node_done 读到未完成(jd)", ai.node_done("r1") is False, str(ai.node_done("r1")))
        ok &= check("node_done 读到已完成(wc)", ai.node_done("r2") is True, str(ai.node_done("r2")))
        ok &= check("_wait_done 立即返回 True", ai._wait_done("r2", timeout=1) is True)

        # 9. 点了但内容没切换 -> 不能谎报成功（这是本次 bug 的核心）
        #    先把内容区恢复成「上一个资源」，模拟真实场景：页面还停在旧资源上
        page.evaluate("""() => {
            document.querySelector('.tab').textContent = '第一章导学.mp4';
            document.querySelector('.docname').textContent = '第一章导学.mp4';
        }""")
        fails = z.open_resource({"id": "r1", "name": "第一章助学.docx"}, retries=2, wait=0.4)
        ok &= check("内容没切换时 open_resource 返回 False", fails is False)

        # 10. 点了并且内容真的换了 -> 判定为已打开
        page.evaluate("""() => {
            const n = document.getElementById('r1');
            n.querySelector('.name').addEventListener('click', () => {
                document.querySelector('.docname').textContent = '第一章助学.docx';
            });
        }""")
        opened = z.open_resource({"id": "r1", "name": "第一章助学.docx"}, retries=2, wait=2.0)
        ok &= check("内容切换后 open_resource 返回 True", opened is True)

        browser.close()

    print("\n结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
