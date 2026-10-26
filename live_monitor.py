import os
import sys
import time
import json
from pathlib import Path

# Ensure Windows PowerShell / CMD prints Unicode & ANSI without crashing
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(r"D:\LocalAI")
JOBS_DIR = BASE_DIR / "frontierswe_jobs"

class ClineStreamFormatter:
    """Formats live JSON-stream events from Cline into a beautiful terminal output."""
    def __init__(self):
        self.in_text = False

    def process_line(self, raw_line: str):
        raw_line = raw_line.strip()
        if not raw_line or not raw_line.startswith('{'):
            if '__CLINE_EXIT=' in raw_line:
                print(f"\n\033[95m[CLI EXIT] {raw_line}\033[0m\n", flush=True)
            return

        try:
            d = json.loads(raw_line)
        except Exception:
            return

        ev = d.get('event', {})
        etype = ev.get('type')
        ctype = ev.get('contentType')

        if etype == 'iteration_start':
            num = ev.get('iteration', '?')
            print(f"\n\033[1;35m══════════════════════════════ [ ITERATION {num} ] ══════════════════════════════\033[0m", flush=True)
            self.in_text = False

        elif ctype == 'text':
            if etype == 'content_start':
                chunk = ev.get('text', '')
                if not self.in_text:
                    print("\033[36m💭 [THINKING]:\033[0m ", end='', flush=True)
                    self.in_text = True
                print(f"\033[36m{chunk}\033[0m", end='', flush=True)
            elif etype == 'content_end':
                print("\n\033[32m✔ [THOUGHT COMPLETE]\033[0m\n", flush=True)
                self.in_text = False

        elif ctype == 'tool':
            tname = ev.get('toolName', 'tool')
            if etype == 'content_start':
                print(f"\033[1;33m⚡ [ACTION: {tname}]\033[0m", flush=True)
                inp = ev.get('input', {})
                if tname == 'run_commands':
                    for c in inp.get('commands', []):
                        print(f"   \033[33m$ {c}\033[0m", flush=True)
                elif tname == 'editor':
                    path = inp.get('path', '')
                    nchars = len(inp.get('new_text', ''))
                    print(f"   \033[33mEditing File: {path} ({nchars} chars)\033[0m", flush=True)
                else:
                    print(f"   \033[33mInput: {inp}\033[0m", flush=True)
            elif etype == 'content_end':
                dur = ev.get('durationMs', 0)
                out = ev.get('output')
                print(f"\033[1;34m📄 [RESULT ({dur}ms)]:\033[0m", flush=True)
                if isinstance(out, list):
                    for item in out:
                        q = item.get('query', '')
                        res = item.get('result', '').strip()
                        err = item.get('error')
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
                    err = out.get('error')
                    res = out.get('result')
                    if res:
                        print(f"     {res}", flush=True)
                    if err:
                        print(f"     \033[31mError: {err}\033[0m", flush=True)
                print("", flush=True)

        elif etype == 'usage':
            inp_t = ev.get('inputTokens', 0)
            out_t = ev.get('outputTokens', 0)
            print(f"\033[90m📊 [STATS] Tokens: {inp_t} prompt, {out_t} generated\033[0m\n", flush=True)

def get_latest_cline_log() -> Path | None:
    if not JOBS_DIR.exists():
        return None
    candidates = list(JOBS_DIR.rglob("agent/cline.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

def main():
    print("\033[1;36m====================================================================\033[0m")
    print("\033[1;36m        LOCAL AI LIVE THINKING & DOING MONITOR (Terminal-Bench 2.0)  \033[0m")
    print("\033[1;36m====================================================================\033[0m")
    print(f"\033[90mWatching directory: {JOBS_DIR}\033[0m\n")

    current_log = None
    file_handle = None
    formatter = ClineStreamFormatter()

    try:
        while True:
            latest = get_latest_cline_log()

            if latest != current_log and latest is not None:
                # Switched to a new log
                if file_handle:
                    file_handle.close()
                current_log = latest
                task_name = current_log.parents[2].name
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
