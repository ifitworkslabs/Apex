"""
=============================================================================
  FRONTIERSWE V2 BENCHMARK RUNNER & EVALUATION HARNESS (v3.0 - FIXED)
=============================================================================
Orchestrates FrontierSWE v2 evaluations with Harbor, Cline, and local LLM.

Key Fixes & Enhancements in v3.0:
  1. Fixes `RuntimeError: Command timed out after 2400 seconds` during Agent Run:
     - In v2.8/v2.9, `_apex_agent_exec` had `if kw["timeout_sec"] is None: kw["timeout_sec"] = 2400`,
       which overwrote `timeout_sec=None` (unlimited command duration during `ClineCli.run()`)
       with a 2,400-second (40-minute) cutoff in `docker.py` line 782.
     - In v3.0, `timeout_sec=None` is NEVER overwritten—only explicit setup commands
       with `0 < timeout_sec < 2400` are raised to 2,400s, allowing `ClineCli.run()`
       to run for the full 20-hour / 150,000-output-token budget and hand off cleanly
       to the Harbor Verifier.
  2. Real-Time JSON Trajectory Mirroring & Live Step Tracking:
     - Because `ClineCli.run()` executes via `docker compose exec` -> `_apex_read_stream`,
       all live Cline JSON stream events (`iteration_start`, `content_delta`, `tool`,
       `usage`) flow into `D:\\LocalAI\\container_setup_live.log`.
     - `FairBudgetWatchdog` now separates setup lines from live Cline JSON events,
       mirroring JSON events into `<trial_dir>/agent/cline.txt` in real time so
       `web_dashboard.py` (http://localhost:8088) and `live_monitor.py` stream every
       thought, tool call, and token update live while the agent works, and printing
       live `[Agent Iter N]` progress lines in the terminal.
  3. Retains v2.9 Fixes:
     - `extra_hosts: ["host.docker.internal:host-gateway"]` + no custom `dns` override.
     - Per-UID npm cache (`/tmp/apex_cache/npm_$(id -u)`) + `$HOME/.nvm` SSD tarball
       restored with `tar --no-same-owner`.
"""

import os
import re
import sys
import site
import json
import time
import socket
import shutil
import urllib.request
import argparse
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

RUNNER_VERSION = "v3.3 (5M Editor + Optional old_text + Bash Heredoc Fix + Solver Guidance)"

# Enforce UTF-8 on Windows Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
TASKS_DIR = BASE_DIR / "frontier_swe_v2" / "tasks"
JOBS_DIR = BASE_DIR / "frontierswe_jobs"
SCOREBOARD_FILE = BASE_DIR / "frontierswe_scoreboard.json"
ACTIVE_TASK_FILE = BASE_DIR / "current_active_task.json"
DOCKER_LOG_FILE = BASE_DIR / "docker_backend.log"
LIVE_SETUP_LOG = BASE_DIR / "container_setup_live.log"

# Import task-aware skill router and Harbor CLI flag detector
try:
    from skill_manager import (
        build_harbor_skill_flags,
        select_skills_for_task,
        get_supported_harbor_flags,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skill_manager import (
        build_harbor_skill_flags,
        select_skills_for_task,
        get_supported_harbor_flags,
    )

# 34 Official FrontierSWE v2 Tasks
FRONTIER_TASKS = [
    "astronomy-toolkit",
    "cranelift-codegen-opt",
    "crash-proof-flash-filesystem",
    "dart-style-in-haskell",
    "ffmpeg-libswscale-optimization",
    "fitness-recap-video-in-remotion",
    "flight-sim-renderer-in-opengl",
    "frogsgame-post-training",
    "git-to-zig",
    "granite-mamba2-inference-optimization",
    "higgs-uncertainty-inference",
    "kolmogorov-audio-compression",
    "lean-4-kernel-type-checker-in-pascal",
    "libexpat-optimization",
    "lua-native-compiler",
    "machine-learned-interatomic-potential",
    "medium-range-weather-forecast",
    "meg-speech-decoding",
    "msms-denovo-generation",
    "multi-gpu-efficient-finetuning",
    "notebook-compression",
    "optimizer-design",
    "postgresql-18-on-sqlite",
    "quantum-espresso-pwx-in-rust",
    "qubit-routing",
    "reconnaissance-blind-chess-recovery",
    "sglang-inference-system-optimization",
    "snooker-prediction",
    "spice-circuit-simulator-in-rust",
    "stepper-music-sequencer-gba",
    "synthetic-music-diarization",
    "verilog-simulator-in-swift",
    "vision-only-torcs-racing-bot",
    "wan-2.1-on-max-mojo",
]

_VERIFIED_CONTAINER_LLM_URL: Optional[str] = None
_CACHED_DOCKER_LIMITS: Optional[Tuple[int, int]] = None
_DOCKER_LOG_HANDLE = None

APEX_HOOK_CODE = r'''r"""
APEX HARBOR RUNTIME INTERCEPTOR (sitecustomize.py v3.3)
1. Preserves timeout_sec=None (unlimited duration) during ClineCli.run() so
   docker.py never kills the agent at 2400s with RuntimeError.
2. Patches Cline CLI's minified JS bundle (using os.walk + shutil.which("cline")):
   - Raises `ufe=6e3` (the 6,000-char `editor` limit from cline/cline#13263) to `5e6`
     (5,000,000 chars) across the entire bundle AND neutralizes the `Editor input too large`
     validator so 600+ line `editor` calls succeed on the first try.
   - Patches the `already exists, but old_text was omitted` check (cline/cline#14397)
     so omitting `old_text` on an existing stub file overwrites it cleanly.
3. Wraps `/bin/bash` inside the container to auto-insert a missing newline after
   `<< 'EOF'` (fixing `cat > file.py << 'EOF'#!/usr/bin/env python3`).
4. Injects active task skill instructions from `~/.cline/skills/*/SKILL.md`
   directly into `ClineCli.run(instruction)` at Step #1.
5. Streams setup & live agent JSON output via Process._read_stream to
   D:\LocalAI\container_setup_live.log.
"""
import os
import re
import sys
import json
import asyncio
import asyncio.subprocess
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
MAX_CPUS = int(os.environ.get("APEX_MAX_DOCKER_CPUS", "4") or "4")
MAX_MEM_MB = int(os.environ.get("APEX_MAX_DOCKER_MEM_MB", "12288") or "12288")
CONTAINER_LLM_URL = os.environ.get("APEX_CONTAINER_LLM_URL", "http://host.docker.internal:8080/v1")
HOST_IPV4 = os.environ.get("APEX_HOST_IPV4", "")
LIVE_SETUP_LOG = BASE_DIR / "container_setup_live.log"
SETUP_TIMEOUT_SEC = 2400.0
ACTIVE_CLINE_SKILLS_DIR = Path(os.environ.get("USERPROFILE", r"C:\Users\Administrator")) / ".cline" / "skills"

CLINE_BUNDLE_PATCH_SNIPPET = r"""
_patch_cline_editor_limit() {
    python3 -c '
import os, re, shutil

roots = set()
nvm_root = os.path.expanduser("~/.nvm")
if os.path.isdir(nvm_root):
    roots.add(nvm_root)
cline_bin = shutil.which("cline")
if cline_bin:
    real_bin = os.path.realpath(cline_bin)
    roots.add(os.path.dirname(real_bin))
    roots.add(os.path.dirname(os.path.dirname(real_bin)))

for r in list(roots):
    for dirpath, _, filenames in os.walk(r):
        for fn in filenames:
            if not (fn.endswith((".js", ".mjs", ".cjs")) or fn == "cline"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) > 40_000_000:
                    continue
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    s = f.read()
                if "Editor input too large" not in s and "already exists, but" not in s:
                    continue
                s2 = s
                # 1. Patch hoisted limit variable (e.g. ufe=6e3 from cline/cline#13263) across entire file
                vars_found = set(
                    re.findall(r"exceeding the recommended limit of \$\{([A-Za-z0-9_$]+)\}", s2)
                    + re.findall(r"Keep this at or below \$\{([A-Za-z0-9_$]+)\} characters", s2)
                )
                for v in vars_found:
                    esc = re.escape(v)
                    s2 = re.sub(rf"\b{esc}\s*=\s*(?:6000|6_000|6e3)\b", f"{v}=5e6", s2)
                # 2. Neutralize Editor input too large return string so validator returns "" (falsy = valid)
                s2 = re.sub(
                    r"`Editor input too large:[^`]+`",
                    lambda m: "(\"\" && " + m.group(0) + ")",
                    s2,
                )
                # 3. Allow omitting old_text on existing files to take the create/overwrite branch
                def _patch_old_text(m):
                    chunk = m.group(0)
                    m_old = re.search(r"([A-Za-z0-9_$]+\.old_text|\bold_text\b)", chunk)
                    if m_old:
                        old_expr = m_old.group(1)
                        chunk = re.sub(
                            r"!\(\s*await\s+([A-Za-z0-9_$]+\([^)]+\))\s*\)",
                            rf"(!(await \1) || {old_expr} == null)",
                            chunk,
                            count=1,
                        )
                    return chunk
                s2 = re.sub(r".{0,1500}already exists, but \\?`old_text\\?` was omitted", _patch_old_text, s2, flags=re.DOTALL)
                if s2 != s:
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(s2)
                    print("[Apex Setup] Patched Cline editor limits (5M chars + optional old_text) in", p)
            except Exception:
                pass
' 2>/dev/null || true
}
"""


def _build_inline_skill_guidance() -> str:
    """Reads active SKILL.md files from ~/.cline/skills and formats them for Step #1 injection."""
    blocks = []
    try:
        if ACTIVE_CLINE_SKILLS_DIR.exists():
            for smd in sorted(ACTIVE_CLINE_SKILLS_DIR.glob("*/SKILL.md")):
                raw = smd.read_text(encoding="utf-8", errors="replace").strip()
                body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL).strip()
                if body:
                    blocks.append(body)
    except Exception:
        pass
    if not blocks:
        return ""
    joined = "\n\n".join(blocks)
    return (
        "\n\n[MANDATORY EXECUTION & DOMAIN PROTOCOL — FOLLOW STRICTLY]\n"
        f"{joined}\n"
        "[END OF MANDATORY PROTOCOL]\n"
    )


def _wrap_container_setup_script(script: str) -> str:
    if not isinstance(script, str) or "# APEX_FAST_SETUP_V33" in script:
        return script
    lower = script.lower()
    is_apt_step = ("apt-get update" in lower or "apt-get install" in lower) and "npm install -g cline" not in lower
    is_cline_npm_step = "npm install -g cline" in lower
    is_cline_run_step = (not is_apt_step and not is_cline_npm_step) and ("cline " in lower and ("--yolo" in lower or "--json" in lower or "openai" in lower or "local-model" in lower))

    if is_apt_step:
        apt_preamble = rf"""# APEX_FAST_SETUP_V33
mkdir -p /tmp/apex_cache/apt /tmp/apex_cache/tarball 2>/dev/null || true
chmod -R 777 /tmp/apex_cache 2>/dev/null || true
export DEBIAN_FRONTEND=noninteractive
if [ "$(id -u)" = "0" ]; then
    if ! getent hosts host.docker.internal >/dev/null 2>&1; then
        _gw_ip="$(ip route show default 2>/dev/null | awk '{{print $3}}' | head -n 1)"
        [ -z "$_gw_ip" ] && _gw_ip="{HOST_IPV4}"
        if [ -n "$_gw_ip" ]; then
            echo "$_gw_ip host.docker.internal" >> /etc/hosts 2>/dev/null || true
        fi
    fi
    # Install /bin/bash wrapper that auto-inserts missing newline after << 'EOF' (fixes wanted EOF#!/usr/bin/env)
    if [ -x /bin/bash ] && [ ! -f /bin/bash.real ]; then
        cp -p /bin/bash /bin/bash.real 2>/dev/null || true
        cat << ' EOWRAP' > /bin/bash.new
#!/bin/sh
if [ "$1" = "-c" ] || [ "$1" = "-lc" ]; then
    _fl="$1"
    _cm="$2"
    shift 2
    case "$_cm" in
        *EOF*)
            _cm="$(python3 -c 'import re, sys; s = sys.argv[1]; s = re.sub(r"(<<-?\s*(?:\x27EOF\x27|\"EOF\"|EOF))([^\n\r;>&|\s])", r"\1\n\2", s); print(s, end="")' "$_cm" 2>/dev/null || printf "%s" "$_cm")"
            ;;
    esac
    exec /bin/bash.real "$_fl" "$_cm" "$@"
fi
exec /bin/bash.real "$@"
 EOWRAP
        chmod 755 /bin/bash.new 2>/dev/null && mv -f /bin/bash.new /bin/bash 2>/dev/null || true
    fi
    rm -f /etc/apt/apt.conf.d/docker-clean 2>/dev/null || true
    mkdir -p /etc/apt/apt.conf.d 2>/dev/null || true
    printf 'Acquire::ForceIPv4 "true";\\nAcquire::Retries "3";\\nAcquire::http::Timeout "15";\\nAcquire::https::Timeout "15";\\nDir::Cache::archives "/tmp/apex_cache/apt";\\nAPT::Keep-Downloaded-Packages "true";\\n' > /etc/apt/apt.conf.d/99apex-fast 2>/dev/null || true
fi
"""
        apt_postamble = """
_apex_rc=$?
chmod -R 777 /tmp/apex_cache 2>/dev/null || true
exit $_apex_rc
"""
        return f"{apt_preamble}\n(\n{script}\n)\n{apt_postamble}"

    if is_cline_npm_step:
        nvm_preamble = rf"""# APEX_FAST_SETUP_V33
_uid="$(id -u)"
mkdir -p "/tmp/apex_cache/npm_${{_uid}}" /tmp/apex_cache/tarball 2>/dev/null || true
export DEBIAN_FRONTEND=noninteractive
export NODE_OPTIONS="--dns-result-order=ipv4first"
export NPM_CONFIG_CACHE="/tmp/apex_cache/npm_${{_uid}}"
export NPM_CONFIG_PREFER_OFFLINE="true"
export NPM_CONFIG_AUDIT="false"
export NPM_CONFIG_FUND="false"
export NPM_CONFIG_PROGRESS="false"
export NPM_CONFIG_FETCH_RETRIES="5"
export NPM_CONFIG_FETCH_RETRY_MINTIMEOUT="2000"
export NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT="15000"
export GIT_TERMINAL_PROMPT="0"
printf -- '--ipv4\\n--connect-timeout 15\\n--retry 3\\n' > ~/.curlrc 2>/dev/null || true
git config --global http.lowSpeedLimit 1000 2>/dev/null || true
git config --global http.lowSpeedTime 20 2>/dev/null || true

{CLINE_BUNDLE_PATCH_SNIPPET}

if [ -d "$HOME/.nvm" ] && [ ! -s "$HOME/.nvm/nvm.sh" ]; then
    rm -rf "$HOME/.nvm" 2>/dev/null || true
fi
if [ -f /tmp/apex_cache/tarball/cline_nvm_v2.tar.gz ] && [ ! -s "$HOME/.nvm/nvm.sh" ]; then
    echo "[Apex Setup] Restoring cached Node v22 + Cline CLI into $HOME/.nvm from SSD..."
    mkdir -p "$HOME"
    tar -xzf /tmp/apex_cache/tarball/cline_nvm_v2.tar.gz -C "$HOME" --no-same-owner 2>/dev/null || true
fi
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    \\. "$NVM_DIR/nvm.sh" 2>/dev/null || true
    _patch_cline_editor_limit
    if ! grep -q 'NVM_DIR' "$HOME/.bashrc" 2>/dev/null; then
        printf '\\nexport NVM_DIR="$HOME/.nvm"\\n[ -s "$NVM_DIR/nvm.sh" ] && \\\\. "$NVM_DIR/nvm.sh"\\n' >> "$HOME/.bashrc" 2>/dev/null || true
    fi
    if cline --version >/dev/null 2>&1 || cline version >/dev/null 2>&1; then
        cline --version 2>/dev/null || cline version 2>/dev/null || true
        echo "Cline npm binary smoke test passed."
        exit 0
    fi
fi
echo "[Apex Setup] Installing Node v22 + Cline CLI into $HOME/.nvm (per-UID cache)..."
"""
        nvm_postamble = """
_apex_rc=$?
if [ "$_apex_rc" -eq 0 ] && [ -d "$HOME/.nvm" ]; then
    _patch_cline_editor_limit
    echo "[Apex Setup] Archiving patched $HOME/.nvm to SSD cache (/tmp/apex_cache/tarball/cline_nvm_v2.tar.gz)..."
    tar -czf /tmp/apex_cache/tarball/cline_nvm_v2.tar.gz -C "$HOME" .nvm 2>/dev/null || true
    chmod 666 /tmp/apex_cache/tarball/cline_nvm_v2.tar.gz 2>/dev/null || true
    rm -rf "/tmp/apex_cache/npm_${_uid}" 2>/dev/null || true
    echo "[Apex Setup] SSD cache saved! Tasks 2-34 will restore Cline in ~2s."
fi
exit $_apex_rc
"""
        return f"{nvm_preamble}\n(\n{script}\n)\n{nvm_postamble}"

    if is_cline_run_step:
        run_preamble = rf"""# APEX_FAST_SETUP_V33
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh" 2>/dev/null || true
{CLINE_BUNDLE_PATCH_SNIPPET}
_patch_cline_editor_limit >/dev/null 2>&1 || true
"""
        return f"{run_preamble}\n{script}"

    return script


if not getattr(asyncio.wait_for, "_is_apex_hooked", False):
    _orig_wait_for = asyncio.wait_for

    async def _apex_wait_for(fut: Any, timeout: Any = None, *args: Any, **kwargs: Any) -> Any:
        if isinstance(timeout, (int, float)) and 300.0 <= float(timeout) <= 600.0:
            timeout = SETUP_TIMEOUT_SEC
        return await _orig_wait_for(fut, timeout, *args, **kwargs)

    _apex_wait_for._is_apex_hooked = True  # type: ignore
    asyncio.wait_for = _apex_wait_for


if not getattr(asyncio.subprocess.Process._read_stream, "_is_apex_hooked", False):
    async def _apex_read_stream(self: Any, fd: int) -> bytes:
        transport = self._transport.get_pipe_transport(fd)
        stream = self.stderr if fd == 2 else self.stdout
        chunks = []
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                try:
                    with open(LIVE_SETUP_LOG, "ab") as lf:
                        lf.write(chunk)
                except Exception:
                    pass
        finally:
            try:
                transport.close()
            except Exception:
                pass
        return b"".join(chunks)

    _apex_read_stream._is_apex_hooked = True  # type: ignore
    asyncio.subprocess.Process._read_stream = _apex_read_stream  # type: ignore

if not getattr(asyncio.subprocess.Process.communicate, "_is_apex_hooked", False):
    _orig_communicate = asyncio.subprocess.Process.communicate

    async def _apex_communicate(self: Any, input: Any = None) -> Any:
        if isinstance(input, (bytes, bytearray)) and input:
            try:
                decoded = input.decode("utf-8", errors="replace")
                wrapped = _wrap_container_setup_script(decoded)
                if wrapped != decoded:
                    input = wrapped.encode("utf-8")
            except Exception:
                pass
        return await _orig_communicate(self, input=input)

    _apex_communicate._is_apex_hooked = True  # type: ignore
    asyncio.subprocess.Process.communicate = _apex_communicate  # type: ignore


def _parse_mem_to_mb(val: Any) -> int:
    if isinstance(val, (int, float)):
        if val > 100_000_000:
            return int(val // (1024 * 1024))
        return int(val)
    s = str(val).strip().lower()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?)b?$", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "g":
        return int(num * 1024)
    if unit == "t":
        return int(num * 1024 * 1024)
    if unit == "k":
        return max(1, int(num / 1024))
    if unit == "m":
        return int(num)
    if num > 100_000_000:
        return int(num // (1024 * 1024))
    return int(num)


def _clamp_resources_obj(obj: Any, max_cpus: int, max_mem_mb: int) -> bool:
    changed = False
    if isinstance(obj, dict):
        if "cpuset" in obj:
            del obj["cpuset"]
            changed = True
        for k in list(obj.keys()):
            v = obj[k]
            if k in ("cpus", "cpu_count", "nano_cpus"):
                try:
                    if k == "nano_cpus":
                        lim = int(max_cpus * 1_000_000_000)
                        if int(v) > lim:
                            obj[k] = lim
                            changed = True
                    else:
                        fval = float(v)
                        if fval > float(max_cpus):
                            if isinstance(v, str):
                                obj[k] = str(max_cpus) if "." not in v else f"{float(max_cpus):.1f}"
                            elif isinstance(v, int):
                                obj[k] = int(max_cpus)
                            else:
                                obj[k] = float(max_cpus)
                            changed = True
                except Exception:
                    pass
            elif k in ("mem_limit", "memswap_limit", "memory", "mem_reservation"):
                mb = _parse_mem_to_mb(v)
                if mb > max_mem_mb:
                    if isinstance(v, (int, float)):
                        obj[k] = max_mem_mb * 1024 * 1024 if v > 100_000_000 else max_mem_mb
                    else:
                        obj[k] = f"{max_mem_mb}m"
                    changed = True
            elif isinstance(v, (dict, list)):
                if _clamp_resources_obj(v, max_cpus, max_mem_mb):
                    changed = True
    elif isinstance(obj, list):
        for item in obj:
            if _clamp_resources_obj(item, max_cpus, max_mem_mb):
                changed = True
    return changed


def _patch_compose_json_file(fpath: Path) -> None:
    try:
        if not fpath.exists() or not fpath.is_file():
            return
        name_lower = fpath.name.lower()
        if not (name_lower.endswith(".json") and ("docker-compose" in name_lower or "compose" in name_lower or "resources" in name_lower)):
            return
        raw = fpath.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            return
        data = json.loads(raw)
        modified = False
        if _clamp_resources_obj(data, MAX_CPUS, MAX_MEM_MB):
            modified = True

        if "environment" in name_lower and isinstance(data, dict):
            services = data.get("services")
            if isinstance(services, dict) and "main" in services and isinstance(services["main"], dict):
                main_svc = services["main"]
                if "dns" in main_svc:
                    del main_svc["dns"]
                    modified = True

                extra_hosts = main_svc.get("extra_hosts")
                host_gw_entry = "host.docker.internal:host-gateway"
                if isinstance(extra_hosts, list):
                    if not any("host.docker.internal" in str(h) for h in extra_hosts):
                        extra_hosts.append(host_gw_entry)
                        modified = True
                elif isinstance(extra_hosts, dict):
                    if "host.docker.internal" not in extra_hosts:
                        extra_hosts["host.docker.internal"] = "host-gateway"
                        modified = True
                else:
                    main_svc["extra_hosts"] = [host_gw_entry]
                    modified = True

                env_block = main_svc.get("environment")
                brain_vars = {
                    "BASE_URL": CONTAINER_LLM_URL,
                    "OPENAI_BASE_URL": CONTAINER_LLM_URL,
                    "OPENAI_API_BASE": CONTAINER_LLM_URL,
                    "API_KEY": "sk-local-ai",
                    "OPENAI_API_KEY": "sk-local-ai",
                    "CLINE_LOG_STREAM_JSON": "1",
                    "DEBIAN_FRONTEND": "noninteractive",
                    "NODE_OPTIONS": "--dns-result-order=ipv4first",
                    "NPM_CONFIG_FETCH_RETRIES": "5",
                    "NPM_CONFIG_FETCH_RETRY_MINTIMEOUT": "2000",
                    "NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT": "15000",
                    "NPM_CONFIG_AUDIT": "false",
                    "NPM_CONFIG_FUND": "false",
                    "GIT_TERMINAL_PROMPT": "0",
                }
                if isinstance(env_block, dict):
                    if env_block.get("NPM_CONFIG_CACHE") == "/tmp/apex_cache/npm":
                        del env_block["NPM_CONFIG_CACHE"]
                        modified = True
                    for ek, ev in brain_vars.items():
                        if not env_block.get(ek):
                            env_block[ek] = ev
                            modified = True
                elif isinstance(env_block, list):
                    env_block[:] = [x for x in env_block if not str(x).startswith("NPM_CONFIG_CACHE=")]
                    existing_keys = {str(item).split("=", 1)[0] for item in env_block}
                    for ek, ev in brain_vars.items():
                        if ek not in existing_keys:
                            env_block.append(f"{ek}={ev}")
                            modified = True
                elif env_block is None:
                    main_svc["environment"] = brain_vars
                    modified = True

        if "mounts" in name_lower and isinstance(data, dict):
            services = data.get("services")
            if isinstance(services, dict) and "main" in services and isinstance(services["main"], dict):
                main_svc = services["main"]
                vols = main_svc.get("volumes")
                apex_cache = BASE_DIR / "harbor_cache"
                apex_cache.mkdir(parents=True, exist_ok=True)
                if isinstance(vols, list):
                    vols[:] = [v for v in vols if "/root/.nvm" not in str(v) and "/root/.npm" not in str(v)]
                    has_apex_cache = any("/tmp/apex_cache" in str(v) for v in vols)
                    if not has_apex_cache:
                        if vols and isinstance(vols[0], dict):
                            vols.append({"type": "bind", "source": str(apex_cache), "target": "/tmp/apex_cache"})
                        else:
                            vols.append(f"{apex_cache}:/tmp/apex_cache")
                        modified = True

        if modified:
            fpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _inspect_and_transform_cmd_args(args_tuple: tuple) -> tuple:
    try:
        args_list = list(args_tuple)
        str_args = [str(a) for a in args_list]
        joined = " ".join(str_args).lower()
        if "compose" not in joined and "docker" not in joined:
            return args_tuple
        for arg in str_args:
            if arg.lower().endswith(".json") and ("compose" in arg.lower() or "resources" in arg.lower() or "mounts" in arg.lower() or "environment" in arg.lower()):
                _patch_compose_json_file(Path(arg))
        if "exec" in str_args:
            for idx, val in enumerate(str_args):
                if val in ("-c", "-lc") and idx + 1 < len(args_list) and isinstance(args_list[idx + 1], str):
                    orig_script = args_list[idx + 1]
                    wrapped = _wrap_container_setup_script(orig_script)
                    if wrapped != orig_script:
                        args_list[idx + 1] = wrapped
        return tuple(args_list)
    except Exception:
        return args_tuple


if not getattr(asyncio.create_subprocess_exec, "_is_apex_hooked", False):
    _orig_create_subprocess_exec = asyncio.create_subprocess_exec
    _orig_create_subprocess_shell = asyncio.create_subprocess_shell

    async def _apex_create_subprocess_exec(program: Any, *args: Any, **kwargs: Any) -> Any:
        new_tuple = _inspect_and_transform_cmd_args((program, *args))
        return await _orig_create_subprocess_exec(new_tuple[0], *new_tuple[1:], **kwargs)

    async def _apex_create_subprocess_shell(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, str) and ("compose" in cmd.lower() or "resources.json" in cmd.lower()):
            for m in re.findall(r'[A-Za-z]:\\[^\s"\']+?\.json', cmd):
                _patch_compose_json_file(Path(m))
        return await _orig_create_subprocess_shell(cmd, *args, **kwargs)

    _apex_create_subprocess_exec._is_apex_hooked = True  # type: ignore
    asyncio.create_subprocess_exec = _apex_create_subprocess_exec
    asyncio.create_subprocess_shell = _apex_create_subprocess_shell

if not getattr(subprocess.Popen, "_is_apex_hooked", False):
    _orig_popen = subprocess.Popen

    class _ApexPopen(_orig_popen):  # type: ignore
        _is_apex_hooked = True

        def __init__(self, args: Any, *pos: Any, **kw: Any) -> None:
            if isinstance(args, (list, tuple)):
                args = list(_inspect_and_transform_cmd_args(tuple(args)))
            elif isinstance(args, str) and ("compose" in args.lower() or "resources.json" in args.lower()):
                for m in re.findall(r'[A-Za-z]:\\[^\s"\']+?\.json', args):
                    _patch_compose_json_file(Path(m))
            super().__init__(args, *pos, **kw)

    subprocess.Popen = _ApexPopen  # type: ignore


def install_all_hooks() -> None:
    try:
        htrial = sys.modules.get("harbor.trial.trial")
        if htrial is not None:
            for attr in dir(htrial):
                if "SETUP_TIMEOUT" in attr.upper() and isinstance(getattr(htrial, attr), (int, float)):
                    setattr(htrial, attr, SETUP_TIMEOUT_SEC)
            TrialCls = getattr(htrial, "Trial", None)
            if TrialCls is not None:
                TrialCls._agent_setup_timeout_sec = property(
                    lambda self: SETUP_TIMEOUT_SEC,
                    lambda self, val: None,
                )
    except Exception:
        pass

    try:
        hbase = sys.modules.get("harbor.agents.installed.base")
        if hbase is not None:
            for cls_name in dir(hbase):
                cls_obj = getattr(hbase, cls_name, None)
                if isinstance(cls_obj, type) and hasattr(cls_obj, "_exec"):
                    orig_exec = getattr(cls_obj, "_exec")
                    if callable(orig_exec) and not getattr(orig_exec, "_is_apex_hooked", False):
                        def _make_exec_wrapper(_orig: Any) -> Any:
                            async def _apex_agent_exec(self: Any, environment: Any, command: str, *a: Any, **kw: Any) -> Any:
                                wrapped = _wrap_container_setup_script(command)
                                is_setup = ("npm install -g cline" in command.lower() or "apt-get " in command.lower())
                                if is_setup and isinstance(kw.get("timeout_sec"), (int, float)):
                                    if 0 < float(kw["timeout_sec"]) < SETUP_TIMEOUT_SEC:
                                        kw["timeout_sec"] = int(SETUP_TIMEOUT_SEC)
                                return await _orig(self, environment, wrapped, *a, **kw)
                            _apex_agent_exec._is_apex_hooked = True  # type: ignore
                            return _apex_agent_exec
                        setattr(cls_obj, "_exec", _make_exec_wrapper(orig_exec))
    except Exception:
        pass

    # Hook ClineCli.run to inject active skill guidance directly into Step #1 instruction
    try:
        hcline = sys.modules.get("harbor.agents.installed.cline.cline")
        if hcline is not None:
            for cls_name in dir(hcline):
                cls_obj = getattr(hcline, cls_name, None)
                if isinstance(cls_obj, type) and hasattr(cls_obj, "run"):
                    orig_run = getattr(cls_obj, "run")
                    if callable(orig_run) and not getattr(orig_run, "_is_apex_hooked", False):
                        def _make_run_wrapper(_orig_run: Any) -> Any:
                            async def _apex_cline_run(self: Any, instruction: str, *a: Any, **kw: Any) -> Any:
                                if isinstance(instruction, str) and "[MANDATORY EXECUTION & DOMAIN PROTOCOL" not in instruction:
                                    extra = _build_inline_skill_guidance()
                                    if extra:
                                        instruction = instruction.rstrip() + extra
                                return await _orig_run(self, instruction, *a, **kw)
                            _apex_cline_run._is_apex_hooked = True  # type: ignore
                            return _apex_cline_run
                        setattr(cls_obj, "run", _make_run_wrapper(orig_run))
    except Exception:
        pass


install_all_hooks()'''


def install_harbor_runtime_interceptor() -> None:
    """
    1. Installs `apex_harbor_hook.py`, `sitecustomize.py`, and `usercustomize.py`.
    2. Hooks `harbor/__init__.py`, `harbor/trial/trial.py`, `harbor/agents/installed/base.py`,
       `harbor/agents/installed/cline/cline.py`, and `harbor/environments/docker/docker.py`
       to call `apex_harbor_hook.install_all_hooks()`.
    3. Directly patches `harbor/trial/trial.py` on disk so `_setup_agent` uses a
       2,400-second timeout instead of 360.0s.
    """
    sp_dirs: List[Path] = [BASE_DIR]
    try:
        for sp in site.getsitepackages():
            p = Path(sp)
            if p.exists() and p.is_dir() and p not in sp_dirs:
                sp_dirs.append(p)
    except Exception:
        pass

    py_dir = Path(sys.executable).resolve().parent
    for cand in [py_dir / "Lib" / "site-packages", Path(r"D:\Python\Lib\site-packages")]:
        if cand.exists() and cand not in sp_dirs:
            sp_dirs.append(cand)

    try:
        import harbor  # type: ignore
        if hasattr(harbor, "__file__") and harbor.__file__:
            h_sp = Path(harbor.__file__).resolve().parent.parent
            if h_sp.exists() and h_sp not in sp_dirs:
                sp_dirs.append(h_sp)
    except Exception:
        pass

    hook_installed = False
    for sp in sp_dirs:
        for fname in ("apex_harbor_hook.py", "sitecustomize.py", "usercustomize.py"):
            try:
                target = sp / fname
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(APEX_HOOK_CODE, encoding="utf-8")
                hook_installed = True
            except Exception:
                pass

        for rel_harbor in (
            Path("harbor") / "__init__.py",
            Path("harbor") / "trial" / "trial.py",
            Path("harbor") / "agents" / "installed" / "base.py",
            Path("harbor") / "agents" / "installed" / "cline" / "cline.py",
            Path("harbor") / "environments" / "docker" / "docker.py",
        ):
            hfile = sp / rel_harbor
            if hfile.exists():
                try:
                    src = hfile.read_text(encoding="utf-8", errors="replace")
                    if "install_all_hooks" not in src:
                        patch_snippet = (
                            "\n# --- APEX HARBOR RUNTIME HOOK v3.0 ---\n"
                            "try:\n"
                            "    import apex_harbor_hook\n"
                            "    apex_harbor_hook.install_all_hooks()\n"
                            "except Exception:\n"
                            "    pass\n"
                        )
                        hfile.write_text(src.rstrip() + "\n" + patch_snippet, encoding="utf-8")
                        print(f"[+] Hooked Harbor module (v3.0): {hfile}")
                except Exception:
                    pass

        trial_py = sp / "harbor" / "trial" / "trial.py"
        if trial_py.exists():
            try:
                tsrc = trial_py.read_text(encoding="utf-8", errors="replace")
                tmod = re.sub(
                    r"(SETUP_TIMEOUT[A-Za-z0-9_]*\s*(?::\s*[^=]+)?=\s*)360(?:\.0)?\b",
                    r"\g<1>2400.0",
                    tsrc,
                )
                tmod = re.sub(
                    r"(\b_agent_setup_timeout_sec\b\s*=\s*)360(?:\.0)?\b",
                    r"\g<1>2400.0",
                    tmod,
                )
                if "timeout=self._agent_setup_timeout_sec" in tmod:
                    tmod = tmod.replace(
                        "timeout=self._agent_setup_timeout_sec",
                        "timeout=max(float(self._agent_setup_timeout_sec or 0), 2400.0)",
                    )
                if tmod != tsrc:
                    trial_py.write_text(tmod, encoding="utf-8")
                    print(f"[+] Patched _setup_agent timeout (360s -> 2400s) directly in: {trial_py}")
            except Exception:
                pass

    if hook_installed:
        print("[+] Installed Harbor Docker-Compose & Agent-Setup Interceptor (v3.0).")


def write_active_task_state(
    task_name: str,
    phase: str,
    model_name: str = "",
    skills: Optional[List[str]] = None,
    detail: str = "",
) -> None:
    """Writes live task status to `current_active_task.json` for `web_dashboard.py`."""
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "task": task_name,
            "phase": phase,
            "model": model_name,
            "skills": skills or [],
            "detail": detail,
            "updated_at": time.time(),
            "timestamp": datetime.now().isoformat(),
        }
        ACTIVE_TASK_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def clear_active_task_state() -> None:
    try:
        if ACTIVE_TASK_FILE.exists():
            ACTIVE_TASK_FILE.unlink()
    except Exception:
        pass


def _is_docker_ready(timeout_sec: int = 12) -> bool:
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec,
        )
        return res.returncode == 0
    except Exception:
        return False


def ensure_docker_daemon() -> bool:
    global _DOCKER_LOG_HANDLE
    if _is_docker_ready(timeout_sec=12):
        return True

    print("[*] Docker daemon not responding. Cleaning any wedged backend & starting Docker WSL2...")
    backend_exe = r"C:\Program Files\Docker\Docker\resources\com.docker.backend.exe"
    desktop_exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"

    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "com.docker.backend.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        time.sleep(1.5)
    except Exception:
        pass

    if os.path.exists(backend_exe):
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            _DOCKER_LOG_HANDLE = open(DOCKER_LOG_FILE, "a", encoding="utf-8", errors="replace")
            subprocess.Popen(
                [backend_exe, "-unattended", "-with-frontend=false", "-engine", "WSL2"],
                stdout=_DOCKER_LOG_HANDLE,
                stderr=_DOCKER_LOG_HANDLE,
            )
        except Exception as e:
            print(f"  [!] Warning launching com.docker.backend.exe with log file: {e}")
            subprocess.Popen([backend_exe, "-unattended", "-with-frontend=false", "-engine", "WSL2"])
    elif os.path.exists(desktop_exe):
        subprocess.Popen([desktop_exe])

    for attempt in range(1, 36):
        time.sleep(2)
        if _is_docker_ready(timeout_sec=10):
            print(f"  [+] Docker WSL2 engine is ready! (Logs redirected to {DOCKER_LOG_FILE})")
            return True
        if attempt % 4 == 0:
            print(f"  [*] Waiting for Docker WSL2 engine to finish booting ({attempt * 2}s)...")
        if attempt == 15 and os.path.exists(desktop_exe):
            print("  [*] Triggering Docker Desktop service helper...")
            try:
                subprocess.Popen([desktop_exe, "-Autostart"])
            except Exception:
                pass

    print("[!] ERROR: Docker daemon is still not responding after 70s.")
    if DOCKER_LOG_FILE.exists():
        tail = _read_tail(DOCKER_LOG_FILE, 600)
        if tail:
            print(f"    --- Last lines of {DOCKER_LOG_FILE} ---\n{tail}\n    ----------------------------------------")
    return False


def get_docker_host_limits() -> Tuple[int, int]:
    global _CACHED_DOCKER_LIMITS
    if _CACHED_DOCKER_LIMITS is not None:
        return _CACHED_DOCKER_LIMITS

    max_cpus = 4
    max_mem_mb = 12288
    try:
        res = subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split()
            if len(parts) >= 1 and parts[0].isdigit():
                detected_cpus = int(parts[0])
                if detected_cpus > 0:
                    max_cpus = detected_cpus
            if len(parts) >= 2 and parts[1].isdigit():
                total_bytes = int(parts[1])
                if total_bytes > 0:
                    max_mem_mb = max(2048, int((total_bytes / (1024 * 1024)) * 0.85))
    except Exception:
        pass

    env_cpus = os.environ.get("APEX_MAX_DOCKER_CPUS")
    if env_cpus and env_cpus.isdigit():
        max_cpus = int(env_cpus)

    _CACHED_DOCKER_LIMITS = (max_cpus, max_mem_mb)
    return _CACHED_DOCKER_LIMITS


def check_local_llm(host_url: str = "http://127.0.0.1:8080/v1") -> Tuple[bool, Optional[str]]:
    try:
        req = urllib.request.Request(f"{host_url.rstrip('/')}/models")
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                data = payload.get("data", [])
                model_id = data[0].get("id") if data else "local-model"
                return True, model_id
    except Exception:
        pass
    return False, None


def get_host_ipv4_candidates() -> List[str]:
    ips: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def ensure_windows_firewall_port_8080() -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                "name=LocalAI_Brain_8080", "dir=in", "action=allow",
                "protocol=TCP", "localport=8080",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        pass


def verify_container_to_brain_handshake(host_url: str = "http://127.0.0.1:8080/v1", model_id: str = "local-model") -> str:
    global _VERIFIED_CONTAINER_LLM_URL
    if _VERIFIED_CONTAINER_LLM_URL:
        return _VERIFIED_CONTAINER_LLM_URL

    env_override = os.environ.get("APEX_CONTAINER_LLM_URL")
    if env_override:
        _VERIFIED_CONTAINER_LLM_URL = env_override.strip()
        return _VERIFIED_CONTAINER_LLM_URL

    print("[*] Sending pre-flight token generation ping to Brain (watch your llama-server window)...")
    try:
        body = json.dumps({
            "model": model_id or "local-model",
            "messages": [{"role": "user", "content": "Reply OK"}],
            "max_tokens": 4,
            "temperature": 0.0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{host_url.rstrip('/')}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print("  [+] Host -> Brain token generation confirmed (200 OK)!")
    except Exception as e:
        print(f"  [!] Host -> Brain test warning: {e}")

    candidates = ["http://host.docker.internal:8080/v1"]
    for ip in get_host_ipv4_candidates():
        candidates.append(f"http://{ip}:8080/v1")

    probe_image = None
    try:
        res = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        fallback_image = None
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or "<none>" in line:
                continue
            if "egress-control-sidecar" in line:
                fallback_image = line
                continue
            probe_image = line
            break
        if not probe_image:
            probe_image = fallback_image
    except Exception:
        pass

    if not probe_image:
        _VERIFIED_CONTAINER_LLM_URL = candidates[0]
        return _VERIFIED_CONTAINER_LLM_URL

    ensure_windows_firewall_port_8080()
    print(f"[*] Testing Docker Container -> Brain connection using image '{probe_image}'...")

    for cand_url in candidates:
        probe_cmd = (
            f"curl -s -m 4 {cand_url}/models || "
            f"wget -qO- -T 4 {cand_url}/models || "
            f"python3 -c \"import urllib.request; print(urllib.request.urlopen('{cand_url}/models', timeout=4).read().decode())\" || "
            f"python -c \"import urllib.request; print(urllib.request.urlopen('{cand_url}/models', timeout=4).read().decode())\""
        )
        try:
            res = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--add-host=host.docker.internal:host-gateway",
                    "--entrypoint", "sh",
                    probe_image,
                    "-c", probe_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=12,
            )
            if res.returncode == 0 and ("data" in res.stdout or "model" in res.stdout or "object" in res.stdout):
                print(f"  [+] Docker Container -> Brain connection VERIFIED at: {cand_url}")
                _VERIFIED_CONTAINER_LLM_URL = cand_url
                return cand_url
        except Exception:
            pass

    print(f"  [*] Using container LLM URL: {candidates[0]}")
    _VERIFIED_CONTAINER_LLM_URL = candidates[0]
    return _VERIFIED_CONTAINER_LLM_URL


def inspect_installed_harbor_cline_adapter() -> Dict[str, Any]:
    info = {
        "separator": ":",
        "adapter_path": None,
    }
    search_roots: List[Path] = []
    try:
        import harbor  # type: ignore
        if hasattr(harbor, "__file__") and harbor.__file__:
            search_roots.append(Path(harbor.__file__).resolve().parent)
    except Exception:
        pass

    harbor_bin = shutil.which("harbor")
    if harbor_bin:
        bin_path = Path(harbor_bin).resolve()
        for parent in [bin_path.parent, bin_path.parent.parent]:
            for sp in parent.rglob("site-packages/harbor"):
                if sp.is_dir():
                    search_roots.append(sp)

    user_profile = Path(os.environ.get("USERPROFILE", r"C:\Users\Administrator"))
    for uv_dir in [
        user_profile / "AppData" / "Roaming" / "uv" / "tools" / "harbor",
        user_profile / ".local" / "share" / "uv" / "tools" / "harbor",
    ]:
        if uv_dir.exists():
            for sp in uv_dir.rglob("site-packages/harbor"):
                if sp.is_dir():
                    search_roots.append(sp)

    for root in search_roots:
        for cand in root.rglob("*cline*.py"):
            try:
                src = cand.read_text(encoding="utf-8", errors="replace")
                info["adapter_path"] = str(cand)
                has_colon_split = ('.split(":")' in src or "split(':'," in src or 'split(":",' in src or '":" in' in src or "':' in" in src)
                has_slash_split = ('.split("/")' in src or "split('/'," in src or 'split("/",' in src or '"/" in' in src or "'/' in" in src)
                if has_colon_split and not has_slash_split:
                    info["separator"] = ":"
                elif has_slash_split and not has_colon_split:
                    info["separator"] = "/"
                elif has_colon_split and has_slash_split:
                    info["separator"] = ":"
                break
            except Exception:
                pass
        if info["adapter_path"]:
            break

    return info


def prepare_task_toml_for_local_brain(task_path: Path, container_llm_url: str) -> None:
    toml_path = task_path / "task.toml"
    max_cpus, max_mem_mb = get_docker_host_limits()

    if toml_path.exists():
        backup_path = task_path / "task.toml.orig_backup"
        try:
            if backup_path.exists():
                raw = backup_path.read_text(encoding="utf-8", errors="replace")
            else:
                raw = toml_path.read_text(encoding="utf-8", errors="replace")
                backup_path.write_text(raw, encoding="utf-8")

            modified = raw

            def _clamp_cpus(match: re.Match) -> str:
                prefix = match.group(1)
                num_str = match.group(2).replace("_", "")
                val = float(num_str)
                clamped = min(val, float(max_cpus))
                if "." not in num_str:
                    return f"{prefix}{int(clamped)}"
                return f"{prefix}{clamped}"

            modified = re.sub(
                r'(\b(?:cpus|cpu_count|num_cpus)\s*=\s*)["\']?([0-9_]+(?:\.[0-9_]+)?)["\']?',
                _clamp_cpus,
                modified,
            )

            def _clamp_mem_mb(match: re.Match) -> str:
                prefix = match.group(1)
                num_str = match.group(2).replace("_", "")
                val = int(float(num_str))
                return f"{prefix}{min(val, max_mem_mb)}"

            modified = re.sub(
                r'(\bmemory_mb\s*=\s*)["\']?([0-9_]+)["\']?',
                _clamp_mem_mb,
                modified,
            )

            def _clamp_mem_str(match: re.Match) -> str:
                prefix = match.group(1)
                num = int(match.group(2).replace("_", ""))
                max_g = max(2, max_mem_mb // 1024)
                return f'{prefix}"{min(num, max_g)}G"'

            modified = re.sub(r'(\bmemory\s*=\s*)["\']([0-9_]+)[Gg]["\']', _clamp_mem_str, modified)

            def _clamp_gpus(match: re.Match) -> str:
                prefix = match.group(1)
                val = int(match.group(2).replace("_", ""))
                return f"{prefix}{min(val, 1)}"

            modified = re.sub(r"(\bgpus\s*=\s*)([0-9_]+)\b", _clamp_gpus, modified)

            modified = re.sub(r"(\ballow_internet\s*=\s*)false\b", r"\1true", modified)
            modified = re.sub(
                r'(\bnetwork_mode\s*=\s*)["\'](?:no-network|allowlist)["\']',
                r'\1"public"',
                modified,
            )

            if "[environment.env]" not in modified and "env = {" not in modified:
                env_block = (
                    "\n# --- APEX LOCAL BRAIN ENV ---\n"
                    "[environment.env]\n"
                    f'BASE_URL = "{container_llm_url}"\n'
                    f'OPENAI_BASE_URL = "{container_llm_url}"\n'
                    f'OPENAI_API_BASE = "{container_llm_url}"\n'
                    'API_KEY = "sk-local-ai"\n'
                    'OPENAI_API_KEY = "sk-local-ai"\n'
                    'CLINE_LOG_STREAM_JSON = "1"\n'
                )
                modified = modified.rstrip() + "\n" + env_block

            toml_path.write_text(modified, encoding="utf-8")
        except Exception as e:
            print(f"[!] Warning: Could not update {toml_path}: {e}")

    env_dir = task_path / "environment"
    if env_dir.exists():
        for dc_name in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"):
            dc_file = env_dir / dc_name
            if dc_file.exists():
                try:
                    dc_raw = dc_file.read_text(encoding="utf-8", errors="replace")
                    dc_mod = re.sub(
                        r'(\bcpus:\s*)["\']?([0-9]+(?:\.[0-9]+)?)["\']?',
                        lambda m: f"{m.group(1)}{min(float(m.group(2)), float(max_cpus))}",
                        dc_raw,
                    )
                    if dc_mod != dc_raw:
                        dc_file.write_text(dc_mod, encoding="utf-8")
                except Exception:
                    pass


def patch_all_tasks_for_local_brain(container_llm_url: str = "http://host.docker.internal:8080/v1") -> int:
    if not TASKS_DIR.exists():
        return 0
    count = 0
    for tdir in TASKS_DIR.iterdir():
        if tdir.is_dir() and (tdir / "task.toml").exists():
            prepare_task_toml_for_local_brain(tdir, container_llm_url)
            count += 1
    return count


def measure_live_token_rate(host_url: str = "http://127.0.0.1:8080/v1") -> Optional[float]:
    try:
        body = json.dumps({
            "model": "local-model",
            "messages": [{"role": "user", "content": "Write a 30-word summary of binary search in C."}],
            "max_tokens": 32,
            "temperature": 0.0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{host_url.rstrip('/')}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8", errors="replace"))
        dt = max(time.time() - t0, 0.001)
        timings = out.get("timings", {})
        if timings.get("predicted_per_second"):
            return round(float(timings["predicted_per_second"]), 2)
        comp_tokens = out.get("usage", {}).get("completion_tokens", 0)
        if comp_tokens > 0:
            return round(comp_tokens / dt, 2)
    except Exception:
        pass
    return None


def load_scoreboard() -> Dict[str, Any]:
    if SCOREBOARD_FILE.exists():
        try:
            data = json.loads(SCOREBOARD_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "completed_tasks" in data:
                return data
        except Exception:
            pass
    return {
        "benchmark": "FrontierSWE v2",
        "started_at": datetime.now().isoformat(),
        "total_tasks": len(FRONTIER_TASKS),
        "completed_tasks": {},
        "passed": 0,
        "partial": 0,
        "failed": 0,
        "infra_errors": 0,
        "total_done": 0,
        "mean_reward": 0.0,
        "mean_reward_percent": 0.0,
        "pass_rate_percent": 0.0,
    }


def recompute_scoreboard_aggregates(board: Dict[str, Any]) -> Dict[str, Any]:
    completed = board.get("completed_tasks", {})
    valid_trials = [
        v for v in completed.values()
        if v.get("status") not in ("INFRA_ERROR",)
        and v.get("infra_ok", True)
        and ((v.get("prompt_tokens", 0) or 0) > 0 or (v.get("output_tokens", 0) or 0) > 0 or (v.get("tokens_evaluated", 0) or 0) > 0 or float(v.get("reward", 0.0) or 0.0) > 0.0)
    ]
    infra_errs = [
        v for v in completed.values()
        if v not in valid_trials
    ]

    total_done = len(valid_trials)
    passed = sum(1 for v in valid_trials if float(v.get("reward", 0.0) or 0.0) >= 1.0)
    partial = sum(1 for v in valid_trials if 0.0 < float(v.get("reward", 0.0) or 0.0) < 1.0)
    failed = total_done - passed - partial
    sum_reward = sum(float(v.get("reward", 0.0) or 0.0) for v in valid_trials)
    mean_reward = (sum_reward / total_done) if total_done > 0 else 0.0

    board["total_tasks"] = len(FRONTIER_TASKS)
    board["total_done"] = total_done
    board["passed"] = passed
    board["partial"] = partial
    board["failed"] = failed
    board["infra_errors"] = len(infra_errs)
    board["mean_reward"] = round(mean_reward, 4)
    board["mean_reward_percent"] = round(mean_reward * 100.0, 2)
    board["pass_rate_percent"] = round((passed / total_done) * 100.0, 2) if total_done > 0 else 0.0
    return board


def save_scoreboard(data: Dict[str, Any]) -> None:
    try:
        recompute_scoreboard_aggregates(data)
        SCOREBOARD_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[!] Warning: Failed to write scoreboard: {e}")


def clean_corrupted_scoreboard_entries() -> int:
    board = load_scoreboard()
    completed = board.get("completed_tasks", {})
    to_remove = []
    for tname, info in completed.items():
        elapsed = float(info.get("elapsed_sec", 0.0) or 0.0)
        has_reward_key = "reward" in info and info["reward"] is not None
        status = info.get("status", "")
        p_tok = int(info.get("prompt_tokens", 0) or 0)
        g_tok = int(info.get("output_tokens", 0) or 0)
        t_eval = int(info.get("tokens_evaluated", 0) or 0)
        rew = float(info.get("reward", 0.0) or 0.0)
        zero_tokens = (p_tok == 0 and g_tok == 0 and t_eval == 0 and rew <= 0.0)

        if elapsed < 5.0 or status == "INFRA_ERROR" or not has_reward_key or zero_tokens:
            to_remove.append(tname)

    for tname in to_remove:
        del completed[tname]

    save_scoreboard(board)
    return len(to_remove)


def _read_tail(path: Path, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace").strip()
        return txt[-max_chars:] if len(txt) > max_chars else txt
    except Exception:
        return ""


def extract_agent_diagnostics(trial_dir: Path) -> str:
    agent_dir = trial_dir / "agent"
    parts: List[str] = []

    res_json = trial_dir / "result.json"
    if res_json.exists():
        try:
            rdata = json.loads(res_json.read_text(encoding="utf-8", errors="replace"))
            exc = rdata.get("exception_info") or rdata.get("error")
            if exc:
                parts.append(f"[result.json exception]: {exc}")
        except Exception:
            pass

    live_setup_tail = _read_tail(LIVE_SETUP_LOG, 1400)
    if live_setup_tail:
        parts.append(f"[container_setup_live.log tail]:\n{live_setup_tail}")

    if not agent_dir.exists():
        parts.append("[agent/ directory missing — container failed before agent phase]")
        return "\n".join(parts)

    setup_rc = _read_tail(agent_dir / "setup" / "return-code.txt", 64)
    setup_err = _read_tail(agent_dir / "setup" / "stderr.txt", 1000)
    setup_out = _read_tail(agent_dir / "setup" / "stdout.txt", 1000)
    if setup_rc != "0":
        parts.append(f"[agent/setup return-code={setup_rc or 'TIMEOUT/unfinished'}]")
    if setup_err:
        parts.append(f"[agent/setup/stderr.txt]:\n{setup_err}")
    if setup_rc != "0" and setup_out:
        parts.append(f"[agent/setup/stdout.txt]:\n{setup_out}")

    for cmd_dir in sorted(agent_dir.glob("command-*")):
        cmd_txt = _read_tail(cmd_dir / "command.txt", 500)
        rc_txt = _read_tail(cmd_dir / "return-code.txt", 64)
        err_txt = _read_tail(cmd_dir / "stderr.txt", 800)
        out_txt = _read_tail(cmd_dir / "stdout.txt", 800)
        if cmd_txt:
            parts.append(f"[{cmd_dir.name}/command.txt (rc={rc_txt or '?'})]: {cmd_txt}")
        if err_txt:
            parts.append(f"[{cmd_dir.name}/stderr.txt]:\n{err_txt}")
        if out_txt and not out_txt.startswith("{"):
            parts.append(f"[{cmd_dir.name}/stdout.txt]:\n{out_txt}")

    cline_tail = _read_tail(agent_dir / "cline.txt", 1000)
    if cline_tail:
        parts.append(f"[agent/cline.txt tail]:\n{cline_tail}")

    return "\n".join(parts) if parts else "No agent log output was written inside the container."


def parse_harbor_trial_result(task_name: str, t_start: float) -> Dict[str, Any]:
    """
    Locates the trial directory created during this run (mtime >= t_start - 5s)
    and extracts the authoritative verifier reward and token/step metrics from
    `verifier/reward.json`, `result.json`, `agent/trajectory.json`, `agent/cline.txt`,
    and `LIVE_SETUP_LOG`.
    """
    outcome = {
        "reward": None,
        "infra_ok": False,
        "trial_dir": None,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "steps": 0,
        "error": None,
        "diagnostics": "",
    }
    if not JOBS_DIR.exists():
        return outcome

    candidates = []
    for p in JOBS_DIR.rglob(f"{task_name}__*"):
        if p.is_dir():
            try:
                if p.stat().st_mtime >= (t_start - 5.0):
                    candidates.append(p)
            except Exception:
                pass

    if not candidates:
        return outcome

    trial_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    outcome["trial_dir"] = str(trial_dir)

    reward_json = trial_dir / "verifier" / "reward.json"
    if reward_json.exists():
        try:
            rdata = json.loads(reward_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(rdata, (int, float)):
                outcome["reward"] = float(rdata)
                outcome["infra_ok"] = True
            elif isinstance(rdata, dict):
                val = rdata.get("reward", rdata.get("score", next(iter(rdata.values()), None)))
                if val is not None:
                    outcome["reward"] = float(val)
                    outcome["infra_ok"] = True
        except Exception:
            pass

    reward_txt = trial_dir / "verifier" / "reward.txt"
    if outcome["reward"] is None and reward_txt.exists():
        try:
            raw = reward_txt.read_text(encoding="utf-8", errors="replace").strip()
            if raw:
                outcome["reward"] = float(raw.splitlines()[-1].strip())
                outcome["infra_ok"] = True
        except Exception:
            pass

    result_json = trial_dir / "result.json"
    if result_json.exists():
        try:
            res_data = json.loads(result_json.read_text(encoding="utf-8", errors="replace"))
            if outcome["reward"] is None:
                v_res = res_data.get("verifier_result") or res_data.get("rewards") or {}
                if isinstance(v_res, dict):
                    rewards_map = v_res.get("rewards", v_res)
                    if isinstance(rewards_map, dict) and "reward" in rewards_map:
                        outcome["reward"] = float(rewards_map["reward"])
                        outcome["infra_ok"] = True
                elif isinstance(res_data.get("reward"), (int, float)):
                    outcome["reward"] = float(res_data["reward"])
                    outcome["infra_ok"] = True
            if res_data.get("exception_info") or res_data.get("error"):
                outcome["error"] = str(res_data.get("exception_info") or res_data.get("error"))
            # Check token counts recorded in result.json / agent_result
            ag_res = res_data.get("agent_result") or {}
            if isinstance(ag_res, dict):
                in_t = int(ag_res.get("n_input_tokens", 0) or 0) + int(ag_res.get("n_cache_tokens", 0) or 0)
                out_t = int(ag_res.get("n_output_tokens", 0) or 0)
                if in_t > 0 or out_t > 0:
                    outcome["prompt_tokens"] = max(outcome["prompt_tokens"], in_t)
                    outcome["output_tokens"] = max(outcome["output_tokens"], out_t)
                    outcome["total_tokens"] = outcome["prompt_tokens"] + outcome["output_tokens"]
        except Exception:
            pass

    for log_cand in [
        trial_dir / "agent" / "cline.txt",
        trial_dir / "agent" / "command-1" / "stdout.txt",
        trial_dir / "agent" / "command-0" / "stdout.txt",
        LIVE_SETUP_LOG,
    ]:
        if log_cand.exists():
            p_tok, g_tok, steps = _parse_cline_log_tokens(log_cand)
            if p_tok > 0 or g_tok > 0 or steps > 0:
                outcome["prompt_tokens"] = max(outcome["prompt_tokens"], p_tok)
                outcome["output_tokens"] = max(outcome["output_tokens"], g_tok)
                outcome["total_tokens"] = outcome["prompt_tokens"] + outcome["output_tokens"]
                outcome["steps"] = max(outcome["steps"], steps)

    outcome["diagnostics"] = extract_agent_diagnostics(trial_dir)
    return outcome


def _parse_cline_log_tokens(log_path: Path) -> Tuple[int, int, int]:
    """
    Accurately parses `usage` events (`inputTokens`/`outputTokens`), `run_result`
    usage summaries, and `stats` events (`tokens`/`generated`) from a Cline log.
    Returns (cumulative_prompt_tokens, cumulative_output_tokens, iteration_count).
    """
    cum_prompt = 0
    cum_output = 0
    max_stats_prompt = 0
    max_stats_gen = 0
    iterations = 0

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                    top_type = d.get("type")
                    if top_type == "run_result":
                        u = d.get("usage", {}) or d.get("aggregateUsage", {})
                        if isinstance(u, dict):
                            max_stats_prompt = max(max_stats_prompt, int(u.get("inputTokens", 0) or u.get("prompt_tokens", 0) or 0) + int(u.get("cacheReadTokens", 0) or 0))
                            max_stats_gen = max(max_stats_gen, int(u.get("outputTokens", 0) or u.get("completion_tokens", 0) or 0))
                        iterations = max(iterations, int(d.get("iterations", 0) or 0))

                    ev = d.get("event", {})
                    if not isinstance(ev, dict):
                        continue
                    etype = ev.get("type")
                    if etype == "iteration_start":
                        iterations = max(iterations, int(ev.get("iteration", iterations + 1)))
                    elif etype == "usage":
                        cum_prompt += int(ev.get("inputTokens", 0) or 0) + int(ev.get("cacheReadTokens", 0) or 0)
                        cum_output += int(ev.get("outputTokens", 0) or 0)
                    stats = ev.get("stats", {})
                    if isinstance(stats, dict) and stats:
                        max_stats_prompt = max(max_stats_prompt, int(stats.get("tokens", 0) or 0))
                        max_stats_gen = max(max_stats_gen, int(stats.get("generated", 0) or 0))
                except Exception:
                    pass
    except Exception:
        pass

    final_prompt = cum_prompt if cum_prompt > 0 else max_stats_prompt
    final_output = cum_output if cum_output > 0 else max_stats_gen
    return final_prompt, final_output, iterations


class FairBudgetWatchdog(threading.Thread):
    """
    1. Tails `D:\\LocalAI\\container_setup_live.log` (written in real time by
       `_apex_read_stream` inside Harbor's subprocess pipes) throughout BOTH
       container setup AND `ClineCli.run()`.
    2. Mirrors plain-text setup lines to `<trial_dir>/agent/setup/stderr.txt` and
       mirrors live JSON agent events (`{"ts":...}`) to `<trial_dir>/agent/cline.txt`
       so `web_dashboard.py` (http://localhost:8088) and `live_monitor.py` stream
       every thought, tool call, and token update in real time!
    3. Enforces `max_output_tokens`, `max_total_tokens`, and `max_steps` by
       sending `SIGINT` to `cline` inside the container so Harbor runs the Verifier.
    """
    def __init__(
        self,
        task_name: str,
        t_start: float,
        max_output_tokens: int,
        max_total_tokens: int,
        max_steps: int,
        proc: subprocess.Popen,
    ):
        super().__init__(daemon=True)
        self.task_name = task_name
        self.t_start = t_start
        self.max_output_tokens = max_output_tokens
        self.max_total_tokens = max_total_tokens
        self.max_steps = max_steps
        self.proc = proc
        self.stop_requested = False
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.steps = 0
        self.budget_triggered = False
        self._live_setup_offset = 0

    def _find_current_trial_dir(self) -> Optional[Path]:
        if not JOBS_DIR.exists():
            return None
        candidates = []
        for p in JOBS_DIR.rglob(f"{self.task_name}__*"):
            if p.is_dir():
                try:
                    if p.stat().st_mtime >= (self.t_start - 3.0):
                        candidates.append(p)
                except Exception:
                    pass
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    def _process_live_stream_chunks(self, trial_dir: Optional[Path]) -> None:
        if not LIVE_SETUP_LOG.exists():
            return
        try:
            fsize = LIVE_SETUP_LOG.stat().st_size
            if fsize <= self._live_setup_offset:
                return
            with open(LIVE_SETUP_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._live_setup_offset)
                chunk = f.read(65536)
                self._live_setup_offset = f.tell()

            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            if not lines:
                return

            plain_lines: List[str] = []
            json_lines: List[str] = []
            for ln in lines:
                if ln.startswith("{") and ('"agent_event"' in ln or '"run_result"' in ln or '"event"' in ln):
                    json_lines.append(ln)
                else:
                    plain_lines.append(ln)

            if plain_lines and not json_lines and self.steps == 0:
                for ln in plain_lines[-4:]:
                    print(f"\n  [Container Setup] {ln[:120]}", flush=True)
                write_active_task_state(
                    task_name=self.task_name,
                    phase="Installing Cline in Container",
                    detail=plain_lines[-1][:180],
                )
                if trial_dir is not None:
                    setup_stderr = trial_dir / "agent" / "setup" / "stderr.txt"
                    setup_stderr.parent.mkdir(parents=True, exist_ok=True)
                    with open(setup_stderr, "a", encoding="utf-8", errors="replace") as sf:
                        for ln in plain_lines:
                            sf.write(f"[Container Setup] {ln}\n")

            if json_lines:
                if trial_dir is not None:
                    cline_txt = trial_dir / "agent" / "cline.txt"
                    cline_txt.parent.mkdir(parents=True, exist_ok=True)
                    with open(cline_txt, "a", encoding="utf-8", errors="replace") as cf:
                        for jln in json_lines:
                            cf.write(jln + "\n")

                for jln in json_lines:
                    try:
                        d = json.loads(jln)
                        ev = d.get("event", {})
                        if isinstance(ev, dict):
                            etype = ev.get("type")
                            if etype == "iteration_start":
                                it = ev.get("iteration", 1)
                                print(
                                    f"\n  [Agent Step {it}] Prompt: {self.prompt_tokens:,} tok | "
                                    f"Generated: {self.output_tokens:,} tok",
                                    flush=True,
                                )
                                write_active_task_state(
                                    task_name=self.task_name,
                                    phase=f"Agent Iteration {it}",
                                    detail=f"Prompt: {self.prompt_tokens:,} | Generated: {self.output_tokens:,}",
                                )
                            elif ev.get("contentType") == "tool" and etype == "content_start":
                                tname = ev.get("toolName", "tool")
                                print(f"\n  [Agent Tool] Calling `{tname}`...", flush=True)
                    except Exception:
                        pass
        except Exception:
            pass

    def _gracefully_stop_container_agent(self) -> None:
        try:
            res = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in res.stdout.splitlines():
                if self.task_name in line.lower() or "harbor" in line.lower():
                    cid = line.split()[0]
                    subprocess.run(
                        ["docker", "exec", cid, "sh", "-c", "pkill -INT -f cline || pkill -TERM -f cline || true"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
        except Exception:
            pass

    def run(self) -> None:
        while not self.stop_requested and self.proc.poll() is None:
            time.sleep(1.5)
            trial_dir = self._find_current_trial_dir()
            self._process_live_stream_chunks(trial_dir)

            if LIVE_SETUP_LOG.exists():
                p_tok, g_tok, steps = _parse_cline_log_tokens(LIVE_SETUP_LOG)
                self.prompt_tokens = max(self.prompt_tokens, p_tok)
                self.output_tokens = max(self.output_tokens, g_tok)
                self.steps = max(self.steps, steps)
                self.total_tokens = self.prompt_tokens + self.output_tokens

            if trial_dir:
                for rel in ("agent/cline.txt", "agent/command-1/stdout.txt"):
                    p = trial_dir / rel
                    if p.exists() and p.stat().st_size > 0:
                        p_tok, g_tok, steps = _parse_cline_log_tokens(p)
                        self.prompt_tokens = max(self.prompt_tokens, p_tok)
                        self.output_tokens = max(self.output_tokens, g_tok)
                        self.steps = max(self.steps, steps)
                        self.total_tokens = self.prompt_tokens + self.output_tokens

            reason = None
            if self.max_output_tokens > 0 and self.output_tokens >= self.max_output_tokens:
                reason = f"GENERATED TOKEN CAP REACHED ({self.output_tokens:,} / {self.max_output_tokens:,} output tokens)"
            elif self.max_total_tokens > 0 and self.total_tokens >= self.max_total_tokens:
                reason = f"TOTAL TOKEN CAP REACHED ({self.total_tokens:,} / {self.max_total_tokens:,} total tokens)"
            elif self.max_steps > 0 and self.steps >= self.max_steps:
                reason = f"STEP CAP REACHED ({self.steps} / {self.max_steps} iterations)"

            if reason:
                self.budget_triggered = True
                print(f"\n[!] {reason}!")
                print(f"[*] Signaling in-container agent for '{self.task_name}' to conclude so Harbor Verifier grades current workspace...")
                self._gracefully_stop_container_agent()
                break


def build_compatible_harbor_cmd(
    task_path: Path,
    model_name: str,
    timeout_mult: str,
    effective_timeout: int,
    skill_flags: List[str],
    container_llm_url: str,
) -> List[str]:
    supported = get_supported_harbor_flags()
    max_cpus, max_mem_mb = get_docker_host_limits()
    cmd = [
        "harbor", "run",
        "-p", str(task_path),
        "-o", str(JOBS_DIR),
        "--agent", "cline-cli",
        "--model", model_name,
    ]

    if skill_flags:
        cmd.extend(skill_flags)

    effective_mult = "4.0" if timeout_mult == "1.0" else timeout_mult
    if not supported or "--agent-timeout-multiplier" in supported:
        cmd.extend(["--agent-timeout-multiplier", effective_mult])
    elif "--timeout-multiplier" in supported:
        cmd.extend(["--timeout-multiplier", effective_mult])

    if not supported or "--agent-kwarg" in supported:
        cmd.extend(["--agent-kwarg", f"timeout_sec={effective_timeout}"])

    if "--override-cpus" in supported:
        cmd.extend(["--override-cpus", str(max_cpus)])
    if "--override-memory-mb" in supported:
        cmd.extend(["--override-memory-mb", str(max_mem_mb)])

    if "--agent-env" in supported:
        for kv in [
            f"BASE_URL={container_llm_url}",
            f"OPENAI_BASE_URL={container_llm_url}",
            f"OPENAI_API_BASE={container_llm_url}",
            "API_KEY=sk-local-ai",
            "OPENAI_API_KEY=sk-local-ai",
            "CLINE_LOG_STREAM_JSON=1",
            "DEBIAN_FRONTEND=noninteractive",
            "NODE_OPTIONS=--dns-result-order=ipv4first",
        ]:
            cmd.extend(["--agent-env", kv])

    return cmd


def run_task(
    task_name: str,
    variant: str = "standard-fair",
    model_name: str = "openai:local-model",
    base_timeout_sec: int = 72000,
    max_output_tokens: int = 150000,
    max_total_tokens: int = 8000000,
    max_steps: int = 600,
    argon_rate: float = 100.0,
    local_rate: float = 0.0,
    host_llm_url: str = "http://127.0.0.1:8080/v1",
) -> Tuple[bool, str]:
    task_path = TASKS_DIR / task_name
    if not task_path.exists():
        print(f"[-] Task directory not found: {task_path}")
        print(f"    Ensure 'frontier_swe_v2' repository is cloned into {TASKS_DIR.parent}")
        return False, "INFRA_ERROR"

    if not ensure_docker_daemon():
        return False, "INFRA_ERROR"

    llm_ok, detected_model = check_local_llm(host_llm_url)
    if not llm_ok:
        print(f"[!] ERROR: Local LLM server is unreachable at {host_llm_url}.")
        print("    Please start your model with 'D:\\LocalAI\\start_brain.bat' first.")
        return False, "INFRA_ERROR"

    adapter_info = inspect_installed_harbor_cline_adapter()
    sep = adapter_info.get("separator", ":")
    if model_name in ("openai/local-model", "openai:local-model"):
        target_model_id = detected_model if (detected_model and detected_model != "local-model") else "local-model"
        model_name = f"openai{sep}{target_model_id}"

    container_llm_url = verify_container_to_brain_handshake(host_llm_url, detected_model or "local-model")
    prepare_task_toml_for_local_brain(task_path, container_llm_url)
    max_cpus, max_mem_mb = get_docker_host_limits()

    if variant == "token-scaled" and local_rate <= 0:
        measured = measure_live_token_rate(host_llm_url)
        local_rate = measured if measured else 25.0

    if variant == "standard-fair":
        effective_timeout = base_timeout_sec
        timeout_mult = "1.0"
        mode_desc = (
            f"STANDARD FAIR BENCHMARK (Max Output: {max_output_tokens:,} tok | "
            f"Max Total: {max_total_tokens:,} tok | Max Steps: {max_steps} | Clock: {effective_timeout:,}s)"
        )
    elif variant == "token-scaled":
        ratio = round(argon_rate / max(local_rate, 1.0), 2)
        effective_timeout = int(base_timeout_sec * ratio)
        timeout_mult = str(ratio)
        mode_desc = (
            f"HARDWARE-NORMALIZED TOKEN-SCALED (Ref: {argon_rate} t/s vs Local: {local_rate} t/s "
            f"-> {ratio}x = {effective_timeout:,}s | Max Output: {max_output_tokens:,} tok)"
        )
    elif variant == "token-capped":
        effective_timeout = base_timeout_sec
        timeout_mult = "1.0"
        mode_desc = (
            f"STRICT TOKEN-CAPPED (Max Output: {max_output_tokens:,} tok | "
            f"Max Total: {max_total_tokens:,} tok | Clock: {effective_timeout:,}s)"
        )
    else:
        effective_timeout = base_timeout_sec
        timeout_mult = "1.0"
        max_output_tokens = 0
        max_total_tokens = 0
        max_steps = 800
        mode_desc = f"OFFICIAL 20-HOUR WALL-CLOCK ({effective_timeout:,}s | Max Steps: {max_steps})"

    skill_flags = build_harbor_skill_flags(task_name)
    active_skill_names = [s.name for s in select_skills_for_task(task_name)]

    cmd = build_compatible_harbor_cmd(
        task_path=task_path,
        model_name=model_name,
        timeout_mult=timeout_mult,
        effective_timeout=effective_timeout,
        skill_flags=skill_flags,
        container_llm_url=container_llm_url,
    )

    cached_tarball = BASE_DIR / "harbor_cache" / "tarball" / "cline_nvm_v2.tar.gz"
    cache_status = "SSD Tarball Ready (~2s restore)" if cached_tarball.exists() else "First-Run Build (saves SSD Tarball)"

    print(f"\n{'='*78}")
    print(f"  STARTING FRONTIERSWE V2 TASK: {task_name.upper()} [{RUNNER_VERSION}]")
    print(f"  Mode:          {mode_desc}")
    print(f"  Model:         {model_name} (Server ID: {detected_model})")
    print(f"  Container URL: {container_llm_url}")
    print(f"  Host Limits:   {max_cpus} CPUs | {max_mem_mb:,} MB RAM | Setup Timeout: 2,400s ({cache_status})")
    print(f"  Skills:        {', '.join(active_skill_names)}")
    print(f"  Cmd:           {' '.join(cmd)}")
    print(f"{'='*78}\n")

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LIVE_SETUP_LOG.write_text(f"=== Container Setup Live Stream for {task_name} ===\n", encoding="utf-8")
    except Exception:
        pass

    write_active_task_state(
        task_name=task_name,
        phase="Starting Container & Installing Cline",
        model_name=model_name,
        skills=active_skill_names,
        detail=f"Connected to {container_llm_url} ({max_cpus} CPUs | {cache_status})",
    )

    host_ips = get_host_ipv4_candidates()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLINE_LOG_STREAM_JSON"] = "1"
    env["BASE_URL"] = container_llm_url
    env["OPENAI_BASE_URL"] = container_llm_url
    env["OPENAI_API_BASE"] = container_llm_url
    env["API_KEY"] = "sk-local-ai"
    env["OPENAI_API_KEY"] = "sk-local-ai"
    env["APEX_MAX_DOCKER_CPUS"] = str(max_cpus)
    env["APEX_MAX_DOCKER_MEM_MB"] = str(max_mem_mb)
    env["APEX_CONTAINER_LLM_URL"] = container_llm_url
    if host_ips:
        env["APEX_HOST_IPV4"] = host_ips[0]
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{BASE_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(BASE_DIR)

    t_start = time.time()
    watchdog = None

    try:
        proc = subprocess.Popen(cmd, env=env)
        watchdog = FairBudgetWatchdog(
            task_name=task_name,
            t_start=t_start,
            max_output_tokens=max_output_tokens,
            max_total_tokens=max_total_tokens,
            max_steps=max_steps,
            proc=proc,
        )
        watchdog.start()

        ret = proc.wait()
        elapsed = time.time() - t_start
    except Exception as e:
        print(f"[-] Execution error on {task_name}: {e}")
        elapsed = time.time() - t_start
        ret = -1
    finally:
        if watchdog:
            watchdog.stop_requested = True
        clear_active_task_state()
        try:
            subprocess.run(["docker", "container", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            subprocess.run(["docker", "volume", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass

    outcome = parse_harbor_trial_result(task_name, t_start)
    reward = outcome["reward"]
    p_tok = max(outcome["prompt_tokens"], watchdog.prompt_tokens if watchdog else 0)
    g_tok = max(outcome["output_tokens"], watchdog.output_tokens if watchdog else 0)
    steps = max(outcome["steps"], watchdog.steps if watchdog else 0)
    zero_token_run = (p_tok == 0 and g_tok == 0 and (reward is None or float(reward) <= 0.0))

    if (reward is None and (elapsed < 10.0 or ret != 0)) or zero_token_run:
        status = "INFRA_ERROR"
        reward_val = 0.0
        infra_ok = False
        print(f"\n[!] INFRASTRUCTURE / AGENT CONNECTION ERROR on '{task_name}' ({elapsed:.1f}s, exit code {ret}):")
        print(f"    Prompt Tokens: {p_tok} | Output Tokens: {g_tok} | Steps: {steps}")
        if outcome["diagnostics"]:
            print("    --- Container Agent Diagnostics ---")
            for dline in outcome["diagnostics"].splitlines()[-35:]:
                print(f"    {dline}")
            print("    -----------------------------------")
            if outcome["trial_dir"]:
                try:
                    cpath = Path(outcome["trial_dir"]) / "agent" / "cline.txt"
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    diag_ev = {
                        "type": "error",
                        "message": f"[INFRA_ERROR on {task_name}] Agent generated 0 tokens.\n{outcome['diagnostics']}",
                    }
                    with open(cpath, "a", encoding="utf-8") as cf:
                        cf.write(json.dumps(diag_ev) + "\n")
                except Exception:
                    pass
        print("    [*] This trial is marked INFRA_ERROR (not counted as a 0.00 failure) so you can safely re-run.")
    else:
        reward_val = float(reward) if reward is not None else 0.0
        infra_ok = True
        if reward_val >= 1.0:
            status = "PASSED"
        elif reward_val > 0.0:
            status = "PARTIAL"
        else:
            status = "FAILED"

    board = load_scoreboard()
    board["completed_tasks"][task_name] = {
        "status": status,
        "reward": round(reward_val, 4),
        "infra_ok": infra_ok,
        "variant": variant,
        "elapsed_sec": round(elapsed, 2),
        "prompt_tokens": p_tok,
        "output_tokens": g_tok,
        "tokens_evaluated": p_tok + g_tok,
        "steps": steps,
        "skills_used": active_skill_names,
        "timestamp": datetime.now().isoformat(),
    }
    save_scoreboard(board)

    print(
        f"\n[+] Task {task_name} finished in {elapsed:.1f}s -> {status} "
        f"(Reward: {reward_val:.4f} | Prompt Tokens: {p_tok:,} | Output Tokens: {g_tok:,} | Steps: {steps})"
    )
    return reward_val >= 1.0, status


def main():
    print(f"[*] Apex FrontierSWE v2 Runner {RUNNER_VERSION}")
    install_harbor_runtime_interceptor()
    patched_count = patch_all_tasks_for_local_brain()
    if patched_count:
        print(f"[+] Pre-clamped CPU & memory limits across {patched_count} task.toml files.")

    parser = argparse.ArgumentParser(description="FrontierSWE v2 Standardized & Fair Evaluation Harness")
    parser.add_argument("--task", "-t", type=str, help="Specific task name to run (e.g. astronomy-toolkit)")
    parser.add_argument("--all", "-a", action="store_true", help="Run all 34 tasks sequentially")
    parser.add_argument("--list", "-l", action="store_true", help="List all 34 tasks and current scoreboard")
    parser.add_argument("--clean-scoreboard", action="store_true", help="Remove corrupted/0-token crash entries from scoreboard")
    parser.add_argument(
        "--variant", "-v",
        choices=["standard-fair", "token-scaled", "token-capped", "official-time", "time"],
        default="standard-fair",
        help="Evaluation mode: standard-fair (default, fair output-token + step cap), token-scaled, token-capped, or official-time",
    )
    parser.add_argument("--max-output-tokens", type=int, default=150000, help="Max generated output tokens per task (default: 150,000)")
    parser.add_argument("--max-total-tokens", type=int, default=8000000, help="Max cumulative prompt+output tokens per task (default: 8,000,000)")
    parser.add_argument("--max-steps", type=int, default=600, help="Max agent turns/iterations per task (default: 600)")
    parser.add_argument("--token-cap", type=int, default=None, help="Legacy alias for --max-output-tokens")
    parser.add_argument("--argon-rate", type=float, default=100.0, help="Reference frontier cloud tokens/sec (default: 100.0)")
    parser.add_argument("--local-rate", type=float, default=0.0, help="Local LLM tokens/sec (0 = auto-measure live)")
    parser.add_argument("--timeout", type=int, default=72000, help="Base agent timeout in seconds (default: 72000 / 20 hours)")
    parser.add_argument("--model", type=str, default="openai:local-model", help="Model name for Harbor/Cline")
    parser.add_argument("--force-restart", action="store_true", help="Re-run all tasks even if already completed")
    args = parser.parse_args()

    if args.token_cap is not None:
        args.max_output_tokens = args.token_cap

    if args.clean_scoreboard:
        removed = clean_corrupted_scoreboard_entries()
        print(f"[+] Cleaned {removed} corrupted/0-token entries from {SCOREBOARD_FILE}.")
        return

    if args.list:
        clean_corrupted_scoreboard_entries()
        board = recompute_scoreboard_aggregates(load_scoreboard())
        print("\n=== FRONTIERSWE V2 TASKS (34 TOTAL) ===")
        print(
            f"Mean Score: {board.get('mean_reward_percent', 0.0):.2f}% | "
            f"Full Pass: {board.get('passed', 0)}/{board.get('total_done', 0)} | "
            f"Partial: {board.get('partial', 0)} | Infra Errors: {board.get('infra_errors', 0)}\n"
        )
        for i, t in enumerate(FRONTIER_TASKS, 1):
            exists = (TASKS_DIR / t).exists()
            past = board.get("completed_tasks", {}).get(t)
            if past:
                rew = past.get("reward")
                rew_str = f"reward={rew:.4f}" if isinstance(rew, (int, float)) else "unverified"
                gtok = past.get("output_tokens", 0) or 0
                status = f"{past.get('status')} ({rew_str}, {gtok:,} out_tok, {past.get('elapsed_sec')}s)"
            else:
                status = "Ready" if exists else "Missing task dir"
            print(f"  [{i:02d}] {t:40s} [{status}]")
        print()
        return

    if args.task:
        run_task(
            args.task,
            variant=args.variant,
            model_name=args.model,
            base_timeout_sec=args.timeout,
            max_output_tokens=args.max_output_tokens,
            max_total_tokens=args.max_total_tokens,
            max_steps=args.max_steps,
            argon_rate=args.argon_rate,
            local_rate=args.local_rate,
        )
    elif args.all:
        purged = clean_corrupted_scoreboard_entries()
        if purged:
            print(f"[*] Auto-cleaned {purged} prior 0-token/infra-error entries from scoreboard.")

        board = load_scoreboard()
        completed = {
            k for k, v in board.get("completed_tasks", {}).items()
            if v.get("status") != "INFRA_ERROR"
            and v.get("infra_ok", False)
            and ((v.get("prompt_tokens", 0) or 0) > 0 or (v.get("output_tokens", 0) or 0) > 0 or float(v.get("reward", 0.0) or 0.0) > 0.0)
        } if not args.force_restart else set()
        remaining = [t for t in FRONTIER_TASKS if t not in completed]

        print(f"\n[*] Total tasks: {len(FRONTIER_TASKS)} | Valid completed: {len(completed)} | Remaining: {len(remaining)}")
        print(f"[*] Evaluation Variant: {args.variant.upper()}")

        consecutive_infra_errors = 0
        for idx, t in enumerate(remaining, 1):
            print(f"\n>>> Running [{idx}/{len(remaining)}]: {t}")
            _, status = run_task(
                t,
                variant=args.variant,
                model_name=args.model,
                base_timeout_sec=args.timeout,
                max_output_tokens=args.max_output_tokens,
                max_total_tokens=args.max_total_tokens,
                max_steps=args.max_steps,
                argon_rate=args.argon_rate,
                local_rate=args.local_rate,
            )
            if status == "INFRA_ERROR":
                consecutive_infra_errors += 1
                if consecutive_infra_errors >= 2:
                    print("\n[!] ABORTING BATCH: 2 consecutive tasks exited with INFRA_ERROR (0 tokens generated).")
                    print("    Review the 'Container Agent Diagnostics' printed above before restarting.")
                    break
            else:
                consecutive_infra_errors = 0
    else:
        print("\n[!] Please specify --task <name>, --all, --list, or --clean-scoreboard")
        parser.print_help()


if __name__ == "__main__":
    main()