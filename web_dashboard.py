import os
import sys
import time
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

DASHBOARD_VERSION = "v2.5 (Fixed JS Raw-String + Live Setup & Agent Stream)"
BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
JOBS_DIR = BASE_DIR / "frontierswe_jobs"
SCOREBOARD_FILE = BASE_DIR / "frontierswe_scoreboard.json"
ACTIVE_TASK_FILE = BASE_DIR / "current_active_task.json"
LIVE_SETUP_LOG = BASE_DIR / "container_setup_live.log"

app = FastAPI(title="Local AI Live Benchmark Dashboard")


def extract_task_name_from_log(log_path: Path) -> str:
    """
    Extracts `<task_name>` from `<job_id>/<task_name>__<trial_hash>/agent/...`.
    Walks up parents to find the folder containing `__`.
    """
    if log_path == LIVE_SETUP_LOG:
        state = get_active_runner_state()
        if state and state.get("task"):
            return state["task"]
        return "Container Setup"
    try:
        for parent in log_path.parents:
            if "__" in parent.name and parent.parent != JOBS_DIR:
                return parent.name.split("__")[0]
            if parent.parent == JOBS_DIR and "__" in parent.name:
                return parent.name.split("__")[0]
        return log_path.parents[1].name.split("__")[0]
    except Exception:
        return "Unknown Task"


def get_latest_cline_log() -> Optional[Path]:
    """
    Finds the most recently updated agent log file across `agent/cline.txt`,
    `agent/command-1/stdout.txt`, `agent/command-1/stderr.txt`, `agent/setup/stderr.txt`,
    and `D:\\LocalAI\\container_setup_live.log`.
    """
    candidates = []
    if LIVE_SETUP_LOG.exists():
        try:
            st = LIVE_SETUP_LOG.stat()
            if st.st_size > 0 and (time.time() - st.st_mtime < 120):
                candidates.append((st.st_mtime, True, LIVE_SETUP_LOG))
        except Exception:
            pass

    if JOBS_DIR.exists():
        try:
            job_dirs = [d for d in JOBS_DIR.iterdir() if d.is_dir()]
            job_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            for jdir in job_dirs[:6]:
                for pattern in (
                    "*/agent/cline.txt",
                    "*/agent/command-1/stdout.txt",
                    "*/agent/command-1/stderr.txt",
                    "*/agent/setup/stderr.txt",
                ):
                    for p in jdir.glob(pattern):
                        try:
                            st = p.stat()
                            if st.st_size > 0:
                                bonus = 10.0 if p.name == "cline.txt" else 2.0
                                candidates.append((st.st_mtime + bonus, True, p))
                        except Exception:
                            pass
        except Exception:
            pass

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][2]


def get_active_runner_state() -> Optional[Dict[str, Any]]:
    if ACTIVE_TASK_FILE.exists():
        try:
            data = json.loads(ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
            if time.time() - float(data.get("updated_at", 0)) < 7200:
                return data
        except Exception:
            pass
    return None


def get_scoreboard_data() -> Dict[str, Any]:
    if SCOREBOARD_FILE.exists():
        try:
            return json.loads(SCOREBOARD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
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


@app.get("/api/status")
async def get_status():
    latest_log = get_latest_cline_log()
    runner_state = get_active_runner_state()
    board = get_scoreboard_data()
    active_task = "Idle / Waiting"
    phase = "Idle"
    is_active = False
    mtime = 0.0

    if runner_state and runner_state.get("task"):
        active_task = runner_state["task"]
        phase = runner_state.get("phase", "Running")
        is_active = True
        mtime = float(runner_state.get("updated_at", time.time()))

    if latest_log:
        log_mtime = latest_log.stat().st_mtime
        if log_mtime > mtime or active_task == "Idle / Waiting":
            active_task = extract_task_name_from_log(latest_log)
            mtime = log_mtime
        if time.time() - log_mtime < 180:
            is_active = True
            phase = "Streaming Agent"

    return {
        "active_task": active_task,
        "phase": phase,
        "is_active": is_active,
        "last_update": datetime.fromtimestamp(mtime).isoformat() if mtime else None,
        "scoreboard": board,
    }


@app.get("/api/poll")
async def poll_updates(
    log_path: str = Query(default=""),
    offset: int = Query(default=0),
):
    """
    Stateless, non-blocking incremental log + status endpoint (< 3ms response time).
    """
    latest_log = get_latest_cline_log()
    runner_state = get_active_runner_state()
    board = get_scoreboard_data()

    active_task = "Idle / Waiting"
    phase = "Idle"
    detail = ""
    if runner_state and runner_state.get("task"):
        active_task = runner_state["task"]
        phase = runner_state.get("phase", "Running")
        detail = runner_state.get("detail", "")

    new_lines: List[str] = []
    current_log_str = str(latest_log) if latest_log else ""
    next_offset = offset
    task_switched = False

    if latest_log and latest_log.exists():
        log_task = extract_task_name_from_log(latest_log)
        if active_task == "Idle / Waiting" or (time.time() - latest_log.stat().st_mtime < 180):
            active_task = log_task
            if latest_log.name == "cline.txt":
                phase = "Streaming Agent"

        if current_log_str != log_path:
            task_switched = True
            next_offset = 0

        try:
            file_size = latest_log.stat().st_size
            if next_offset > file_size:
                next_offset = 0
            if file_size > next_offset:
                with open(latest_log, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(next_offset)
                    chunk = f.read(256 * 1024)
                    next_offset = f.tell()
                for line in chunk.splitlines():
                    s = line.strip()
                    if s:
                        new_lines.append(s)
        except Exception:
            pass

    return {
        "status": "ok",
        "active_task": active_task,
        "phase": phase,
        "detail": detail,
        "log_path": current_log_str,
        "log_name": latest_log.name if latest_log else "",
        "task_switched": task_switched,
        "next_offset": next_offset,
        "lines": new_lines[:200],
        "scoreboard": board,
    }


@app.get("/api/stream")
async def sse_stream(request: Request):
    async def event_generator():
        current_file = None
        f = None
        try:
            yield f"data: {json.dumps({'type': 'connected', 'status': 'ok'})}\n\n"
            while not await request.is_disconnected():
                latest = get_latest_cline_log()
                if latest and latest != current_file:
                    if f:
                        f.close()
                    current_file = latest
                    try:
                        f = open(current_file, "r", encoding="utf-8", errors="replace")
                        task_name = extract_task_name_from_log(current_file)
                        yield f"data: {json.dumps({'type': 'task_switch', 'task': task_name, 'log': str(current_file.name)})}\n\n"
                    except Exception:
                        f = None

                if f:
                    line = f.readline()
                    if line:
                        line = line.strip()
                        if line.startswith("{"):
                            yield f"data: {line}\n\n"
                        elif line:
                            yield f"data: {json.dumps({'type': 'plain_log', 'text': line})}\n\n"
                    else:
                        await asyncio.sleep(0.25)
                else:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1.0)
        finally:
            if f:
                try:
                    f.close()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# NOTE: MUST BE A RAW STRING (r"""...""") so \n inside JS strings is NOT turned
# into a literal multi-line newline by Python!
HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Local AI Live Reasoning & Action Dashboard (v2.5)</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(15, 23, 42, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --accent-cyan: #38bdf8;
      --accent-green: #10b981;
      --accent-yellow: #f59e0b;
      --accent-purple: #a855f7;
      --accent-red: #ef4444;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: radial-gradient(circle at 10% 20%, #0d1527 0%, var(--bg) 90%);
      color: var(--text);
      font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }
    header {
      padding: 16px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(16px);
      background: rgba(9, 13, 22, 0.8);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .pulse-dot {
      width: 10px; height: 10px; border-radius: 50%;
      background: var(--accent-green);
      box-shadow: 0 0 12px var(--accent-green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.3); }
    }
    .brand h1 {
      font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em;
      background: linear-gradient(135deg, #fff 30%, var(--accent-cyan));
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .badge {
      font-size: 0.75rem; padding: 4px 10px; border-radius: 20px;
      font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
      background: rgba(56, 189, 248, 0.15);
      border: 1px solid rgba(56, 189, 248, 0.3);
      color: var(--accent-cyan);
    }
    .container {
      display: grid; grid-template-columns: 1fr 360px;
      gap: 20px; padding: 20px 28px; flex: 1;
    }
    .main-col, .side-col { display: flex; flex-direction: column; gap: 20px; }
    .card {
      background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 14px; backdrop-filter: blur(12px);
      overflow: hidden; display: flex; flex-direction: column;
    }
    .card-header {
      padding: 12px 18px; border-bottom: 1px solid var(--card-border);
      display: flex; justify-content: space-between; align-items: center;
      background: rgba(255, 255, 255, 0.02);
    }
    .card-title {
      font-size: 0.85rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.05em; color: var(--text-muted);
      display: flex; align-items: center; gap: 8px;
    }
    .thought-box {
      padding: 18px; font-family: Consolas, 'JetBrains Mono', monospace;
      font-size: 0.92rem; line-height: 1.6; color: #bae6fd;
      background: rgba(0, 0, 0, 0.25); min-height: 180px; max-height: 280px;
      overflow-y: auto; white-space: pre-wrap; word-break: break-word;
    }
    .events-feed {
      padding: 16px; display: flex; flex-direction: column;
      gap: 12px; max-height: 480px; overflow-y: auto;
    }
    .event-card {
      background: rgba(0, 0, 0, 0.3); border-left: 3px solid var(--accent-cyan);
      border-radius: 6px; padding: 10px 14px;
      font-family: Consolas, 'JetBrains Mono', monospace; font-size: 0.85rem;
    }
    .event-card.tool { border-left-color: var(--accent-yellow); }
    .event-card.result { border-left-color: var(--accent-green); }
    .event-card.error { border-left-color: var(--accent-red); background: rgba(239, 68, 68, 0.12); }
    .event-card.iteration {
      border-left-color: var(--accent-purple);
      background: rgba(168, 85, 247, 0.1); font-weight: bold;
    }
    .stat-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 16px; }
    .stat-pill {
      background: rgba(0, 0, 0, 0.25); border: 1px solid var(--card-border);
      border-radius: 10px; padding: 12px; text-align: center;
    }
    .stat-pill .val { font-size: 1.4rem; font-weight: 700; color: var(--accent-cyan); margin-top: 4px; }
    .stat-pill .lbl { font-size: 0.72rem; text-transform: uppercase; color: var(--text-muted); }
    .scoreboard-list {
      padding: 12px; max-height: 400px; overflow-y: auto;
      display: flex; flex-direction: column; gap: 8px;
    }
    .score-item {
      display: flex; justify-content: space-between; align-items: center;
      padding: 8px 12px; background: rgba(0, 0, 0, 0.2);
      border-radius: 8px; font-size: 0.85rem;
    }
    .score-item .name { max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tag { padding: 2px 8px; border-radius: 12px; font-size: 0.7rem; font-weight: bold; }
    .tag.pass { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); }
    .tag.partial { background: rgba(245, 158, 11, 0.2); color: var(--accent-yellow); }
    .tag.fail { background: rgba(239, 68, 68, 0.2); color: var(--accent-red); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="pulse-dot"></div>
      <h1>LOCAL SOTA REASONING MONITOR</h1>
      <span class="badge" id="model-badge">FrontierSWE v2.5</span>
    </div>
    <div style="display: flex; gap: 12px; align-items: center;">
      <span style="font-size: 0.85rem; color: var(--text-muted);">ACTIVE TASK:</span>
      <span id="active-task-badge" class="badge" style="background: rgba(16, 185, 129, 0.15); border-color: rgba(16, 185, 129, 0.35); color: var(--accent-green);">Connected • Idle</span>
    </div>
  </header>

  <div class="container">
    <div class="main-col">
      <div class="card">
        <div class="card-header">
          <div class="card-title">💭 Live Reasoning Monologue & Container Stream</div>
          <span style="font-size: 0.75rem; color: var(--accent-green);" id="thought-status">🟢 Connected • Live (v2.5)</span>
        </div>
        <div class="thought-box" id="thought-box">Dashboard connected to D:\LocalAI. Waiting for a task container to start...</div>
      </div>

      <div class="card" style="flex: 1;">
        <div class="card-header">
          <div class="card-title">⚡ Actions, Tools & Terminal Execution</div>
          <span style="font-size: 0.75rem; color: var(--text-muted);" id="iter-badge">Iter 1</span>
        </div>
        <div class="events-feed" id="events-feed"></div>
      </div>
    </div>

    <div class="side-col">
      <div class="card">
        <div class="card-header">
          <div class="card-title">📊 Token & Official Mean Score</div>
        </div>
        <div class="stat-row">
          <div class="stat-pill">
            <div class="lbl">Prompt Tokens</div>
            <div class="val" id="val-prompt">0</div>
          </div>
          <div class="stat-pill">
            <div class="lbl">Generated</div>
            <div class="val" id="val-gen">0</div>
          </div>
          <div class="stat-pill">
            <div class="lbl">Mean Score</div>
            <div class="val" id="val-passrate">0%</div>
          </div>
          <div class="stat-pill">
            <div class="lbl">Passed / Done</div>
            <div class="val" id="val-ratio">0/0</div>
          </div>
        </div>
      </div>

      <div class="card" style="flex: 1;">
        <div class="card-header">
          <div class="card-title">🏆 Benchmark Scoreboard</div>
          <span style="font-size: 0.7rem; color: var(--accent-cyan);">Fable 5.1: 56.3% | Argon 4: 55.0%</span>
        </div>
        <div class="scoreboard-list" id="score-list">
          <div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;">No completed trials yet...</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const thoughtBox = document.getElementById('thought-box');
    const thoughtStatus = document.getElementById('thought-status');
    const eventsFeed = document.getElementById('events-feed');
    const activeTaskBadge = document.getElementById('active-task-badge');
    const iterBadge = document.getElementById('iter-badge');
    const valPrompt = document.getElementById('val-prompt');
    const valGen = document.getElementById('val-gen');
    const valPassrate = document.getElementById('val-passrate');
    const valRatio = document.getElementById('val-ratio');
    const scoreList = document.getElementById('score-list');

    let currentThought = "";
    let cumOutputTokens = 0;
    let currentLogPath = "";
    let currentOffset = 0;
    let isPolling = false;

    function escapeHtml(str) {
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function appendEvent(type, title, detail, cls = "") {
      const el = document.createElement('div');
      el.className = `event-card ${cls}`;
      el.innerHTML = `<strong>${escapeHtml(title)}</strong><div style="margin-top: 4px; color: #cbd5e1; white-space: pre-wrap; font-size: 0.8rem;">${escapeHtml(detail)}</div>`;
      eventsFeed.appendChild(el);
      while (eventsFeed.children.length > 150) {
        eventsFeed.removeChild(eventsFeed.firstChild);
      }
      eventsFeed.scrollTop = eventsFeed.scrollHeight;
    }

    function appendThoughtText(chunk) {
      if (!chunk) return;
      currentThought += chunk;
      if (currentThought.length > 16000) {
        currentThought = currentThought.slice(-16000);
      }
      thoughtBox.innerText = currentThought;
      thoughtBox.scrollTop = thoughtBox.scrollHeight;
    }

    function processLogLine(rawLine) {
      if (!rawLine) return;
      if (!rawLine.startsWith("{")) {
        appendThoughtText(rawLine + "\n");
        return;
      }
      try {
        const data = JSON.parse(rawLine);
        if (data.type === 'error') {
          const msg = data.message || JSON.stringify(data);
          appendThoughtText("\n[ERROR] " + msg + "\n");
          appendEvent('error', '❌ AGENT / CONTAINER ERROR', msg, 'error');
          return;
        }
        if (data.type === 'run_result') {
          const u = data.usage || {};
          if (u.inputTokens || u.prompt_tokens) {
            valPrompt.innerText = Number(u.inputTokens || u.prompt_tokens).toLocaleString();
          }
          if (u.outputTokens || u.completion_tokens) {
            valGen.innerText = Number(u.outputTokens || u.completion_tokens).toLocaleString();
          }
          if (data.text) {
            appendThoughtText("\n" + data.text + "\n");
          }
          appendEvent('result', `🏁 RUN FINISHED (${data.finishReason || 'done'})`, `Iterations: ${data.iterations || 1}`, 'result');
          return;
        }

        const ev = data.event || {};
        if (ev.type === 'iteration_start') {
          iterBadge.innerText = `Iter ${ev.iteration || 1}`;
          appendEvent('iteration', `🔄 ITERATION ${ev.iteration || 1}`, 'Starting deliberation step...', 'iteration');
        } else if (
          (ev.contentType === 'text' || ev.contentType === 'reasoning' || !ev.contentType) &&
          (ev.type === 'content_start' || ev.type === 'content_update' || ev.type === 'content_delta')
        ) {
          appendThoughtText(ev.text || ev.delta || ev.content || '');
        } else if (ev.contentType === 'tool' && (ev.type === 'content_start' || ev.type === 'content_update')) {
          if (ev.type === 'content_start') {
            const name = ev.toolName || 'tool';
            const inp = JSON.stringify(ev.input || {}, null, 2);
            appendEvent('tool', `⚡ TOOL CALL: ${name}`, inp, 'tool');
          }
        } else if (ev.contentType === 'tool' && ev.type === 'content_end') {
          const out = JSON.stringify(ev.output || {}, null, 2).slice(0, 1500);
          appendEvent('result', `📄 RESULT (${ev.durationMs || 0}ms)`, out, 'result');
        } else if (ev.type === 'error' || ev.type === 'notice') {
          const msg = ev.message || ev.text || JSON.stringify(ev);
          appendThoughtText("\n[" + ev.type.toUpperCase() + "] " + msg + "\n");
          appendEvent('error', `⚠️ ${ev.type.toUpperCase()}`, msg, ev.type === 'error' ? 'error' : '');
        } else if (ev.type === 'done') {
          if (ev.text) appendThoughtText("\n" + ev.text + "\n");
        } else if (ev.type === 'usage') {
          valPrompt.innerText = (ev.inputTokens || 0).toLocaleString();
          cumOutputTokens += (ev.outputTokens || 0);
          valGen.innerText = cumOutputTokens.toLocaleString();
        } else if (ev.stats) {
          valPrompt.innerText = (ev.stats.tokens || 0).toLocaleString();
          valGen.innerText = (ev.stats.generated || 0).toLocaleString();
        }
      } catch (e) {
        appendThoughtText(rawLine + "\n");
      }
    }

    function renderScoreboard(b) {
      if (!b) return;
      const meanPct = b.mean_reward_percent !== undefined ? b.mean_reward_percent : (b.pass_rate_percent || 0);
      valPassrate.innerText = `${meanPct}%`;
      valRatio.innerText = `${b.passed || 0}/${b.total_done || 0}`;

      const completed = b.completed_tasks || {};
      const entries = Object.entries(completed);
      if (entries.length > 0) {
        scoreList.innerHTML = entries.map(([name, info]) => {
          const hasReward = info.reward !== undefined && info.reward !== null;
          const rew = hasReward ? Number(info.reward) : (info.status === 'PASSED' ? 1.0 : 0.0);
          let tagCls = 'fail';
          let tagTxt = `FAIL ${rew.toFixed(2)}`;
          if (info.status === 'INFRA_ERROR') {
            tagCls = 'fail';
            tagTxt = 'INFRA ERR';
          } else if (rew >= 1.0) {
            tagCls = 'pass';
            tagTxt = `PASS ${rew.toFixed(2)}`;
          } else if (rew > 0.0) {
            tagCls = 'partial';
            tagTxt = `PARTIAL ${rew.toFixed(3)}`;
          }
          return `<div class="score-item">
            <span class="name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
            <span class="tag ${tagCls}">${escapeHtml(tagTxt)}</span>
          </div>`;
        }).join('');
      } else {
        scoreList.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;">No completed trials yet...</div>`;
      }
    }

    async function pollDashboard() {
      if (isPolling) return;
      isPolling = true;
      try {
        const url = `/api/poll?log_path=${encodeURIComponent(currentLogPath)}&offset=${currentOffset}`;
        const res = await fetch(url, { cache: 'no-store' });
        const data = await res.json();

        thoughtStatus.innerText = `🟢 Connected • ${data.phase || 'Live'} (v2.5)`;
        activeTaskBadge.innerText = data.active_task || 'Connected • Idle';

        if (data.task_switched && data.log_path) {
          currentLogPath = data.log_path;
          currentThought = "";
          cumOutputTokens = 0;
          eventsFeed.innerHTML = "";
          appendEvent('iteration', '🚀 ATTACHED TO TASK LOG', `${data.active_task} (${data.log_name})`, 'iteration');
        }

        if (data.next_offset !== undefined) {
          currentOffset = data.next_offset;
        }

        if (Array.isArray(data.lines) && data.lines.length > 0) {
          for (const line of data.lines) {
            processLogLine(line);
          }
        } else if (!currentThought && data.active_task && data.active_task !== 'Idle / Waiting') {
          thoughtBox.innerText = "[Harbor Runner] Active Task: " + data.active_task + " (" + (data.phase || 'Running') + ")\n" + (data.detail || '');
        }

        renderScoreboard(data.scoreboard);
      } catch (e) {
        thoughtStatus.innerText = '🔴 Reconnecting to localhost:8088...';
      } finally {
        isPolling = false;
      }
    }

    setInterval(pollDashboard, 500);
    pollDashboard();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(
        content=HTML_CONTENT,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


if __name__ == "__main__":
    print("==================================================================")
    print(f"      LOCAL AI REASONING DASHBOARD {DASHBOARD_VERSION}")
    print("      Open your browser at: http://localhost:8088                 ")
    print("==================================================================")
    uvicorn.run(app, host="0.0.0.0", port=8088, log_level="warning")