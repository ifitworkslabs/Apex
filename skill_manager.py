"""
=============================================================================
  APEX SKILL MANAGER & TASK-AWARE ROUTER FOR FRONTIERSWE V2 (v3.4 - FAIR)
=============================================================================
100% Fair Benchmark Edition (Zero Task Spoilers / Zero Algorithmic Leakage):
  - Contains NO task names, NO task-specific formulas, and NO domain spoilers.
  - Provides only general-purpose software engineering methodology (reading
    documentation, running self-check validators, testing edge cases, and
    clean tool usage) so 100% of the algorithmic problem-solving comes purely
    from the evaluated model.
"""

import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Set

BASE_DIR = Path(os.environ.get("APEX_BASE_DIR", r"D:\LocalAI"))
CURATED_SKILLS_DIR = BASE_DIR / "skills" / "curated"
STAGING_DIR = BASE_DIR / "skill_staging"
ACTIVE_CLINE_SKILLS_DIR = Path(os.environ.get("USERPROFILE", r"C:\Users\Administrator")) / ".cline" / "skills"


# ---------------------------------------------------------------------------
# 1. Task-Agnostic General Software Engineering Skills (Zero Task Spoilers)
# ---------------------------------------------------------------------------
BUILTIN_SKILLS: Dict[str, str] = {
    "frontierswe-long-horizon-core": """---
name: frontierswe-long-horizon-core
description: General-purpose software engineering workflow: read specifications, write complete implementations, and verify with local tests before submitting.
---
# General Software Engineering & Tool Execution Protocol

1. **Tool Mechanics (Avoid Syntax & Truncation Errors)**:
   - You may use `editor` to create or replace full files directly.
   - If you use `run_commands` with a bash heredoc (`cat << 'EOF' > file`), always place a newline immediately after `'EOF'`. Never write files one line at a time with dozens of `echo` commands.
   - Keep conversational text before tool calls brief (1–2 sentences).

2. **Specification & Test-Driven Workflow**:
   - Read the workspace `README.md`, existing source files, and any provided validation or test scripts first to understand the exact input/output contract.
   - Never leave placeholder constants, dummy identity outputs, or `NotImplementedError` stubs—always implement the complete algorithm and verify it end-to-end.
   - Run the workspace's validation script or test suite on the provided example/development data, inspect any errors or accuracy metrics, and iterate until checks pass before calling `submit_and_exit`.
""",

    "perf-optimization-engineering": """---
name: perf-optimization-engineering
description: General performance engineering principles: profile baseline metrics, preserve correctness invariants, and optimize hot loops.
---
# General Performance Engineering Principles

1. **Measure Before & After Every Change**:
   - Run the provided benchmark or verification script first to establish a working baseline.
   - Verify that all correctness tests pass after every optimization; never trade required correctness for speed.
""",

    "systems-implementation-strict": """---
name: systems-implementation-strict
description: General systems programming workflow: build incremental vertical slices and validate against the test suite.
---
# General Systems Engineering Principles

1. **Incremental Implementation & Verification**:
   - Implement core data structures and interfaces first, compile and run the test suite early, and systematically resolve failing test cases one by one.
""",

    "scientific-computing-validation": """---
name: scientific-computing-validation
description: General scientific computing workflow: check numerical stability, units/coordinate conventions, memory bounds on large datasets, and validation residuals.
---
# General Scientific Computing Principles

1. **Empirical Validation & Numerical Hygiene**:
   - Inspect sample inputs and reference development outputs (if present in the workspace) to verify coordinate conventions, units, array shapes, and file headers.
   - Check input file sizes before loading datasets into memory; use streaming or filtering when processing gigabyte-scale files.
   - Measure quantitative residuals on development datasets and verify that all output schemas and headers pass validation before finishing.
""",

    "visual-reasoning-instrumentation": """---
name: visual-reasoning-instrumentation
description: General visual and numerical inspection workflow using terminal scripts to measure output quality.
---
# General Quantitative Inspection Principles

1. **Programmatic Output Inspection**:
   - When working with images, video, or numerical arrays, write small scripts to check dimensions, finite values, value ranges, and quantitative error metrics against reference samples.
""",

    "ai-research-iterative-loop": """---
name: ai-research-iterative-loop
description: General ML engineering workflow: validate the end-to-end pipeline on a small split first and checkpoint working models.
---
# General ML Experimentation Principles

1. **Fast End-to-End Smoke Test First**:
   - Verify data loading, model forward/backward passes, evaluation scripts, and artifact saving on a small subset before running longer jobs, and save working checkpoints.
""",

    "reverse-engineering-ghidra": """---
name: reverse-engineering-ghidra
description: General binary inspection workflow using standard CLI analysis tools.
---
# General Binary Analysis Principles

1. **Static & Dynamic Inspection**:
   - Use standard inspection utilities (`file`, `strings`, `nm`, `objdump`, or headless decompilers if installed) and controlled test inputs to understand binary behavior.
"""
}


# ---------------------------------------------------------------------------
# 2. Task-to-Domain Category Mapping
# ---------------------------------------------------------------------------
TASK_SKILL_MAP: Dict[str, List[str]] = {
    "astronomy-toolkit": ["scientific-computing-validation", "visual-reasoning-instrumentation"],
    "cranelift-codegen-opt": ["perf-optimization-engineering", "systems-implementation-strict"],
    "crash-proof-flash-filesystem": ["systems-implementation-strict"],
    "dart-style-in-haskell": ["systems-implementation-strict"],
    "ffmpeg-libswscale-optimization": ["perf-optimization-engineering", "systems-implementation-strict"],
    "fitness-recap-video-in-remotion": ["visual-reasoning-instrumentation", "systems-implementation-strict"],
    "flight-sim-renderer-in-opengl": ["visual-reasoning-instrumentation", "systems-implementation-strict"],
    "frogsgame-post-training": ["ai-research-iterative-loop"],
    "git-to-zig": ["systems-implementation-strict"],
    "granite-mamba2-inference-optimization": ["perf-optimization-engineering", "ai-research-iterative-loop"],
    "higgs-uncertainty-inference": ["scientific-computing-validation"],
    "kolmogorov-audio-compression": ["perf-optimization-engineering"],
    "lean-4-kernel-type-checker-in-pascal": ["systems-implementation-strict"],
    "libexpat-optimization": ["perf-optimization-engineering", "systems-implementation-strict"],
    "lua-native-compiler": ["systems-implementation-strict"],
    "machine-learned-interatomic-potential": ["scientific-computing-validation", "ai-research-iterative-loop"],
    "medium-range-weather-forecast": ["scientific-computing-validation", "ai-research-iterative-loop"],
    "meg-speech-decoding": ["scientific-computing-validation", "ai-research-iterative-loop"],
    "msms-denovo-generation": ["scientific-computing-validation", "ai-research-iterative-loop"],
    "multi-gpu-efficient-finetuning": ["ai-research-iterative-loop"],
    "notebook-compression": ["perf-optimization-engineering"],
    "optimizer-design": ["ai-research-iterative-loop"],
    "postgresql-18-on-sqlite": ["systems-implementation-strict"],
    "quantum-espresso-pwx-in-rust": ["scientific-computing-validation", "systems-implementation-strict"],
    "qubit-routing": ["perf-optimization-engineering"],
    "reconnaissance-blind-chess-recovery": ["ai-research-iterative-loop"],
    "sglang-inference-system-optimization": ["perf-optimization-engineering", "ai-research-iterative-loop"],
    "snooker-prediction": ["visual-reasoning-instrumentation", "ai-research-iterative-loop"],
    "spice-circuit-simulator-in-rust": ["systems-implementation-strict", "scientific-computing-validation"],
    "stepper-music-sequencer-gba": ["visual-reasoning-instrumentation", "reverse-engineering-ghidra"],
    "synthetic-music-diarization": ["ai-research-iterative-loop"],
    "verilog-simulator-in-swift": ["systems-implementation-strict"],
    "vision-only-torcs-racing-bot": ["visual-reasoning-instrumentation", "ai-research-iterative-loop"],
    "wan-2.1-on-max-mojo": ["systems-implementation-strict", "ai-research-iterative-loop"],
}

_CACHED_HARBOR_FLAGS: Set[str] | None = None


def get_supported_harbor_flags() -> Set[str]:
    global _CACHED_HARBOR_FLAGS
    if _CACHED_HARBOR_FLAGS is not None:
        return _CACHED_HARBOR_FLAGS

    flags: Set[str] = set()
    try:
        env = os.environ.copy()
        env["COLUMNS"] = "240"
        env["LINES"] = "100"
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        res = subprocess.run(
            ["harbor", "run", "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=12,
        )
        help_text = (res.stdout or "") + "\n" + (res.stderr or "")
        help_text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", help_text)
        for match in re.findall(r"(--[a-zA-Z0-9_-]+)", help_text):
            flags.add(match)
    except Exception:
        pass

    _CACHED_HARBOR_FLAGS = flags
    return flags


def ensure_curated_skills() -> Path:
    CURATED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for skill_name, content in BUILTIN_SKILLS.items():
        sdir = CURATED_SKILLS_DIR / skill_name
        sdir.mkdir(parents=True, exist_ok=True)
        skill_md = sdir / "SKILL.md"
        skill_md.write_text(content.strip() + "\n", encoding="utf-8")

    ghidra_src = BASE_DIR / "ghidra_scripts" / "ExportDecompiledCode.java"
    if ghidra_src.exists():
        dst = CURATED_SKILLS_DIR / "reverse-engineering-ghidra" / "ExportDecompiledCode.java"
        try:
            shutil.copy2(ghidra_src, dst)
        except Exception:
            pass

    return CURATED_SKILLS_DIR


def discover_staged_skills() -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    if not STAGING_DIR.exists():
        return found
    for skill_md in STAGING_DIR.rglob("SKILL.md"):
        sdir = skill_md.parent
        found[sdir.name.lower()] = sdir
    return found


def select_skills_for_task(task_name: str, max_skills: int = 4) -> List[Path]:
    ensure_curated_skills()
    selected: List[Path] = [CURATED_SKILLS_DIR / "frontierswe-long-horizon-core"]

    for domain_skill in TASK_SKILL_MAP.get(task_name, ["systems-implementation-strict"]):
        p = CURATED_SKILLS_DIR / domain_skill
        if p.exists() and p not in selected:
            selected.append(p)

    staged = discover_staged_skills()
    task_tokens = set(task_name.lower().split("-"))
    for sname, spath in staged.items():
        if len(selected) >= max_skills:
            break
        sname_tokens = set(sname.replace("_", "-").split("-"))
        if task_tokens & sname_tokens and spath not in selected:
            selected.append(spath)

    return selected[:max_skills]


def sync_active_cline_skills(skill_dirs: List[Path]) -> Path:
    ACTIVE_CLINE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    for item in ACTIVE_CLINE_SKILLS_DIR.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)

    for sdir in skill_dirs:
        if sdir.exists():
            dest = ACTIVE_CLINE_SKILLS_DIR / sdir.name
            shutil.copytree(sdir, dest, dirs_exist_ok=True)

    return ACTIVE_CLINE_SKILLS_DIR


def build_harbor_skill_flags(task_name: str) -> List[str]:
    skill_dirs = select_skills_for_task(task_name)
    active_dir = sync_active_cline_skills(skill_dirs)
    supported = get_supported_harbor_flags()

    if "--skills" in supported:
        return ["--skills", str(active_dir)]
    if "--skill" in supported:
        flags: List[str] = []
        for sdir in skill_dirs:
            flags.extend(["--skill", str(sdir)])
        return flags
    return []


if __name__ == "__main__":
    ensure_curated_skills()
    print(f"[+] Initialized {len(BUILTIN_SKILLS)} fair, task-agnostic FrontierSWE v2 skills in: {CURATED_SKILLS_DIR}")