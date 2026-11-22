"""zjy2 新版课件阅读器的识别回归测试（v1.8.0）。

用户报的 bug：「明明已经加载出来了，却识别不到」——截图里 PPT 已经显示
（1 / 59 页），软件却一直写「页面加载较慢（没出现播放器或文档）」，
最后报「第 1 次没等到内容，换方式重试」。

根因：zjy2 换了新版课件阅读器
  · 没有 <video>、没有 iframe、没有 canvas，只有 `.office` 里的 el-carousel
    幻灯片 <img>；
  · 页码写成 `1 / 59 下一页`（页码容器里还塞着翻页按钮），
    旧代码要求「整行就是 12/34」才认，于是永远识别不到。

这里用仿造页面把新版结构钉死，不登录、不联网。
跑法：python tests/test_zjy2_reader.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.platforms.zjy2 import Zjy2  # noqa: E402

# 新版阅读器：.office + el-carousel 的幻灯片图片 + "1 / 59 下一页" 复合页码
HTML_NEW = """<!doctype html><html><head><meta charset="utf-8">
<style>
  body{margin:0;font:14px/1.5 sans-serif}
  .office{display:block;width:900px;padding:10px}
  .office img{width:900px;height:500px;display:block}
  .page{margin-top:8px}
</style></head><body>
<div class="office">
  <div class="carousel el-carousel el-carousel--horizontal">
    <div class="el-carousel__container">
      <img id="s1" src="https://file.icve.com.cn/app/site/prod/zyzyzx_components/loading.gif">
      <img id="s2" src="https://file.icve.com.cn/app/site/prod/zyzyzx_components/loading.gif">
    </div>
  </div>
  <div class="page">
    <span id="cur">1</span> / <span id="tot">59</span>
    <button type="button" id="next"><span> 下一页 </span></button>
  </div>
</div>
<div class="el-form-item">
  <span class="el-input__count">0/50</span>
</div>
<script>
  let cur = 1;
  const total = 59;
  document.getElementById('next').addEventListener('click', () => {
    if (cur >= total) return;
    cur += 1;
    document.getElementById('cur').textContent = cur;
    if (cur >= total) document.getElementById('next').remove();
  });
</script>
</body></html>"""


class FakeBr:
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

    def reload(self):
        self.page.reload()

    def guard_flush(self):
        pass

    def click_text(self, *a, **k):
        return False


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "sb_fixture_reader.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HTML_NEW)

    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto("file:///" + tmp.replace("\\", "/"))
        page.wait_for_timeout(300)
        z = Zjy2(FakeBr(page))

        st = z._content_state()
        ok &= check("识别到新版阅读器 .office", st.get("reader") is True, str(st.get("reader")))
        ok &= check("页码解析出 1/59（含后缀「下一页」）",
                    st.get("pages") == {"cur": 1, "tot": 59}, str(st.get("pages")))
        ok &= check("排除评论框字数计数器 0/50",
                    (st.get("pages") or {}).get("tot") != 50, str(st.get("pages")))
        ok &= check("幻灯片全部是 loading.gif 时不算就绪",
                    st.get("slidesLoaded") == 0, str(st.get("slidesLoaded")))
        ok &= check("_ready_kind 认得新版阅读器", z._ready_kind(st) == ("doc", True),
                    str(z._ready_kind(st)))
        ok &= check("不再误报「没出现播放器或文档」",
                    "没出现播放器或文档" not in z._describe(st), z._describe(st))
        ok &= check("_doc_pages 与状态一致", z._doc_pages() == {"cur": 1, "tot": 59},
                    str(z._doc_pages()))

        # 幻灯片真的加载出来（src 不再是 loading.gif）→ 依然就绪
        page.evaluate("""() => {
            document.getElementById('s1').src = 'https://file.icve.com.cn/doc/1/page1.png';
            document.getElementById('s2').src = 'https://file.icve.com.cn/doc/1/page2.png';
        }""")
        page.wait_for_timeout(120)
        st2 = z._content_state()
        ok &= check("幻灯片换成真图后仍就绪", z._ready_kind(st2) == ("doc", True)
                    and st2.get("slidesLoaded") == 2, str(st2.get("slidesLoaded")))

        # 翻页按钮是 <button><span> 下一页 </span></button>
        ok &= check("点得动新版「下一页」", z._click_next_page() is True)
        page.wait_for_timeout(120)
        ok &= check("页码跟着走到 2/59", z._doc_pages() == {"cur": 2, "tot": 59},
                    str(z._doc_pages()))
        ok &= check("翻页后内容指纹变化（能判断内容真的换了）",
                    z._content_sig() != (st2.get("sig") or ""), z._content_sig())

        # 一直翻到末页：按钮消失后不能死循环
        for _ in range(70):
            if not z._click_next_page():
                break
        page.wait_for_timeout(120)
        ok &= check("翻到末页停在 59/59", z._doc_pages() == {"cur": 59, "tot": 59},
                    str(z._doc_pages()))
        ok &= check("末页后「下一页」点不动返回 False", z._click_next_page() is False)

        browser.close()

    print("\n结果：全部通过" if ok else "\n结果：有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
