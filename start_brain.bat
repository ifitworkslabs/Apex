@echo off
title Local SOTA AI Brain Launcher
echo ============================================================================
echo   SELECT YOUR LOCAL AI ENGINE:
echo   [1] Qwen3-Coder-30B-A3B MoE (18.9 t/s FAST + Cache Reuse)
echo   [2] Qwen3.5-27B Dense       (4.5 t/s DEEP DENSE + Auto-Fit VRAM)
echo ============================================================================
set /p choice="Enter 1 or 2: "
if "%%choice%%"=="1" goto MOE_ENGINE
if "%%choice%%"=="2" goto DENSE_ENGINE
:MOE_ENGINE
D:\LocalAI\llama\llama-server.exe -m D:\LocalAI\models\Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf -c 65536 -ngl 999 -ncmoe 36 -fit off -fa on -ctk q8_0 -ctv q8_0 --temp 0.1 --min-p 0.05 -t 6 -b 1024 -ub 512 -np 1 --cache-reuse 256 --host 0.0.0.0 --port 8080 --jinja
goto END
:DENSE_ENGINE
D:\LocalAI\llama\llama-server.exe -m D:\LocalAI\models\Qwen3.5-27B-UD-Q3_K_XL.gguf -c 32768 -fit on -fa on -ctk q8_0 -ctv q8_0 --temp 0.1 --min-p 0.05 -t 6 -b 1024 -ub 512 -np 1 --host 0.0.0.0 --port 8080 --jinja
:END
pause
