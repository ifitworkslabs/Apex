@echo off
title Apex LocalAI Full Environment Re-Setup Wizard
color 0B

set "PY_CMD=python"
if exist "D:\Python\python.exe" set "PY_CMD=D:\Python\python.exe"

echo ============================================================================
echo               APEX LOCAL AI - AUTOMATED RE-SETUP WIZARD
echo ============================================================================
echo This script reconstructs D:\LocalAI from scratch, including:
echo   1. Directory structure [models, llama, frontier_swe_v2, skills, jobs]
echo   2. Python dependencies [harbor, fastapi, uvicorn, huggingface_hub^<2.0]
echo   3. FrontierSWE v2 34-task repository [Proximal-Labs/frontier-swe-v2]
echo   4. Curated FrontierSWE v2 and Ghidra Skills [via skill_manager.py]
echo   5. Official llama.cpp CUDA binaries + GGUF models from Hugging Face
echo ============================================================================
echo.

if not exist "D:\LocalAI" mkdir "D:\LocalAI"
if not exist "D:\LocalAI\models" mkdir "D:\LocalAI\models"
if not exist "D:\LocalAI\llama" mkdir "D:\LocalAI\llama"
if not exist "D:\LocalAI\frontierswe_jobs" mkdir "D:\LocalAI\frontierswe_jobs"
if not exist "D:\LocalAI\skill_staging" mkdir "D:\LocalAI\skill_staging"
if not exist "D:\LocalAI\ghidra_scripts" mkdir "D:\LocalAI\ghidra_scripts"

cd /d "D:\LocalAI"

echo [*] Step 1/5: Installing required Python packages...
"%PY_CMD%" -m pip install --upgrade pip fastapi uvicorn "huggingface-hub>=0.34.0,<2.0"
where uv >nul 2>nul
if %errorlevel%==0 goto INSTALL_HARBOR_UV
"%PY_CMD%" -m pip install --upgrade harbor "huggingface-hub>=0.34.0,<2.0"
goto STEP2

:INSTALL_HARBOR_UV
uv tool install harbor --upgrade

:STEP2
echo.
echo [*] Step 2/5: Cloning FrontierSWE v2 tasks repository into D:\LocalAI\frontier_swe_v2...
if exist "D:\LocalAI\frontier_swe_v2\tasks" goto CHECK_PXEVAL
if exist "D:\LocalAI\frontier_swe_v2" rmdir /s /q "D:\LocalAI\frontier_swe_v2"
git clone https://github.com/Proximal-Labs/frontier-swe-v2.git "D:\LocalAI\frontier_swe_v2"

:CHECK_PXEVAL
if exist "D:\LocalAI\px-eval\.git" goto STEP3
if exist "D:\LocalAI\px-eval" rmdir /s /q "D:\LocalAI\px-eval"
git clone https://github.com/Proximal-Labs/px-eval.git "D:\LocalAI\px-eval"

:STEP3
echo.
echo [*] Step 3/5: Bootstrapping 7 Curated FrontierSWE v2 Skills and Clean Scoreboard...
if exist "D:\LocalAI\skill_manager.py" "%PY_CMD%" "D:\LocalAI\skill_manager.py"
if exist "D:\LocalAI\run_frontierswe_v2.py" "%PY_CMD%" "D:\LocalAI\run_frontierswe_v2.py" --clean-scoreboard

echo.
echo [*] Step 4/5: Installing llama.cpp Windows CUDA binaries into D:\LocalAI\llama...
if exist "D:\LocalAI\install_llama_server.py" "%PY_CMD%" "D:\LocalAI\install_llama_server.py"

echo.
echo ============================================================================
echo   Step 5/5: DOWNLOAD GGUF MODELS TO D:\LocalAI\models\
echo ============================================================================
echo   [1] Download Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf [~17.7 GB]
echo   [2] Download Qwen3.5-27B-UD-Q3_K_XL.gguf                  [~14.4 GB]
echo   [3] Download BOTH Models
echo   [4] Skip Model Download [Already downloaded]
echo ============================================================================
set /p mchoice="Select option [1-4]: "

if "%mchoice%"=="1" goto DL_MOE
if "%mchoice%"=="2" goto DL_DENSE
if "%mchoice%"=="3" goto DL_BOTH
goto DONE_SETUP

:DL_MOE
echo [*] Downloading Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf to D:\LocalAI\models ...
"%PY_CMD%" -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF', filename='Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf', local_dir=r'D:\LocalAI\models')"
goto DONE_SETUP

:DL_DENSE
echo [*] Downloading Qwen3.5-27B-UD-Q3_K_XL.gguf to D:\LocalAI\models ...
"%PY_CMD%" -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='unsloth/Qwen3.5-27B-GGUF', filename='Qwen3.5-27B-UD-Q3_K_XL.gguf', local_dir=r'D:\LocalAI\models')"
goto DONE_SETUP

:DL_BOTH
echo [*] Downloading Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf to D:\LocalAI\models ...
"%PY_CMD%" -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF', filename='Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf', local_dir=r'D:\LocalAI\models')"
echo [*] Downloading Qwen3.5-27B-UD-Q3_K_XL.gguf to D:\LocalAI\models ...
"%PY_CMD%" -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='unsloth/Qwen3.5-27B-GGUF', filename='Qwen3.5-27B-UD-Q3_K_XL.gguf', local_dir=r'D:\LocalAI\models')"

:DONE_SETUP
echo.
echo ============================================================================
echo   [+] RE-SETUP COMPLETE!
echo   Next steps:
echo     1. Run D:\LocalAI\start_brain.bat [choose Option 1]
echo     2. Run D:\LocalAI\start_dashboard.bat [optional live web UI]
echo     3. Run D:\LocalAI\start_full_frontierswe.bat [choose Option 1]
echo ============================================================================
pause
