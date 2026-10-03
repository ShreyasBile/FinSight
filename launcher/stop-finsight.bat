@echo off
REM Stops the FinSight web app and API started by start-finsight.bat.
title FinSight AI - stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-finsight.ps1"
