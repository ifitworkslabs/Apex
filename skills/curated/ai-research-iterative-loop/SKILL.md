---
name: ai-research-iterative-loop
description: General ML engineering workflow: validate the end-to-end pipeline on a small split first and checkpoint working models.
---
# General ML Experimentation Principles

1. **Fast End-to-End Smoke Test First**:
   - Verify data loading, model forward/backward passes, evaluation scripts, and artifact saving on a small subset before running longer jobs, and save working checkpoints.
