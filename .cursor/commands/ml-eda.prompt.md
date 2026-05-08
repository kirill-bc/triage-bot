---
mode: 'agent'
description: 'Plan and execute the first pass of ML-focused exploratory data analysis'
---

# ML EDA Jumpstart

Goal: Produce a compact plan plus notebook-ready steps for exploratory data analysis tailored to the current ML problem.

Preconditions:
- Primary dataset location identified (local file, DB, or API).
- Basic problem statement available (prediction/regression/classification/etc.).

Instructions:
1) Restate the ML objective and data sources in 1-2 sentences.
2) Outline data access steps (including sample loading code if applicable) with dependency notes.
3) Enumerate critical checks: schema, missing values, class balance, target leakage risks, and data drift (if historical data exists).
4) List high-value visualizations and summary tables (e.g., distributions, correlations, stratified plots) with the reasoning behind each.
5) Capture immediate follow-ups: data fixes, feature ideas, or questions for stakeholders.
6) Recommend tooling (pandas, polars, duckdb, visualization libs) and notebook/script structure to execute the plan reproducibly.
7) Flag compliance/PII handling requirements and storage considerations when relevant.
8) Convert planned actions into TODO entries (or confirm they are captured) so `TODO.md` reflects the EDA workload.

Acceptance:
- Output fits in ≤ 30 actionable bullet points or steps.
- Each step maps clearly to either inspection, visualization, or follow-up action.
- Provides at least one reproducibility tip (seed setting, data snapshot, or versioning guidance).
- Highlights red-flag findings discovered during the EDA planning.
- Ensures `TODO.md` is updated with the agreed EDA tasks.