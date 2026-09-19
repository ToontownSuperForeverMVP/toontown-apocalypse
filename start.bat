@echo off
setlocal EnableExtensions
title Toontown Apocalypse

set "PROJECT_ROOT=%~dp0"
set "UPDATER=%PROJECT_ROOT%tools\update_from_github.ps1"
set "GAME_LAUNCHER=%PROJECT_ROOT%launch\windows\start-all.bat"

echo Updating Toontown Apocalypse from GitHub...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%UPDATER%" -ProjectRoot "%PROJECT_ROOT%"
if errorlevel 1 (
    echo.
    echo Update failed. The game was not started.
    pause
    exit /b 1
)

echo.
echo Starting Toontown Apocalypse...
call "%GAME_LAUNCHER%"
exit /b %errorlevel%
