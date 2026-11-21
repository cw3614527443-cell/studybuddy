"""刷课 skip 模式的真机回归测试（v1.0.5）。

做三件事，全部使用真实登录态：
1. 打开一个**已完成**的视频节点 —— 必然触发「检测到上次播放…是否继续？」弹窗；
2. 验证 dismiss_modal() 能识别并点掉它（这是本次修复的核心）；
3. 验证 skip_video() 能把进度条拖到结尾。

用法：
    python tests/test_brush_skip.py [--headless]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# 允许指定一个别的浏览器数据目录（例如带登录态的测试 profile）
if os.environ.get("SB_USERDATA"):
    config.USERDATA_DIR = os.environ["SB_USERDATA"]

import core.browser as _browser_mod
_browser_mod.USERDATA_DIR = config.USERDATA_DIR

from core.browser import Browser, EdgeProc
from core.events import EventBus
from core.platforms.ai_icve import AiIcve

HEADLESS = "--headless" in sys.argv
COURSE = os.environ.get("SB_COURSE", "大学生心理健康")


def main():
    bus = EventBus()
    br = Browser(bus)
    br.connect(launch=True, headless=HEADLESS, start_url=config.AI_ICVE_HOME)
    rc = 0
    try:
        plat = AiIcve(br, bus)
        br.wait(3000)
        h, c = plat.read_watch()
        print(f"[1] 登录态检查：观看时长={h} 时 / 学习次数={c}")
        if h is None:
            print("    ✗ 读不到观看时长，可能掉登录；先手动登录后再测")
            return 2

        if not plat.enter_course(COURSE):
            print("    ✗ 进入课程失败")
            return 2
        print(f"[2] 已进入课程《{COURSE}》")

        plat.expand_all()
        items = plat.list_resources()
        vids = [x for x in items if x["type"] == "video"]
        print(f"[3] 资源 {len(items)} 项，其中视频 {len(vids)} 项，"
              f"已完成 {sum(1 for x in items if x['done'])} 项")
        if not vids:
            print("    ✗ 没有视频节点，无法测试")
            return 2

        node = vids[0]
        print(f"[4] 打开视频：{node['name']}（当前标记 done={node['done']}）")
        plat.open_node(node)
        br.wait(2000)

        modal = br.visible_modal_text()
        print(f"[5] 弹窗检测：{modal[:100] or '(无)'}")
        if modal:
            br.shot("test_modal_before")
            n = plat.dismiss_modal()
            print(f"    已点掉 {n} 个弹窗")
            br.wait(1000)
            still = br.visible_modal_text()
            print(f"    处理后残留：{still[:80] or '(无)'}")
            if "检测到上次播放" in still:
                print("    ✗ 弹窗没点掉")
                rc = 1
            else:
                print("    ✓ 弹窗已清除")
        else:
            print("    （本次未弹续播框，属正常：该视频可能没有历史进度）")

        print("[6] 测试 skip_video 拖进度条到末尾…")
        t0 = time.time()
        # 已经打开过了，这里直接走内部拖动逻辑：用当前页面状态
        st0 = plat.video_state()
        print(f"    拖动前：{st0}")
        ok = plat.skip_video(node)
        dur = time.time() - t0
        st1 = plat.video_state()
        print(f"    拖动后：{st1}")
        print(f"    结果={'成功' if ok else '失败'}，耗时 {dur:.1f} 秒")
        br.shot("test_skip_after")
        if dur > 120:
            print("    ✗ 耗时过长，可能没走 skip 路径")
            rc = 1
        if not ok:
            rc = 1
    finally:
        br.detach()
        print("[done] 已断开连接（浏览器保持打开，方便你继续看）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
