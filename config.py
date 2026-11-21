"""StudyBuddy 全局配置。"""
import os
import sys


def app_dir():
    """程序所在目录（兼容 PyInstaller 打包后运行）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_dir()
# 持久化用户数据目录（登录态等）放在程序旁，便携不外泄。
# v1.0.5：支持环境变量覆盖，便于在同一台机器上跑隔离的测试实例，
# 不会和用户正在使用的浏览器抢同一个 profile 锁。
USERDATA_DIR = os.environ.get("SB_USERDATA") or os.path.join(BASE_DIR, "userdata")
SHOTS_DIR = os.path.join(BASE_DIR, "shots")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

for _d in (USERDATA_DIR, SHOTS_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)

# 站点
AI_ICVE_HOME = "https://ai.icve.com.cn/app/my-excellent-home"
ZJY2_HOME = "https://www.icve.com.cn/"

# 默认目标
DEFAULT_TARGET_HOURS = 5.0

# 浏览器调试端口（同时只允许一个实例占用）；可用 SB_CDP_PORT 覆盖
CDP_PORT = int(os.environ.get("SB_CDP_PORT") or 9222)

APP_NAME = "StudyBuddy 学习助手"
APP_VERSION = "1.0.6"
