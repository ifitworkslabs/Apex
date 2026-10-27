import os
import sys
import time
import json
from pathlib import Path
from typing import Optional

# Ensure Windows PowerShell / CMD prints Unicode & ANSI without crashing
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
JOBS_DIR = BASE_DIR / "frontierswe_jobs"


def extract_task_name_from_log(log_path: Path) -> str:
    """Extracts the task name from `<job_dir>/<task_name>__<hash>/agent/...`."""
    try:
        for parent in log_path.parents:
            if "__" in parent.name:
                return parent.name.split("__")[0]
        return log_path.parents[1].name
    except Exception:
        return "Unknown Task"


class ClineStreamFormatter:
    """Formats live JSON-stream and plain-text events from Cline into terminal output."""
    def __init__(self):
        self.in_text = False
        self.cum_output_tokens = 0

    def process_line(self, raw_line: str):
        raw_line = raw_line.strip()
        if not raw_line:
            return
        if not raw_line.startswith("{"):
            if "__CLINE_EXIT=" in raw_line:
                print(f"\n\033[95m[CLI EXIT] {raw_line}\033[0m\n", flush=True)
            else:
                print(f"\033[90m[LOG] {raw_line}\033[0m", flush=True)
            return

        try:
            d = json.loads(raw_line)
        except Exception:
            print(f"\033[90m[LOG] {raw_line}\033[0m", flush=True)
            return

        if d.get("type") == "error":
            print(f"\n\033[1;31m❌ [ERROR]: {d.get('message', d)}\033[0m\n", flush=True)
            return
        if d.get("type") == "run_result":
            u = d.get("usage", {})
            print(
                f"\n\033[1;32m🏁 [RUN RESULT]: finishReason={d.get('finishReason')} | "
                f"iterations={d.get('iterations')} | usage={u}\033[0m\n",
                flush=True,
            )
            return

        ev = d.get("event", {})
        if not isinstance(ev, dict):
            return
        etype = ev.get("type")
        ctype = ev.get("contentType")

        if etype == "iteration_start":
            num = ev.get("iteration", "?")
            print(f"\n\033[1;35m══════════════════════════════ [ ITERATION {num} ] ══════════════════════════════\033[0m", flush=True)
            self.in_text = False

        elif ctype in ("text", "reasoning") or (ctype is None and etype in ("content_start", "content_update", "content_delta")):
            if etype in ("content_start", "content_update", "content_delta"):
                chunk = ev.get("text", "") or ev.get("delta", "") or ev.get("content", "")
                if chunk:
                    if not self.in_text:
                        print("\033[36m💭 [THINKING]:\033[0m ", end="", flush=True)
                        self.in_text = True
                    print(f"\033[36m{chunk}\033[0m", end="", flush=True)
            elif etype == "content_end":
                if self.in_text:
                    print("\n\033[32m✔ [THOUGHT COMPLETE]\033[0m\n", flush=True)
                    self.in_text = False

        elif ctype == "tool":
            tname = ev.get("toolName", "tool")
            if etype == "content_start":
                print(f"\033[1;33m⚡ [ACTION: {tname}]\033[0m", flush=True)
                inp = ev.get("input", {})
                if tname == "run_commands":
                    for c in inp.get("commands", []):
                        print(f"   \033[33m$ {c}\033[0m", flush=True)
                elif tname == "editor":
                    path = inp.get("path", "")
                    nchars = len(inp.get("new_text", ""))
                    print(f"   \033[33mEditing File: {path} ({nchars} chars)\033[0m", flush=True)
                else:
                    print(f"   \033[33mInput: {inp}\033[0m", flush=True)
            elif etype == "content_end":
                dur = ev.get("durationMs", 0)
                out = ev.get("output")
                print(f"\033[1;34m📄 [RESULT ({dur}ms)]:\033[0m", flush=True)
                if isinstance(out, list):
                    for item in out:
                        if not isinstance(item, dict):
                            continue
                        q = item.get("query", "")
                        res = str(item.get("result", "")).strip()
                        err = item.get("error")
                        if q:
                            print(f"   \033[90m$ {q}\033[0m", flush=True)
                        if res:
                            lines = res.splitlines()[:6]
                            for l in lines:
                                print(f"     {l}", flush=True)
                            if len(res.splitlines()) > 6:
                                print(f"     \033[90m... ({len(res.splitlines())-6} more lines)\033[0m", flush=True)
                        if err and err != res:
                            print(f"     \033[31mError: {err}\033[0m", flush=True)
                elif isinstance(out, dict):
                    err = out.get("error")
                    res = out.get("result")
                    if res:
                        print(f"     {res}", flush=True)
                    if err:
                        print(f"     \033[31mError: {err}\033[0m", flush=True)
                print("", flush=True)

        elif etype in ("error", "notice"):
            print(f"\n\033[1;31m⚠️ [{etype.upper()}]: {ev.get('message') or ev.get('text') or ev}\033[0m\n", flush=True)

        elif etype == "usage":
            inp_t = int(ev.get("inputTokens", 0) or 0)
            out_t = int(ev.get("outputTokens", 0) or 0)
            self.cum_output_tokens += out_t
            print(
                f"\033[90m📊 [STATS] Turn Prompt: {inp_t:,} | Turn Output: {out_t:,} | "
                f"Cumulative Generated: {self.cum_output_tokens:,}\033[0m\n",
                flush=True,
            )


def get_latest_cline_log() -> Optional[Path]:
    if not JOBS_DIR.exists():
        return None
    candidates = []
    for pattern in (
        "agent/cline.txt",
        "agent/command-1/stdout.txt",
        "agent/command-1/stderr.txt",
        "agent/setup/stderr.txt",
    ):
        for p in JOBS_DIR.rglob(pattern):
            try:
                st = p.stat()
                if st.st_size > 0 or pattern == "agent/cline.txt":
                    candidates.append((st.st_mtime, st.st_size > 0, p))
            except Exception:
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0] + (5.0 if item[1] else 0.0)))
    return candidates[-1][2]


def main():
    print("\033[1;36m====================================================================\033[0m")
    print("\033[1;36m        LOCAL AI LIVE THINKING & DOING MONITOR (FrontierSWE v2)     \033[0m")
    print("\033[1;36m====================================================================\033[0m")
    print(f"\033[90mWatching directory: {JOBS_DIR}\033[0m\n")

    current_log = None
    file_handle = None
    formatter = ClineStreamFormatter()

    try:
        while True:
            latest = get_latest_cline_log()

            if latest != current_log and latest is not None:
                if file_handle:
                    file_handle.close()
                current_log = latest
                task_name = extract_task_name_from_log(current_log)
                print(f"\n\033[1;32m>>> [MONITOR ATTACHED] Active Task: {task_name}\033[0m")
                print(f"\033[90m    Path: {current_log}\033[0m\n")
                formatter = ClineStreamFormatter()
                file_handle = open(current_log, "r", encoding="utf-8", errors="replace")

            if file_handle:
                line = file_handle.readline()
                if line:
                    formatter.process_line(line)
                else:
                    time.sleep(0.15)
            else:
                time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n\033[90m[MONITOR DETACHED] Exiting...\033[0m")
        if file_handle:
            file_handle.close()


if __name__ == "__main__":
    main()