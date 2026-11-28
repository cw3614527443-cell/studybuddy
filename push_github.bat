@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  StudyBuddy - one click push to GitHub
REM  Handles: git not on PATH / proxy / remote ahead of local
REM  Edit PROXY below if your Clash uses another port.
REM ============================================================
set PROXY=http://127.0.0.1:7897

echo ============================================
echo  StudyBuddy - push to GitHub
echo  repo : %CD%
echo ============================================
echo.

REM ---------- 1. locate git ----------
set GITCMD=
for %%G in (git.exe) do if not defined GITCMD if exist "%%~$PATH:G" set GITCMD=%%~$PATH:G
if not defined GITCMD if exist "D:\Git\cmd\git.exe" set GITCMD=D:\Git\cmd\git.exe
if not defined GITCMD if exist "C:\Program Files\Git\cmd\git.exe" set GITCMD=C:\Program Files\Git\cmd\git.exe
if not defined GITCMD if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set GITCMD=%LOCALAPPDATA%\Programs\Git\cmd\git.exe
if not defined GITCMD if exist "C:\Users\mahiro\.workbuddy\binaries\PortableGit\versions\1.3.0\cmd\git.exe" set GITCMD=C:\Users\mahiro\.workbuddy\binaries\PortableGit\versions\1.3.0\cmd\git.exe
if not defined GITCMD if exist "D:\workbuddy\resources\vendor\PortableGit\cmd\git.exe" set GITCMD=D:\workbuddy\resources\vendor\PortableGit\cmd\git.exe
if not defined GITCMD goto nogit
set "GITDIR=%GITCMD%\.."
set PATH=%GITDIR%;%PATH%
echo [git ] %GITCMD%
"%GITCMD%" --version
echo.

set HTTP_PROXY=%PROXY%
set HTTPS_PROXY=%PROXY%

REM ---------- 2. sync with remote ----------
echo [1/5] fetching origin ...
"%GITCMD%" fetch origin
if errorlevel 1 goto netfail

for /f %%i in ('"%GITCMD%" rev-list --count HEAD..origin/main') do set BEHIND=%%i
for /f %%i in ('"%GITCMD%" rev-list --count origin/main..HEAD') do set AHEAD=%%i
echo       local ahead  : %AHEAD%
echo       remote ahead : %BEHIND%
echo.

if "%BEHIND%"=="0" goto pushmain

REM ---------- 3. remote has new commits -> merge ----------
echo [2/5] remote has %BEHIND% commit^(s^) that you do not have:
"%GITCMD%" log --oneline --no-decorate HEAD..origin/main
echo.
echo [3/5] merging remote into local (merge commit, keeps tag v1.7.2 valid) ...
"%GITCMD%" -c core.editor=true merge --no-edit origin/main
if errorlevel 1 goto conflict
echo.

:pushmain
echo [4/5] pushing main ...
"%GITCMD%" push origin main
if errorlevel 1 goto pushfail
echo.

echo [5/5] pushing tags and verifying ...
"%GITCMD%" push origin --tags
"%GITCMD%" fetch origin --quiet
"%GITCMD%" status -sb
"%GITCMD%" log --oneline -1 origin/main
echo.
echo ============================================
echo  DONE - local and GitHub are in sync.
echo ============================================
echo.
pause
exit /b 0

:nogit
echo [ERROR] git.exe not found. Searched PATH, D:\Git, C:\Program Files\Git,
echo         %%LOCALAPPDATA%%\Programs\Git, WorkBuddy PortableGit.
echo.
pause
exit /b 1

:netfail
echo.
echo [ERROR] cannot reach github.com.
echo  - Is Clash running on port 7897?  (edit PROXY at top of this file if not)
echo  - Nothing was changed locally.
echo.
pause
exit /b 1

:conflict
echo.
echo [ERROR] merge conflict. NOTHING was pushed.
echo Conflicted files:
"%GITCMD%" diff --name-only --diff-filter=U
echo.
echo  Send me this list and I will resolve it.
echo  To roll back this merge:  git merge --abort
echo.
pause
exit /b 1

:pushfail
echo.
echo [ERROR] push failed - see the error above.
echo  - auth prompt: username = cw3614527443-cell, password = GitHub PAT
echo.
pause
exit /b 1
