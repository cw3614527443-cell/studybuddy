@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 安装依赖 ===
if exist "D:\python\python.exe" (
    set PY=D:\python\python.exe
) else (
    set PY=python
)
%PY% -m pip install -r requirements.txt
echo === 打包 ===
%PY% -m PyInstaller build.spec --noconfirm --clean
echo === 完成，产物在 dist\StudyBuddy\ ===
pause
