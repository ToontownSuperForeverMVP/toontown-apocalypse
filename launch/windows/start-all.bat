@echo off
setlocal EnableExtensions
title Toontown Apocalypse: Unified Launcher

rem This script lives in launch\windows, so the repository root is two levels up.
set "REPO_DIR=%~dp0..\.."
set "PYTHON_EXE=%REPO_DIR%\Panda3D\python\ppython.exe"
set "PANDA3D_DIR=%REPO_DIR%\Panda3D"

rem The Panda3D runtime is ignored by Git and may be extracted into a nested
rem folder (for example, Panda3D-SDK\Panda3D\python\ppython.exe). Prefer the
rem documented location, then search the entire checkout for the interpreter.
if not exist "%PYTHON_EXE%" (
    for /r "%REPO_DIR%" %%F in (ppython.exe) do (
        if not defined PYTHON_EXE_FOUND (
            set "PYTHON_EXE=%%~fF"
            set "PYTHON_EXE_FOUND=1"
            set "PANDA3D_DIR=%%~dpF.."
        )
    )
)

if not exist "%PYTHON_EXE%" (
    echo Could not find the bundled Panda3D Python interpreter:
    echo   "%PYTHON_EXE%"
    echo.
    echo Place the Panda3D runtime in the repository's Panda3D directory.
    pause
    exit /b 1
)

echo Using Panda3D runtime:
echo   "%PANDA3D_DIR%"
echo Using Panda3D Python:
echo   "%PYTHON_EXE%"

"%PYTHON_EXE%" "%~dp0launcher.py"
exit /b %errorlevel%
