@echo off
REM ============================================================================
REM  Simple Deck - uruchamianie deweloperskie (Windows)
REM  ----------------------------------------------------------------------------
REM  Jedna komenda zrobi wszystko:
REM    1) stworzy .venv jesli brakuje (jednorazowo)
REM    2) zainstaluje zaleznosci z pyproject.toml (jednorazowo, ~60 s)
REM    3) uruchomi aplikacje
REM
REM  Uzycie:
REM    run.bat                - normalny start (laczy z MCU)
REM    run.bat --demo         - tryb demo (bez urzadzenia)
REM    run.bat --verbose      - debug logging
REM    run.bat --help
REM
REM  Idempotentny: drugie uruchomienie pomija setup i startuje w ~1 s.
REM
REM  m13 fix: caly plik w ASCII (CMD cp852 - brak polskich znakow w helpie)
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"

REM ---- Help ----
if /I "%~1"=="--help" goto :help
if /I "%~1"=="-h"     goto :help
goto :skip_help
:help
echo Simple Deck - uruchamianie
echo.
echo Uzycie:
echo   run.bat                - normalny start (laczy z MCU)
echo   run.bat --demo         - tryb demo (bez urzadzenia)
echo   run.bat --verbose      - debug logging
echo   run.bat --help         - ta pomoc
echo.
echo Flagi sa przekazywane do aplikacji, mozna je laczyc:
echo   run.bat --demo --verbose
exit /b 0
:skip_help

REM ---- Krok 1: venv (jednorazowo) ----
if not exist "%PYTHON%" (
    echo [setup] Tworzenie virtualenv .venv ...
    where python >nul 2>&1
    if errorlevel 1 (
        echo BLAD: python nie znaleziony w PATH. Zainstaluj Python 3.10+ z https://python.org
        exit /b 1
    )
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo BLAD: nie udalo sie utworzyc venv
        exit /b 1
    )
    "%PYTHON%" -m pip install --quiet --upgrade pip wheel setuptools
    if errorlevel 1 (
        echo BLAD: nie udalo sie zaktualizowac pip
        exit /b 1
    )
    echo [setup]   venv gotowy
)

REM ---- Krok 2: instalacja zaleznosci (idempotentnie) ----
REM UWAGA: pip package "hidapi" importuje sie jako modul `hid`
"%PYTHON%" -c "import PySide6, hid" >nul 2>&1
if errorlevel 1 (
    echo [setup] Instalacja zaleznosci - jednorazowo, potrwa ~60 s ...
    echo [setup]   wykryto Windows - instalacja z extras [windows]
    "%PYTHON%" -m pip install -e ".[windows]"
    if errorlevel 1 (
        echo BLAD: instalacja zaleznosci nie powiodla sie
        exit /b 1
    )
    echo [setup]   zaleznosci gotowe
)

REM ---- Krok 3: uruchom aplikacje ----
echo [run] Simple Deck - start
"%PYTHON%" -m simple_deck %*
set "EXITCODE=%ERRORLEVEL%"
exit /b %EXITCODE%
