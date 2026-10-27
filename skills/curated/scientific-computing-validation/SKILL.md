---
name: scientific-computing-validation
description: General scientific computing workflow: check numerical stability, units/coordinate conventions, memory bounds on large datasets, and validation residuals.
---
# General Scientific Computing Principles

1. **Empirical Validation & Numerical Hygiene**:
   - Inspect sample inputs and reference development outputs (if present in the workspace) to verify coordinate conventions, units, array shapes, and file headers.
   - Check input file sizes before loading datasets into memory; use streaming or filtering when processing gigabyte-scale files.
   - Measure quantitative residuals on development datasets and verify that all output schemas and headers pass validation before finishing.
