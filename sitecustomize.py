r"""
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


install_all_hooks()