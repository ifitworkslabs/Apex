@echo off
setlocal EnableDelayedExpansion
title Apex LocalAI & Docker WSL2 Disk Space Cleaner

echo ============================================================================
echo   APEX LOCALAI ^& DOCKER WSL2 DISK SPACE CLEANER
echo ============================================================================
echo   [*] Checking current Docker disk usage...
docker system df 2>nul
echo.
echo   [1] SAFE QUICK CLEAN (Recommended while working)
echo       - Removes stopped containers, anonymous volumes ^& Docker build cache
echo       - Removes aborted / 0-score trial folders in D:\LocalAI\frontierswe_jobs
echo       - Cleans temp npm caches (KEEPS your fast cline_nvm_v2.tar.gz cache!)
echo.
echo   [2] DEEP CLEAN (Reclaim 15-100+ GB of Task Docker Images ^& Old Jobs)
echo       - Everything in [1] PLUS removes unused task Docker images
echo         (Harbor will re-pull only the active task image when needed)
echo.
echo   [3] COMPACT WSL2 VIRTUAL DISK (Shrink docker_data.vhdx on C: drive)
echo       - Runs Safe Clean, shuts down WSL2, and runs Windows DiskPart
echo         `compact vdisk` on docker_data.vhdx / ext4.vhdx to return free GBs
echo         back to Windows C: drive immediately.
echo ============================================================================
set /p c="Select option [1, 2, or 3, Default=1]: "
if "%c%"=="" set "c=1"

echo.
echo [*] Step 1: Pruning stopped Docker containers, dangling volumes ^& build cache...
docker container prune -f 2>nul
docker volume prune -f 2>nul
docker builder prune -f 2>nul

echo [*] Step 2: Cleaning redundant temp npm caches in D:\LocalAI\harbor_cache...
REM Keep D:\LocalAI\harbor_cache\tarball\cline_nvm_v2.tar.gz and apt\, remove unpacked npm_* and nvm\
if exist "D:\LocalAI\harbor_cache\nvm" (
    rmdir /s /q "D:\LocalAI\harbor_cache\nvm" 2>nul
)
if exist "D:\LocalAI\harbor_cache\npm" (
    rmdir /s /q "D:\LocalAI\harbor_cache\npm" 2>nul
)
for /d %%D in ("D:\LocalAI\harbor_cache\npm_*") do (
    rmdir /s /q "%%~fD" 2>nul
)
if exist "D:\LocalAI\docker_backend.log" del /f /q "D:\LocalAI\docker_backend.log" 2>nul
if exist "D:\LocalAI\container_setup_live.log" del /f /q "D:\LocalAI\container_setup_live.log" 2>nul

echo [*] Step 3: Removing crashed / 0-score trial folders in D:\LocalAI\frontierswe_jobs...
D:\Python\python.exe -c "import shutil, json; from pathlib import Path; jobs = Path(r'D:\LocalAI\frontierswe_jobs'); removed = 0; [ (shutil.rmtree(j, ignore_errors=True), print('  [-] Removed crashed/empty job:', j.name)) for j in (jobs.iterdir() if jobs.exists() else []) if j.is_dir() and not list(j.rglob('reward.json')) and not list(j.rglob('reward.txt')) ]" 2>nul

if "%c%"=="1" goto DONE

if "%c%"=="2" (
    echo.
    echo [*] Step 4 [DEEP CLEAN]: Pruning all unused Docker images ^& volumes...
    docker system prune -a --volumes -f
    goto DONE
)

if "%c%"=="3" (
    echo.
    echo [*] Step 4 [VHDX COMPACT]: Pruning unused Docker objects before compacting...
    docker system prune --volumes -f 2>nul
    echo [*] Shutting down Docker / WSL2 so Windows can compact the virtual disk...
    taskkill /F /IM "com.docker.backend.exe" >nul 2>&1
    taskkill /F /IM "Docker Desktop.exe" >nul 2>&1
    wsl --shutdown
    timeout /t 3 /nobreak >nul

    set "VHDX_FOUND=0"
    for %%V in (
        "%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx"
        "%LOCALAPPDATA%\Docker\wsl\data\ext4.vhdx"
        "%LOCALAPPDATA%\Docker\wsl\main\ext4.vhdx"
    ) do (
        if exist "%%~V" (
            set "VHDX_FOUND=1"
            echo [*] Compacting WSL2 virtual disk: %%~V
            echo     (This may take 1-3 minutes depending on disk size...)
            (
                echo select vdisk file="%%~V"
                echo attach vdisk readonly
                echo compact vdisk
                echo detach vdisk
                echo exit
            ) > "%TEMP%\apex_compact_vhdx.txt"
            diskpart /s "%TEMP%\apex_compact_vhdx.txt"
            del /f /q "%TEMP%\apex_compact_vhdx.txt" 2>nul
        )
    )
    if "!VHDX_FOUND!"=="0" (
        echo [!] Could not find docker_data.vhdx in %LOCALAPPDATA%\Docker\wsl\.
    )
    goto DONE
)

:DONE
echo.
echo [+] Disk cleanup complete! Current Docker disk usage:
docker system df 2>nul
echo.
pause
