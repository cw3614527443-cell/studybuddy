@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "D:\python\python.exe" (
    "D:\python\python.exe" main.py
) else (
    python main.py
)
pause
