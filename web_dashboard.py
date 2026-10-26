import os
import sys
import time
import json
import asyncio
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

BASE_DIR = Path(r"D:\LocalAI")
JOBS_DIR = BASE_DIR / "frontierswe_jobs"
SCOREBOARD_FILE = BASE_DIR / "frontierswe_scoreboard.json"

app = FastAPI(title="Local AI Live Benchmark Dashboard")

def get_latest_cline_log() -> Path | None:
    if not JOBS_DIR.exists():
        return None
    candidates = list(JOBS_DIR.rglob("agent/cline.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

def get_scoreboard_data():
    if SCOREBOARD_FILE.exists():
        try:
            return json.loads(SCOREBOARD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed_tasks": {}, "passed": 0, "total_done": 0, "pass_rate_percent": 0.0}

@app.get("/api/status")
async def get_status():
    latest_log = get_latest_cline_log()
    board = get_scoreboard_data()
    active_task = "Idle / Waiting"
    is_active = False
    mtime = 0

    if latest_log:
        active_task = latest_log.parents[2].name
        mtime = latest_log.stat().st_mtime
        if time.time() - mtime < 120:
            is_active = True

    return {
        "active_task": active_task,
        "is_active": is_active,
        "last_update": datetime.fromtimestamp(mtime).isoformat() if mtime else None,
        "scoreboard": board
    }

@app.get("/api/stream")
async def sse_stream():
    """Streams live Cline events to the web frontend using Server-Sent Events."""
    async def event_generator():
        current_file = None
        f = None
        while True:
            latest = get_latest_cline_log()
            if latest and latest != current_file:
                if f:
                    f.close()
                current_file = latest
                try:
                    f = open(current_file, "r", encoding="utf-8", errors="replace")
                    task_name = current_file.parents[2].name
                    yield f"data: {json.dumps({'type': 'task_switch', 'task': task_name})}\n\n"
                except Exception:
                    f = None

            if f:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line.startswith("{"):
                        yield f"data: {line}\n\n"
                    elif "__CLINE_EXIT=" in line:
                        yield f"data: {json.dumps({'type': 'exit', 'message': line})}\n\n"
                else:
                    await asyncio.sleep(0.15)
            else:
                await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Local AI Live Reasoning & Action Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
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
      font-family: 'Outfit', sans-serif;
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
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .pulse-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent-green);
      box-shadow: 0 0 12px var(--accent-green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.3); }
    }
    .brand h1 {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      background: linear-gradient(135deg, #fff 30%, var(--accent-cyan));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .badge {
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 20px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: rgba(56, 189, 248, 0.15);
      border: 1px solid rgba(56, 189, 248, 0.3);
      color: var(--accent-cyan);
    }
    .container {
      display: grid;
      grid-template-columns: 1fr 340px;
      gap: 20px;
      padding: 20px 28px;
      flex: 1;
    }
    .main-col {
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      backdrop-filter: blur(12px);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .card-header {
      padding: 12px 18px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, 0.02);
    }
    .card-title {
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .thought-box {
      padding: 18px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.92rem;
      line-height: 1.6;
      color: #bae6fd;
      background: rgba(0, 0, 0, 0.25);
      min-height: 180px;
      max-height: 280px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .events-feed {
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-height: 480px;
      overflow-y: auto;
    }
    .event-card {
      background: rgba(0, 0, 0, 0.3);
      border-left: 3px solid var(--accent-cyan);
      border-radius: 6px;
      padding: 10px 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
    }
    .event-card.tool {
      border-left-color: var(--accent-yellow);
    }
    .event-card.result {
      border-left-color: var(--accent-green);
    }
    .event-card.iteration {
      border-left-color: var(--accent-purple);
      background: rgba(168, 85, 247, 0.1);
      font-weight: bold;
    }
    .side-col {
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .stat-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      padding: 16px;
    }
    .stat-pill {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 12px;
      text-align: center;
    }
    .stat-pill .val {
      font-size: 1.4rem;
      font-weight: 700;
      color: var(--accent-cyan);
      margin-top: 4px;
    }
    .stat-pill .lbl {
      font-size: 0.72rem;
      text-transform: uppercase;
      color: var(--text-muted);
    }
    .scoreboard-list {
      padding: 12px;
      max-height: 400px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .score-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      background: rgba(0, 0, 0, 0.2);
      border-radius: 8px;
      font-size: 0.85rem;
    }
    .score-item .name {
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .tag {
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 0.7rem;
      font-weight: bold;
    }
    .tag.pass { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); }
    .tag.fail { background: rgba(239, 68, 68, 0.2); color: var(--accent-red); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="pulse-dot"></div>
      <h1>LOCAL SOTA REASONING MONITOR</h1>
      <span class="badge" id="model-badge">Qwen3-Coder-30B</span>
    </div>
    <div style="display: flex; gap: 12px; align-items: center;">
      <span style="font-size: 0.85rem; color: var(--text-muted);">ACTIVE TASK:</span>
      <span id="active-task-badge" class="badge" style="background: rgba(168, 85, 247, 0.15); border-color: rgba(168, 85, 247, 0.3); color: var(--accent-purple);">Connecting...</span>
    </div>
  </header>

  <div class="container">
    <div class="main-col">
      <!-- Live Thought Stream -->
      <div class="card">
        <div class="card-header">
          <div class="card-title">💭 Live Reasoning Monologue</div>
          <span style="font-size: 0.75rem; color: var(--text-muted);" id="thought-status">Streaming</span>
        </div>
        <div class="thought-box" id="thought-box">Waiting for agent to deliberate...</div>
      </div>

      <!-- Action & Tool Events Feed -->
      <div class="card" style="flex: 1;">
        <div class="card-header">
          <div class="card-title">⚡ Actions, Tools & Terminal Execution</div>
          <span style="font-size: 0.75rem; color: var(--text-muted);" id="iter-badge">Iter 1</span>
        </div>
        <div class="events-feed" id="events-feed"></div>
      </div>
    </div>

    <!-- Right Column: Stats & Scoreboard -->
    <div class="side-col">
      <div class="card">
        <div class="card-header">
          <div class="card-title">📊 Token & Speed Metrics</div>
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
            <div class="lbl">Pass Rate</div>
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
          <span style="font-size: 0.7rem; color: var(--accent-cyan);">Argon 4: 57.4%</span>
        </div>
        <div class="scoreboard-list" id="score-list">
          <div style="color: var(--text-muted); font-size: 0.8rem; text-align: center; padding: 20px;">Loading tasks...</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const thoughtBox = document.getElementById('thought-box');
    const eventsFeed = document.getElementById('events-feed');
    const activeTaskBadge = document.getElementById('active-task-badge');
    const iterBadge = document.getElementById('iter-badge');
    const valPrompt = document.getElementById('val-prompt');
    const valGen = document.getElementById('val-gen');
    const valPassrate = document.getElementById('val-passrate');
    const valRatio = document.getElementById('val-ratio');
    const scoreList = document.getElementById('score-list');

    let currentThought = "";

    function appendEvent(type, title, detail, cls = "") {
      const el = document.createElement('div');
      el.className = `event-card ${cls}`;
      el.innerHTML = `<strong>${title}</strong><div style="margin-top: 4px; color: #cbd5e1; white-space: pre-wrap; font-size: 0.8rem;">${detail}</div>`;
      eventsFeed.appendChild(el);
      eventsFeed.scrollTop = eventsFeed.scrollHeight;
    }

    const evtSource = new EventSource("/api/stream");
    evtSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'task_switch') {
          activeTaskBadge.innerText = data.task;
          thoughtBox.innerText = "";
          currentThought = "";
          eventsFeed.innerHTML = "";
          appendEvent('iteration', '🚀 NEW TASK STARTED', data.task, 'iteration');
          return;
        }

        const ev = data.event || {};
        if (ev.type === 'iteration_start') {
          iterBadge.innerText = `Iter ${ev.iteration || 1}`;
          appendEvent('iteration', `🔄 ITERATION ${ev.iteration || 1}`, 'Starting next deliberation step...', 'iteration');
        } else if (ev.contentType === 'text' && ev.type === 'content_start') {
          currentThought += (ev.text || '');
          thoughtBox.innerText = currentThought;
          thoughtBox.scrollTop = thoughtBox.scrollHeight;
        } else if (ev.contentType === 'tool' && ev.type === 'content_start') {
          const name = ev.toolName || 'tool';
          const inp = JSON.stringify(ev.input || {}, null, 2);
          appendEvent('tool', `⚡ TOOL CALL: ${name}`, inp, 'tool');
        } else if (ev.contentType === 'tool' && ev.type === 'content_end') {
          const out = JSON.stringify(ev.output || {}, null, 2);
          appendEvent('result', `📄 RESULT (${ev.durationMs || 0}ms)`, out, 'result');
        } else if (ev.type === 'usage') {
          valPrompt.innerText = ev.inputTokens || 0;
          valGen.innerText = ev.outputTokens || 0;
        }
      } catch (err) {
        console.error(err);
      }
    };

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.active_task && activeTaskBadge.innerText === 'Connecting...') {
          activeTaskBadge.innerText = data.active_task;
        }
        const b = data.scoreboard || {};
        valPassrate.innerText = `${b.pass_rate_percent || 0}%`;
        valRatio.innerText = `${b.passed || 0}/${b.total_done || 0}`;

        const completed = b.completed_tasks || {};
        const entries = Object.entries(completed);
        if (entries.length > 0) {
          scoreList.innerHTML = entries.map(([name, info]) => {
            const isPass = (info.reward || 0) >= 1.0;
            const tagCls = isPass ? 'pass' : 'fail';
            const tagTxt = isPass ? 'PASS 1.0' : `FAIL ${info.reward || 0}`;
            return `<div class="score-item">
              <span class="name" title="${name}">${name}</span>
              <span class="tag ${tagCls}">${tagTxt}</span>
            </div>`;
          }).join('');
        }
      } catch (e) {}
    }

    setInterval(fetchStatus, 3000);
    fetchStatus();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=HTML_CONTENT)

if __name__ == "__main__":
    print("==================================================================")
    print("      LOCAL AI REASONING DASHBOARD SERVER LAUNCHING               ")
    print("      Open your browser at: http://localhost:8088                 ")
    print("==================================================================")
    uvicorn.run(app, host="0.0.0.0", port=8088, log_level="warning")
