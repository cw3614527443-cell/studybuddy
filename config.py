"""StudyBuddy 全局配置。"""
import os
import sys


def app_dir():
    """程序所在目录（兼容 PyInstaller 打包后运行）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_dir()


def _writable(p):
    """目录是否可写（装到 Program Files 时普通用户不可写）。"""
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def data_dir():
    """用户数据（登录态/截图/日志）存放位置。

    三种情形，优先级从高到低：
    1. 环境变量 SB_DATA_DIR 显式指定（测试用）；
    2. 程序目录存在 portable.txt → 便携模式，数据放程序旁；
    3. 安装版（安装器写入 installed.flag）或程序目录不可写 → %LOCALAPPDATA%\\StudyBuddy，
       避免装在 Program Files 时无法写入，也避免卸载时误删登录态。

    v1.1.0：新增安装版支持。
    """
    env = os.environ.get("SB_DATA_DIR")
    if env:
        return env
    if os.path.exists(os.path.join(BASE_DIR, "portable.txt")):
        return BASE_DIR
    installed = os.path.exists(os.path.join(BASE_DIR, "installed.flag"))
    if getattr(sys, "frozen", False) and (installed or not _writable(BASE_DIR)):
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local, "StudyBuddy")
    return BASE_DIR


DATA_DIR = data_dir()

# 持久化用户数据目录（登录态等）。
# v1.0.5：支持环境变量覆盖，便于在同一台机器上跑隔离的测试实例，
# 不会和用户正在使用的浏览器抢同一个 profile 锁。
USERDATA_DIR = os.environ.get("SB_USERDATA") or os.path.join(DATA_DIR, "userdata")
SHOTS_DIR = os.path.join(DATA_DIR, "shots")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

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
APP_VERSION = "1.4.0"


# ---------------------------------------------------------------------------
# 大模型设置（自动答题用）
# ---------------------------------------------------------------------------
# 为什么不内置 Key：这个工具是发给别人用的，内置 Key 等于把你的额度公开，
# 几天就会被刷光。所以做成「使用者在设置页填自己的 Key」，
# 接口按 OpenAI 兼容协议写，DeepSeek / 通义 / Kimi / 豆包 / OpenAI 都能用。
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

DEFAULT_LLM = {
    "base": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "key": "",
    "timeout": 90,
}


def load_settings():
    """读设置；文件不存在或损坏时返回默认值（不让程序起不来）。"""
    import json
    data = {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    llm = dict(DEFAULT_LLM)
    llm.update({k: v for k, v in (data.get("llm") or {}).items() if v is not None})
    return {"llm": llm}


def save_settings(settings):
    """写设置（仅本机，明文保存 Key，界面上要提示这一点）。"""
    import json
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def llm_config():
    return load_settings()["llm"]


def resource_path(rel):
    """取打包进程序内部的资源（PyInstaller 解包到 _MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


ICON_PATH = resource_path(os.path.join("assets", "icon.ico"))
