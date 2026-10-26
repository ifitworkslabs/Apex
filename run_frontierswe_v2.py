"""
=============================================================================
  FRONTIERSWE V2 BENCHMARK RUNNER & EVALUATION HARNESS
=============================================================================
Orchestrates FrontierSWE v2 evaluations with Harbor, Cline, and local LLM.
Supports:
  1. Standard Time-Budgeted Variant (official 20h / 72,000s per task)
  2. Token-Rate Scaled Variant (fair ratio scaled against Gemini 4 Argon)
  3. Strict Token-Capped Variant (capped by total prompt+generated tokens)

Maintains real-time scoreboard in D:\\LocalAI\\frontierswe_scoreboard.json
and streams live reasoning to D:\\LocalAI\\web_dashboard.py and live_monitor.py.
"""

import os
import sys
import json
import time
import shutil
import argparse
import threading
import subprocess
from pathlib import Path
from datetime import datetime

# Enforce UTF-8 on Windows Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(r"D:\LocalAI")
TASKS_DIR = BASE_DIR / "frontier_swe_v2" / "tasks"
JOBS_DIR = BASE_DIR / "frontierswe_jobs"
SCOREBOARD_FILE = BASE_DIR / "frontierswe_scoreboard.json"
SKILLS_DIR = Path(os.environ.get("USERPROFILE", r"C:\Users\Administrator")) / ".cline" / "skills"

# 34 FrontierSWE v2 Tasks
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

def ensure_docker_daemon():
    """Verifies Docker daemon is responding, and starts it unattended if needed."""
    try:
        res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    print("[*] Docker daemon not responding. Auto-starting Docker backend (WSL2 unattended)...")
    backend_exe = r"C:\Program Files\Docker\Docker\resources\com.docker.backend.exe"
    if os.path.exists(backend_exe):
        subprocess.Popen([backend_exe, "-unattended", "-with-frontend=false", "-engine", "WSL2"])
        for _ in range(15):
            time.sleep(2)
            try:
                if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    print("  [+] Docker backend started successfully!")
                    return True
            except Exception:
                pass
    print("[!] Warning: Docker could not be automatically confirmed. Proceeding anyway...")
    return False

def check_local_llm():
    """Checks if the local LLM server is accessible."""
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False

def load_scoreboard():
    if SCOREBOARD_FILE.exists():
        try:
            return json.loads(SCOREBOARD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "benchmark": "FrontierSWE v2",
        "started_at": datetime.now().isoformat(),
        "total_tasks": len(FRONTIER_TASKS),
        "completed_tasks": {},
        "passed": 0,
        "failed": 0,
        "total_done": 0,
        "pass_rate_percent": 0.0
    }

def save_scoreboard(data):
    try:
        SCOREBOARD_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[!] Warning: Failed to write scoreboard: {e}")

class TokenCapWatchdog(threading.Thread):
    """
    Monitors cumulative tokens generated during the run.
    If total tokens reach or exceed token_cap, signals the process to conclude.
    """
    def __init__(self, task_name: str, token_cap: int, proc: subprocess.Popen):
        super().__init__(daemon=True)
        self.task_name = task_name
        self.token_cap = token_cap
        self.proc = proc
        self.stop_requested = False
        self.total_tokens_seen = 0

    def run(self):
        while not self.stop_requested and self.proc.poll() is None:
            time.sleep(2.0)
            # Find the active cline.txt log file in JOBS_DIR
            candidate_logs = list(JOBS_DIR.rglob("agent/cline.txt"))
            if not candidate_logs:
                continue
            latest_log = max(candidate_logs, key=lambda p: p.stat().st_mtime)
            
            # Read and sum tokens
            try:
                with open(latest_log, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("{"):
                            try:
                                d = json.loads(line)
                                stats = d.get("event", {}).get("stats", {})
                                p_tok = stats.get("tokens", 0)
                                g_tok = stats.get("generated", 0)
                                if p_tok or g_tok:
                                    self.total_tokens_seen = max(self.total_tokens_seen, p_tok + g_tok)
                            except Exception:
                                pass
            except Exception:
                continue

            if self.total_tokens_seen >= self.token_cap:
                print(f"\n[!] TOKEN CAP REACHED ({self.total_tokens_seen:,} / {self.token_cap:,} tokens)!")
                print(f"[*] Gracefully concluding agent task '{self.task_name}' for verifier grading...")
                try:
                    self.proc.terminate()
                except Exception:
                    pass
                break

def run_task(
    task_name: str,
    variant: str = "token-scaled",
    model_name: str = "openai/local-model",
    base_timeout_sec: int = 72000,
    token_cap: int = 500000,
    argon_rate: float = 100.0,
    local_rate: float = 18.9
):
    task_path = TASKS_DIR / task_name
    if not task_path.exists():
        print(f"[-] Task directory not found: {task_path}")
        return False

    ensure_docker_daemon()

    # Calculate effective timeout and multiplier based on variant
    if variant == "token-scaled":
        ratio = round(argon_rate / local_rate, 2)
        effective_timeout = int(base_timeout_sec * ratio)
        mode_desc = f"TOKEN-RATE SCALED (Gemini 4 Argon: {argon_rate} t/s vs Local: {local_rate} t/s -> {ratio}x scale = {effective_timeout:,}s)"
        timeout_mult = str(ratio)
    elif variant == "token-capped":
        effective_timeout = base_timeout_sec
        mode_desc = f"STRICT TOKEN CAPPED (Max Tokens: {token_cap:,} tokens | Time Cap: {effective_timeout:,}s)"
        timeout_mult = "1.0"
    else:
        effective_timeout = base_timeout_sec
        mode_desc = f"STANDARD TIME-BUDGETED ({effective_timeout:,}s per task)"
        timeout_mult = "1.0"

    print(f"\n{'='*75}")
    print(f"  STARTING FRONTIERSWE V2 TASK: {task_name.upper()}")
    print(f"  Mode: {mode_desc}")
    print(f"  Jobs Directory: {JOBS_DIR}")
    print(f"{'='*75}\n")

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLINE_LOG_STREAM_JSON"] = "1"
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:8080/v1"
    env["OPENAI_API_KEY"] = "sk-local-ai"

    # Harbor command with -o (--jobs-dir)
    cmd = [
        "harbor", "run",
        "-p", str(task_path),
        "-o", str(JOBS_DIR),
        "--agent", "cline-cli",
        "--model", model_name,
        "--skills", str(SKILLS_DIR),
        "--agent-timeout-multiplier", timeout_mult,
        "--agent-kwarg", f"timeout_sec={effective_timeout}"
    ]

    t_start = time.time()
    passed = False
    watchdog = None

    try:
        proc = subprocess.Popen(cmd, env=env)
        if variant == "token-capped":
            watchdog = TokenCapWatchdog(task_name, token_cap, proc)
            watchdog.start()

        ret = proc.wait()
        elapsed = time.time() - t_start
        passed = (ret == 0)
    except Exception as e:
        print(f"[-] Execution error on {task_name}: {e}")
        elapsed = time.time() - t_start
        passed = False
    finally:
        if watchdog:
            watchdog.stop_requested = True

    board = load_scoreboard()
    board["completed_tasks"][task_name] = {
        "status": "PASSED" if passed else "FAILED",
        "variant": variant,
        "elapsed_sec": round(elapsed, 2),
        "tokens_evaluated": getattr(watchdog, "total_tokens_seen", 0) if watchdog else None,
        "timestamp": datetime.now().isoformat()
    }
    board["total_done"] = len(board["completed_tasks"])
    board["passed"] = sum(1 for v in board["completed_tasks"].values() if v.get("status") == "PASSED")
    board["failed"] = board["total_done"] - board["passed"]
    board["pass_rate_percent"] = round((board["passed"] / board["total_done"]) * 100, 2) if board["total_done"] > 0 else 0.0
    save_scoreboard(board)

    print(f"\n[+] Task {task_name} finished in {elapsed:.1f}s -> {'PASSED' if passed else 'FAILED'}")
    return passed

def main():
    parser = argparse.ArgumentParser(description="FrontierSWE v2 Automated Runner & Evaluation Harness")
    parser.add_argument("--task", "-t", type=str, help="Specific task name to run (e.g. astronomy-toolkit)")
    parser.add_argument("--all", "-a", action="store_true", help="Run all 34 tasks sequentially")
    parser.add_argument("--list", "-l", action="store_true", help="List all available tasks and scoreboard status")
    parser.add_argument("--variant", "-v", choices=["token-scaled", "token-capped", "time"], default="token-scaled",
                        help="Benchmark evaluation variant: token-scaled (default, Gemini 4 Argon fair ratio), token-capped, or time")
    parser.add_argument("--token-cap", type=int, default=500000, help="Max total tokens for token-capped variant (default: 500,000)")
    parser.add_argument("--argon-rate", type=float, default=100.0, help="Gemini 4 Argon baseline tokens/sec (default: 100.0)")
    parser.add_argument("--local-rate", type=float, default=18.9, help="Local LLM measured tokens/sec (default: 18.9 for Qwen 30B MoE)")
    parser.add_argument("--timeout", type=int, default=72000, help="Base agent timeout in seconds (default: 72000 / 20 hours)")
    parser.add_argument("--model", type=str, default="openai/local-model", help="Model name for Harbor/Cline")
    parser.add_argument("--force-restart", action="store_true", help="Do not skip previously completed tasks")
    args = parser.parse_args()

    if args.list:
        print("\n=== FRONTIERSWE V2 TASKS (34 TOTAL) ===")
        board = load_scoreboard()
        for i, t in enumerate(FRONTIER_TASKS, 1):
            exists = (TASKS_DIR / t).exists()
            past = board.get("completed_tasks", {}).get(t)
            if past:
                status = f"Completed ({past.get('status')}) - {past.get('elapsed_sec')}s"
            else:
                status = "Ready" if exists else "Missing"
            print(f"  [{i:02d}] {t:40s} [{status}]")
        print()
        return

    # Pre-flight check
    if not check_local_llm():
        print("[!] Local LLM server is not responding at http://127.0.0.1:8080/v1.")
        print("    Please start your model with 'D:\\LocalAI\\start_brain.bat' before running.")
    ensure_docker_daemon()

    if args.task:
        run_task(
            args.task,
            variant=args.variant,
            model_name=args.model,
            base_timeout_sec=args.timeout,
            token_cap=args.token_cap,
            argon_rate=args.argon_rate,
            local_rate=args.local_rate
        )
    elif args.all:
        board = load_scoreboard()
        completed = set(board.get("completed_tasks", {}).keys()) if not args.force_restart else set()
        remaining = [t for t in FRONTIER_TASKS if t not in completed]
        
        print(f"\n[*] Total tasks: {len(FRONTIER_TASKS)} | Already completed: {len(completed)} | Remaining: {len(remaining)}")
        print(f"[*] Evaluation Variant: {args.variant.upper()}")

        for idx, t in enumerate(remaining, 1):
            print(f"\n>>> Running [{idx}/{len(remaining)}]: {t}")
            run_task(
                t,
                variant=args.variant,
                model_name=args.model,
                base_timeout_sec=args.timeout,
                token_cap=args.token_cap,
                argon_rate=args.argon_rate,
                local_rate=args.local_rate
            )
    else:
        print("\n[!] Please specify --task <name>, --all, or --list")
        parser.print_help()

if __name__ == "__main__":
    main()
