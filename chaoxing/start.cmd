@echo off
REM 启动有头浏览器常驻驱动（Windows）
REM 用法：start.cmd [会话目录名]    例：start.cmd a3
setlocal
set BROWSER_ROOT=%~dp0..\%1
if "%1"=="" set BROWSER_ROOT=%~dp0..
echo BROWSER_ROOT=%BROWSER_ROOT%
node "%~dp0agent.mjs"
