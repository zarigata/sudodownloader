@echo off
rem ---------------------------------------------------------------
rem  Suno Playlist Downloader - Windows launcher
rem  Just double-click this file. First run installs what it needs.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set SUNODL_NO_PAUSE=1

set "PY="

rem Prefer the official Windows Python launcher
where py >nul 2>nul && set "PY=py -3"

rem Fall back to plain python
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo.
    echo  [!] Python wasn't found on this computer.
    echo      The download page is opening in your browser -
    echo      install Python, then double-click this file again.
    echo.
    start "" https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

rem First run: quietly install the two needed packages
%PY% -c "import requests, rich, cryptography" >nul 2>nul
if errorlevel 1 (
    echo.
    echo  First run - setting things up one time only...
    echo.
    %PY% -m pip install --user --quiet -r requirements.txt
)

%PY% suno_dl.py %*

echo.
pause
