# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

策略：不打包 Chromium（省 150MB+），运行时复用系统 Edge。
用 `pyinstaller build.spec` 打包，产物在 dist/StudyBuddy/。
"""
import sys
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

_ICON = os.path.abspath(os.path.join("assets", "icon.ico"))
if not os.path.exists(_ICON):
    _ICON = None
print(f"[spec] icon = {_ICON}")

hiddenimports = collect_submodules("playwright") + ["PIL.Image", "PIL.ImageTk"]

# playwright 需要完整的 driver（含 node.exe 与 package），否则打包后无法驱动浏览器
_datas = collect_data_files("playwright", include_py_files=False)
# 显式补齐 driver 下的二进制与包文件
import playwright as _pw_pkg
_pw_root = os.path.dirname(os.path.abspath(_pw_pkg.__file__))
_driver = os.path.join(_pw_root, "driver")
if os.path.isdir(_driver):
    for root, _dirs, files in os.walk(_driver):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, _pw_root)
            dst = os.path.join("playwright", os.path.dirname(rel))
            _datas.append((src, dst))
# 图标同时打进包内，供 tkinter 设置窗口 / 任务栏图标
if _ICON:
    _datas.append((_ICON, "assets"))

# 去重
_seen = set()
_datas2 = []
for d in _datas:
    if d[0] not in _seen:
        _seen.add(d[0]); _datas2.append(d)
_datas = _datas2

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "scipy", "PyQt5", "PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StudyBuddy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StudyBuddy",
)
