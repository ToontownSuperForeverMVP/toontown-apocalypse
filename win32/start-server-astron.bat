@echo off
title Super CFO: Astron Launcher
cd ..\astron

:main
    astrond.exe --loglevel info config/astrond.yml
    pause
goto main
