"""v1.0.1 回归测试：验证「跨任务复用浏览器」不再抛 TargetClosedError。

背景：v1.0.0 把 Playwright 连接缓存在 TaskRunner 上，第二个任务线程复用时连接已失效，
必然崩溃。v1.0.1 改为每任务线程独立 connect / detach。

本脚本模拟 GUI 里连续点两次按钮的真实序列，两次都必须成功。

用法：
    D:\\python\\python.exe tests\\test_reconnect.py
    D:\\python\\python.exe tests\\test_reconnect.py --visible   # 有头模式，能看到窗口
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AI_ICVE_HOME                      # noqa: E402
from core.browser import Browser, EdgeProc           # noqa: E402
from core.events import EventBus                     # noqa: E402
from core.platforms.ai_icve import AiIcve            # noqa: E402

HEADLESS = "--visible" not in sys.argv


def one_task(tag, bus):
    """模拟一个完整任务：建连接 → 干活 → 断开（浏览器保持存活）。"""
    br = Browser(bus)
    # 不传 start_url：让 goto 单独发导航，避免和站点自动跳 SSO 撞车
    br.connect(launch=True, headless=HEADLESS)
    try:
        plat = AiIcve(br, bus)
        h, c = plat.read_watch()
        url = br.current_url()
        shot = br.shot(tag)
        print(f"[{tag}] 连接成功  观看时长={h}  次数={c}")
        print(f"[{tag}] URL={url[:60]}")
        print(f"[{tag}] 截图={shot}")
        return h, c
    finally:
        br.detach()


def main():
    bus = EventBus()
    ok = True
    try:
        print("=" * 60)
        print("第一轮任务（模拟点「读取观看时长」）")
        print("=" * 60)
        h1, c1 = one_task("r1", bus)
        print(f"→ 第一轮结束，Edge 进程仍在运行：{EdgeProc.is_running()}")

        time.sleep(2)  # 模拟用户看日志、再点下一个按钮的间隔

        print()
        print("=" * 60)
        print("第二轮任务（关键：旧版本在这里必然 TargetClosedError）")
        print("=" * 60)
        h2, c2 = one_task("r2", bus)
        print(f"→ 第二轮结束，Edge 进程仍在运行：{EdgeProc.is_running()}")

        print()
        if h1 is not None and h2 is not None:
            print(f"✅ 通过：两轮都读到数据（{h1} 时 / {h2} 时），跨线程复用问题已修复")
        elif h1 is None and h2 is None:
            print("⚠️  两轮都连上了（未崩溃），但没读到时长——请确认已登录该站点")
            print("   连接层修复本身是成功的；数据为 None 属于登录态或页面问题")
        else:
            print(f"⚠️  部分成功：第一轮={h1}，第二轮={h2}")
            ok = False

    except Exception as e:
        ok = False
        print()
        print("❌ 失败：", e)
        traceback.print_exc()
    finally:
        try:
            EdgeProc.kill()
            print("\n已清理测试用 Edge 进程")
        except Exception:
            pass

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
