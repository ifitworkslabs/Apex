@echo off
title FrontierSWE v2 Benchmark Suite
color 0B
echo ============================================================================
echo                     FRONTIERSWE V2 BENCHMARK SUITE
echo ============================================================================
echo   [1] Run All 34 Tasks - Token-Rate Scaled (Fair Gemini 4 Argon 5.3x Ratio) [RECOMMENDED]
echo   [2] Run All 34 Tasks - Strict Token-Capped (Max 250,000 Tokens per task)
echo   [3] Run All 34 Tasks - Standard Time-Budgeted (Fixed 20-Hour Clock per task)
echo   [4] Run a Single Specific Task
echo   [5] Check / List All 34 Tasks and Current Scoreboard
echo ============================================================================
set /p choice="Select option [1-5]: "

if "%choice%"=="1" (
    echo Starting 34-Task Suite with Token-Rate Scaled Ratio (Gemini 4 Argon Fair Scale)...
    python D:\LocalAI\run_frontierswe_v2.py --all --variant token-scaled
    goto END
)
if "%choice%"=="2" (
    echo Starting 34-Task Suite with Strict Token Cap (250,000 tokens)...
    python D:\LocalAI\run_frontierswe_v2.py --all --variant token-capped --token-cap 250000
    goto END
)
if "%choice%"=="3" (
    echo Starting 34-Task Suite with Standard Time Budget (Official 72,000s)...
    python D:\LocalAI\run_frontierswe_v2.py --all --variant time
    goto END
)
if "%choice%"=="4" (
    set /p taskname="Enter exact task name (e.g. astronomy-toolkit): "
    python D:\LocalAI\run_frontierswe_v2.py --task %taskname% --variant token-scaled
    goto END
)
if "%choice%"=="5" (
    python D:\LocalAI\run_frontierswe_v2.py --list
    goto END
)

:END
pause
