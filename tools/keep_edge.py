"""有头联调用的「保活」脚本（开发期，不打包）。

单独跑一个 python -c 启动 Edge，进程一退 Edge 就跟着被回收；
所以这里把 Edge 拉起来后自己不退出，让浏览器窗口一直留着给人扫码。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.browser import EdgeProc, _port_alive


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else config.AI_ICVE_HOME
    EdgeProc.start(headless=False, start_url=url, log=lambda m: print(m, flush=True))
    print("READY", url, flush=True)
    n = 0
    while True:
        time.sleep(5)
        n += 1
        if not _port_alive():
            print("EDGE_GONE after %d checks" % n, flush=True)
            return 1
        if n % 12 == 0:
            print("alive %ds" % (n * 5), flush=True)


if __name__ == "__main__":
    sys.exit(main())
