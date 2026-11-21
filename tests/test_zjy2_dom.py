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
import time

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
      <div class="fwenjianjia" id="r3">
        <span class="name">第三章总结.mp4</span><span class="ziProgress">已学完</span>
      </div>
    </div>
  </div>
</div>
<div id="content">
  <div class="tab active">第一章导学.mp4</div>
  <div class="docname">第一章导学.mp4</div>
  <div id="pages"></div>
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
        ok &= check("list_resources 只返回叶子节点",
                    names == ["第一章助学.docx", "第一章导学.mp4", "第三章总结.mp4"], str(names))

        # 2. 进度百分比解析
        pcts = {x["name"]: x["pct"] for x in items}
        ok &= check("进度解析 0% / 100%",
                    pcts.get("第一章助学.docx") == 0 and pcts.get("第一章导学.mp4") == 100, str(pcts))
        ok &= check("「已学完」这类无数字进度算 100", pcts.get("第三章总结.mp4") == 100, str(pcts))

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
                document.querySelector('#pages').textContent = '1 / 3';
            });
        }""")
        opened = z.open_resource({"id": "r1", "name": "第一章助学.docx"}, retries=2, wait=2.0)
        ok &= check("内容切换后 open_resource 返回 True", opened is True)

        # 11. 慢网：点了之后只弹出加载遮罩、内容还是旧的 -> 不能开始刷
        page.evaluate("""() => {
            const c = document.getElementById('content');
            const mask = document.createElement('div');
            mask.id = 'mask';
            mask.className = 'el-loading-mask';
            mask.textContent = '加载中…';
            mask.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;background:#fff;z-index:9;pointer-events:none';
            c.appendChild(mask);
            document.querySelector('#pages').textContent = '';
            document.querySelector('.docname').textContent = '第一章助学.docx';
            document.querySelector('.tab').textContent = '第一章助学.docx';
        }""")
        st = z._content_state()
        ok &= check("识别出加载遮罩", st.get("loading") is True, str(st.get("loading")))
        ok &= check("加载中不判定为就绪", z._ready_kind(st)[1] is False)
        res = z._learn_current("第一章助学.docx")
        ok &= check("加载中绝不启动刷课", res["ok"] is False and res["kind"] == "", str(res))

        # 12. 遮罩还在的时候，open_resource 不许返回 True（哪怕标签已经变了）
        t0 = time.time()
        bad = z.open_resource({"id": "r1", "name": "第一章助学.docx"}, retries=1, wait=1.2)
        ok &= check("加载没完时 open_resource 返回 False", bad is False,
                    f"{time.time() - t0:.1f}s")

        # 13. 遮罩消失、页码出现后才算打开（模拟慢网 1.2 秒才加载完）
        page.evaluate("""() => {
            const n = document.getElementById('r1');
            n.querySelector('.name').addEventListener('click', () => {
                document.querySelector('#pages').textContent = '';
                let mask = document.getElementById('mask');
                if (!mask) {
                    const c = document.getElementById('content');
                    mask = document.createElement('div');
                    mask.id = 'mask';
                    mask.className = 'el-loading-mask';
                    mask.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;background:#fff;z-index:9;pointer-events:none';
                    c.appendChild(mask);
                }
                setTimeout(() => {
                    const m = document.getElementById('mask');
                    if (m) m.remove();
                    document.querySelector('.docname').textContent = '第一章助学.docx';
                    document.querySelector('#pages').textContent = '1 / 3';
                }, 1200);
            });
        }""")
        t0 = time.time()
        good = z.open_resource({"id": "r1", "name": "第一章助学.docx"}, retries=2, wait=10)
        dt = time.time() - t0
        ok &= check("慢网加载完后 open_resource 返回 True", good is True, f"{dt:.1f}s")
        ok &= check("确实是等加载完才继续（>=1 秒）", dt >= 1.0, f"{dt:.1f}s")

        # 14. 视频：元数据没到（duration=0）不算就绪，到了才算
        page.evaluate("""() => {
            const v = document.createElement('video');
            v.id = 'vslow';
            v.style.cssText = 'width:520px;height:300px;display:block';
            Object.defineProperty(v, 'duration', {get: () => window.__dur || 0});
            Object.defineProperty(v, 'readyState', {get: () => window.__rs || 0});
            Object.defineProperty(v, 'currentSrc', {get: () => 'https://x/slow.mp4'});
            document.getElementById('content').appendChild(v);
            document.querySelector('#pages').textContent = '';   // 只留视频，避免判成文档
            window.__rs = 0; window.__dur = 0;
        }""")
        st = z._content_state()
        ok &= check("视频没拿到时长时不算就绪", z._ready_kind(st) == ("", False),
                    str(z._ready_kind(st)))
        page.evaluate("() => { window.__rs = 4; window.__dur = 120; }")
        st = z._content_state()
        ok &= check("拿到时长后判定为可刷视频", z._ready_kind(st) == ("video", True),
                    str(z._ready_kind(st)))

        browser.close()

    print("\n结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
