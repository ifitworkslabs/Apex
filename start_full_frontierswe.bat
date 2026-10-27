@echo off
title FrontierSWE v2 Standardized Benchmark Suite
color 0B

set "PY_CMD=python"
if exist "D:\Python\python.exe" set "PY_CMD=D:\Python\python.exe"

:MENU
echo ============================================================================
echo              FRONTIERSWE V2 BENCHMARK SUITE - FAIR AND STANDARDIZED
echo ============================================================================
echo   [1] Run All 34 Tasks - Standard Fair Mode [150k Output Tok / 8M Total / 600 Steps]
echo   [2] Run All 34 Tasks - Hardware Token-Rate Scaled [Live tok/s vs Cloud 100 t/s]
echo   [3] Run All 34 Tasks - Official 20-Hour Wall-Clock Mode [72,000s per task]
echo   [4] Run a Single Specific Task [Standard Fair Mode]
echo   [5] Check / List All 34 Tasks and Current Verifier Scoreboard
echo   [6] Clean Corrupted / 1.7s Infra-Crash Entries from Scoreboard
echo ============================================================================
set "choice="
set /p choice="Select option [1-6]: "

if "%choice%"=="1" goto OPT_FAIR
if "%choice%"=="2" goto OPT_SCALED
if "%choice%"=="3" goto OPT_OFFICIAL
if "%choice%"=="4" goto OPT_SINGLE
if "%choice%"=="5" goto OPT_LIST
if "%choice%"=="6" goto OPT_CLEAN

echo.
echo [!] Invalid selection "%choice%". Please enter a number from 1 to 6.
echo.
goto MENU

:OPT_FAIR
echo.
echo [*] Starting 34-Task Suite in Standard Fair Mode [150,000 Output Tokens / 600 Steps]...
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --all --variant standard-fair --max-output-tokens 150000 --max-total-tokens 8000000 --max-steps 600
goto END

:OPT_SCALED
echo.
echo [*] Starting 34-Task Suite with Hardware-Normalized Token-Rate Scaling...
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --all --variant token-scaled
goto END

:OPT_OFFICIAL
echo.
echo [*] Starting 34-Task Suite with Official 20-Hour Wall-Clock Budget [72,000s]...
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --all --variant official-time
goto END

:OPT_SINGLE
echo.
set "taskname="
set /p taskname="Enter exact task name [e.g. cranelift-codegen-opt]: "
if "%taskname%"=="" goto MENU
echo [*] Starting single task: %taskname% ...
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --task "%taskname%" --variant standard-fair
goto END

:OPT_LIST
echo.
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --list
echo.
pause
goto MENU

:OPT_CLEAN
echo.
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --clean-scoreboard
"%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --list
echo.
pause
goto MENU

:END
echo.
echo ============================================================================
echo   Benchmark command finished. Press any key to close...
echo ============================================================================
pause
