@echo off
setlocal EnableExtensions
title Toontown Apocalypse: Unified Launcher

rem This script lives in launch\windows, so the repository root is two levels up.
set "REPO_DIR=%~dp0..\.."
set "PYTHON_EXE=%REPO_DIR%\Panda3D\python\ppython.exe"

if not exist "%PYTHON_EXE%" (
    echo Could not find the bundled Panda3D Python interpreter:
    echo   "%PYTHON_EXE%"
    echo.
    echo Place the Panda3D runtime in the repository's Panda3D directory.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0launcher.py"
exit /b %errorlevel%
