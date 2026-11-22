"""续播弹窗检测/清除的真机测试（v1.0.5）。

思路：把视频拖到 5 分钟处播放几秒（让平台记住进度），切到别的资源再切回来，
平台就会弹「检测到上次播放至X分X秒，是否继续？」。然后验证：
1. visible_modal_text() 能抓到它（不能误报成时间条/评价面板）；
2. dismiss_modal() 能点掉它，点完后检测为空。

用法：
    SB_USERDATA=<带登录态的 profile> python tests/test_modal.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

if os.environ.get("SB_USERDATA"):
    config.USERDATA_DIR = os.environ["SB_USERDATA"]

import core.browser as _bm
_bm.USERDATA_DIR = config.USERDATA_DIR

from core.browser import Browser
from core.events import EventBus
from core.platforms.ai_icve import AiIcve

COURSE = os.environ.get("SB_COURSE", "大学生心理健康")


def main():
    bus = EventBus()
    br = Browser(bus)
    br.connect(launch=True, start_url=config.AI_ICVE_HOME)
    rc = 0
    try:
        br.wait(2500)
        plat = AiIcve(br, bus)
        h, _ = plat.read_watch()
        print(f"[1] 登录态：{h} 时")
        if not plat.enter_course(COURSE):
            print("    ✗ 进入课程失败")
            return 2
        plat.expand_all()
        items = plat.list_resources()
        vids = [x for x in items if x["kind"] == "video"]
        docs = [x for x in items if x["kind"] == "doc"]
        if len(vids) < 1:
            print("    ✗ 无视频节点")
            return 2
        v = vids[0]
        other = docs[0] if docs else (vids[1] if len(vids) > 1 else None)

        print(f"[2] 打开《{v['name']}》，拖到 5 分钟处停留 6 秒制造历史进度")
        plat.open_node(v)
        br.wait(2500)
        plat.dismiss_modal(rounds=1)
        plat.p.evaluate(
            """()=>{const v=document.querySelector('video'); if(!v) return;
                v.muted=true; v.playbackRate=1;
                try{ v.currentTime = 300; }catch(e){}
                v.play().catch(()=>{});}"""
        )
        br.wait(6000)

        if other:
            print(f"[3] 切走：{other['name'][:30]}")
            plat.open_node(other)
            br.wait(3000)
            plat.dismiss_modal(rounds=1)
        print("[4] 切回刚才那个视频（此刻应弹续播框）")
        plat.open_node(v)
        br.wait(2500)

        txt = br.visible_modal_text()
        print(f"[5] 检测结果：{txt[:120] or '(空)'}")
        br.shot("modal_test_detected")
        if not txt:
            print("    ⚠ 没检测到弹窗（平台这次没弹，或进度没被记住）")
        elif "检测到上次播放" in txt or "是否继续" in txt:
            if "00:" in txt or "课程评价" in txt:
                print("    ✗ 误报：抓到的是时间条/评价面板")
                rc = 1
            else:
                n = plat.dismiss_modal()
                print(f"    已点击 {n} 次")
                br.wait(1500)
                after = br.visible_modal_text()
                print(f"[6] 处理后再检测：{after[:100] or '(空)'}")
                if "检测到上次播放" in after:
                    print("    ✗ 弹窗仍在")
                    rc = 1
                else:
                    print("    ✓ 弹窗已清除")
                    st = plat.video_state()
                    print(f"    视频状态：{st}")
        else:
            print("    ⚠ 检测到浮层但内容无关，确认不是误报")
            print("    ", txt[:100])

        # 顺带确认正常页面不会误报
        plat.open_node(other) if other else None
        br.wait(2500)
        print(f"[7] 普通资源页的空检测：{br.visible_modal_text()[:80] or '(空，正确)'}")
    finally:
        br.detach()
        print("[done]")
    return rc


if __name__ == "__main__":
    sys.exit(main())
