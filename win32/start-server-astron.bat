@echo off
title Toontown Apocalypse: Astron Launcher
cd ..\astron

:main
    astrond.exe --loglevel info config/astrond.yml
    pause
goto main
