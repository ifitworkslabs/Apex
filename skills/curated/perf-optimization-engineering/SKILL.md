---
name: perf-optimization-engineering
description: General performance engineering principles: profile baseline metrics, preserve correctness invariants, and optimize hot loops.
---
# General Performance Engineering Principles

1. **Measure Before & After Every Change**:
   - Run the provided benchmark or verification script first to establish a working baseline.
   - Verify that all correctness tests pass after every optimization; never trade required correctness for speed.
