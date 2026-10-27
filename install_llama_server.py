"""
=============================================================================
  APEX LLAMA.CPP WINDOWS CUDA AUTO-INSTALLER (install_llama_server.py)
=============================================================================
Automatically downloads and extracts the official prebuilt Windows x64 CUDA
(or Vulkan fallback) binaries and CUDA runtime DLLs (`cudart`, `cublas`) from
`ggml-org/llama.cpp` GitHub Releases directly into `D:\\LocalAI\\llama\\`.

Features:
  - Scans the latest 5 releases on `ggml-org/llama.cpp` so if the very latest
    commit tag is still compiling Windows CUDA binaries on GitHub Actions, it
    automatically picks the most recent release that has complete binaries.
  - Detects the system's maximum supported CUDA version via `nvidia-smi`
    (selecting CUDA 12.x or CUDA 13.x accordingly).
  - Flattens any nested archive folders so `llama-server.exe` / `llama.exe` and
    all `*.dll` files sit directly inside `D:\\LocalAI\\llama\\`.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
LLAMA_DIR = BASE_DIR / "llama"


def detect_cuda_major_version() -> int:
    """Runs `nvidia-smi` to detect the maximum CUDA major version supported by the driver."""
    try:
        res = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", res.stdout)
            if m:
                major = int(m.group(1))
                print(f"[*] Detected NVIDIA Driver supporting up to CUDA {major}.{m.group(2)}")
                return major
    except Exception:
        pass
    print("[*] Defaulting to CUDA 12.x compatible build.")
    return 12


def fetch_recent_releases(limit: int = 8) -> List[Dict[str, Any]]:
    url = f"https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page={limit}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Apex-LocalAI-Installer/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def select_best_assets(releases: List[Dict[str, Any]], cuda_major: int) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Finds the newest release that has both the Windows CUDA binary zip and
    matching cudart zip (or Vulkan zip as fallback).
    """
    # Prefer CUDA 12.x first for broad driver compatibility unless only 13.x exists and driver >= 13
    preferred_patterns = ["cuda-12", "cuda-13"] if cuda_major < 13 else ["cuda-12", "cuda-13"]

    for rel in releases:
        tag = rel.get("tag_name", "unknown")
        assets = rel.get("assets", [])
        if not assets:
            continue

        for pat in preferred_patterns:
            bin_asset = None
            rt_asset = None
            for a in assets:
                name = a.get("name", "").lower()
                if not name.endswith("x64.zip"):
                    continue
                if f"bin-win-{pat}" in name and not name.startswith("cudart"):
                    bin_asset = a
                elif name.startswith("cudart-llama-bin-win-") and pat in name:
                    rt_asset = a
            if bin_asset:
                return tag, bin_asset, rt_asset

        # Fallback: check for Vulkan x64 build if no CUDA build found in this release
        for a in assets:
            name = a.get("name", "").lower()
            if "bin-win-vulkan-x64.zip" in name:
                return tag, a, None

    return None, None, None


def download_file(url: str, dest: Path) -> None:
    print(f"[*] Downloading: {dest.name}")
    print(f"    URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Apex-LocalAI-Installer/2.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        downloaded = 0
        chunk_size = 1024 * 256
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = (downloaded / total) * 100
                mb_done = downloaded / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                print(f"\r    Progress: {mb_done:6.1f} / {mb_tot:6.1f} MB ({pct:5.1f}%)", end="", flush=True)
    print("\n    [+] Download complete.")


def extract_flat(zip_path: Path, target_dir: Path) -> None:
    """Extracts all files from `zip_path` into `target_dir`, flattening top-level folders if needed."""
    tmp_extract = target_dir / "_tmp_extract"
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract, ignore_errors=True)
    tmp_extract.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_extract)

    # Move all extracted files (.exe, .dll, etc.) directly into target_dir
    for item in tmp_extract.rglob("*"):
        if item.is_file():
            dest = target_dir / item.name
            shutil.copy2(item, dest)

    shutil.rmtree(tmp_extract, ignore_errors=True)


def ensure_llama_installed() -> bool:
    LLAMA_DIR.mkdir(parents=True, exist_ok=True)
    server_exe = LLAMA_DIR / "llama-server.exe"
    unified_exe = LLAMA_DIR / "llama.exe"

    if server_exe.exists() or unified_exe.exists():
        found = server_exe if server_exe.exists() else unified_exe
        print(f"[+] Found llama binary: {found}")
        return True

    print(f"[*] Fetching latest ggml-org/llama.cpp Windows CUDA release into {LLAMA_DIR}...")
    cuda_major = detect_cuda_major_version()
    try:
        releases = fetch_recent_releases(limit=10)
    except Exception as e:
        print(f"[!] Failed to query GitHub API: {e}")
        return False

    tag, bin_asset, rt_asset = select_best_assets(releases, cuda_major)
    if not bin_asset:
        print("[!] Could not find a matching Windows x64 binary asset in recent releases.")
        return False

    print(f"[+] Selected release {tag}: {bin_asset['name']}")
    if rt_asset:
        print(f"[+] Selected CUDA runtime DLLs: {rt_asset['name']}")

    try:
        if rt_asset:
            rt_zip = LLAMA_DIR / rt_asset["name"]
            download_file(rt_asset["browser_download_url"], rt_zip)
            extract_flat(rt_zip, LLAMA_DIR)
            rt_zip.unlink(missing_ok=True)

        bin_zip = LLAMA_DIR / bin_asset["name"]
        download_file(bin_asset["browser_download_url"], bin_zip)
        extract_flat(bin_zip, LLAMA_DIR)
        bin_zip.unlink(missing_ok=True)
    except Exception as e:
        print(f"[!] Error downloading/extracting llama.cpp binaries: {e}")
        return False

    if server_exe.exists() or unified_exe.exists():
        print(f"[+] Successfully installed llama.cpp ({tag}) into {LLAMA_DIR}!")
        return True

    print("[!] Warning: Archive extracted, but neither llama-server.exe nor llama.exe was found.")
    return False


if __name__ == "__main__":
    ok = ensure_llama_installed()
    sys.exit(0 if ok else 1)