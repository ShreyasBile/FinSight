@echo off
REM One-click start for FinSight AI (this is what the desktop shortcut runs).
REM Extra options are passed through, e.g.:  start-finsight.bat -NoBrowser
title FinSight AI launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-finsight.ps1" %*
if errorlevel 1 pause
