@echo off
REM =============================================================================
REM  LoopCoder — HuggingFace model download launcher (Windows .bat)
REM =============================================================================
REM
REM Wraps Download-Model.ps1 so non-PowerShell users can double-click it. By
REM default this downloads the FULL B300 model (~480 GB) into D:\loopcoder\models.
REM Edit the variables below before launching, or pass them on the command line:
REM
REM   Download-Model.bat
REM   Download-Model.bat tiny
REM   Download-Model.bat custom Qwen/Qwen2.5-Coder-1.5B-Instruct D:\my\out
REM
REM =============================================================================

setlocal ENABLEDELAYEDEXPANSION

set "DEFAULT_MODEL=Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"
set "TINY_MODEL=Qwen/Qwen2.5-Coder-0.5B-Instruct"
set "DEFAULT_OUTDIR=D:\loopcoder\models"

set "MODE=%~1"
set "ARG2=%~2"
set "ARG3=%~3"

if /I "%MODE%"=="tiny" (
    set "MODEL_ID=%TINY_MODEL%"
    set "OUTDIR=%DEFAULT_OUTDIR%"
) else if /I "%MODE%"=="custom" (
    if "%ARG2%"==""  ( echo Need MODEL_ID for 'custom' mode & exit /b 2 )
    if "%ARG3%"==""  ( echo Need OUTDIR for 'custom' mode & exit /b 2 )
    set "MODEL_ID=%ARG2%"
    set "OUTDIR=%ARG3%"
) else (
    set "MODEL_ID=%DEFAULT_MODEL%"
    set "OUTDIR=%DEFAULT_OUTDIR%"
)

set "PS1=%~dp0Download-Model.ps1"

echo.
echo ===== LoopCoder model downloader =====
echo  Model:  %MODEL_ID%
echo  Output: %OUTDIR%
echo.
echo (Defender exclusion + sleep override require admin; UAC may pop up.)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -ModelId "%MODEL_ID%" -OutDir "%OUTDIR%"

set "RC=%ERRORLEVEL%"
echo.
echo Exited with code %RC%.
pause
exit /b %RC%
