@echo off
rem  Double-click this file to use Mindboard.
rem  First run sets everything up; after that it just opens the menu.
title Mindboard
chcp 65001 >nul
cd /d "%~dp0"

set "MB=.venv\Scripts\mindboard.exe"

rem  Set up when Mindboard is missing - or present but unable to run, as a
rem  half-finished update leaves it. Present is not the same as working.
set "READY=0"
if exist "%MB%" (
  "%MB%" version >nul 2>&1 && set "READY=1"
)
if "%READY%"=="0" (
  echo.
  echo   Setting Mindboard up. This takes a minute.
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  "%MB%" version >nul 2>&1 || (
    echo.
    echo   Setup did not finish - the messages above say why.
    echo.
    pause
    exit /b 1
  )
)

"%MB%" menu

if errorlevel 1 (
  echo.
  echo   Mindboard closed with an error.
  pause
)
