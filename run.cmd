@echo off
setlocal
cd /d "%~dp0"

REM Double-click friendly launcher for Windows.
REM Uses PowerShell with ExecutionPolicy Bypass so it works on most machines.
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0run.ps1"
