@echo off
rem  Double-click this file to use Mindboard.
rem  First run sets everything up; after that it just opens the menu.
title Mindboard
chcp 65001 >nul
cd /d "%~dp0"

set "MB=.venv\Scripts\mindboard.exe"

if not exist "%MB%" (
  echo.
  echo   Setting Mindboard up for the first time. This takes a minute.
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if not exist "%MB%" (
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
