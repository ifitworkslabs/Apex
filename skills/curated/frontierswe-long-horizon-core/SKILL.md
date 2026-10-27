---
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
