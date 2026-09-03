@echo off
REM ============================================================================
REM  Simple Deck - build Windows (opakowanie .bat na build.ps1)
REM  Buduje domyślnie OBA instalatory: .exe (Inno Setup) + .msi (WiX).
REM
REM  Użycie:
REM    build.bat            - pełny build (.exe + .msi)
REM    build.bat clean      - clean + pełny build
REM    build.bat noexe      - pomiń Inno Setup (tylko .msi)
REM    build.bat nomsi      - pomiń WiX (tylko .exe, stare zachowanie)
REM    build.bat noinno     - alias noexe (wsteczna kompatybilność)
REM
REM  Argumenty można łączyć (kolejność dowolna), np.:
REM    build.bat clean nomsi
REM ============================================================================

setlocal enabledelayedexpansion

set ARGS=
call :parse_arg %1
call :parse_arg %2
call :parse_arg %3
call :parse_arg %4

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %ARGS%
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% neq 0 (
    echo.
    echo [X] Build nieudany ^(exit %EXITCODE%^)
) else (
    echo.
    echo [OK] Build zakonczony pomyslnie
)

exit /b %EXITCODE%

:parse_arg
if /I "%~1"=="clean"  set ARGS=%ARGS% -Clean
if /I "%~1"=="noexe"  set ARGS=%ARGS% -SkipExe
if /I "%~1"=="nomsi"  set ARGS=%ARGS% -SkipMsi
if /I "%~1"=="noinno" set ARGS=%ARGS% -SkipInno
goto :eof
