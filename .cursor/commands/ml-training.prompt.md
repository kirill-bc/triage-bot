---
mode: 'agent'
description: 'Bootstrap model training workflow with reproducible experiments and baselines'
---

# ML Training Jumpstart

Goal: Define the minimal reproducible pipeline for training, validating, and tracking ML models for the current problem.

Preconditions:
- Core dataset is profiled or scoped (see ML EDA Jumpstart output).
- Target variable and evaluation metric candidates are identified.

Instructions:
1) Summarize the prediction task, target, and success metric(s) in plain language.
2) Specify data splits (train/validation/test or cross-validation) with rationale and leakage safeguards.
3) Recommend baseline model(s) and at least two upgrade candidates (e.g., linear models, tree-based, neural net) with required libraries.
4) Outline the training loop structure, including feature preprocessing, hyperparameter sweep strategy, and evaluation checkpoints.
5) Detail experiment tracking: directory layout, logging format, and tool choices (MLflow, Weights & Biases, simple CSV, etc.).
6) Capture resource considerations: hardware requirements, batching, mixed precision, or cloud vs. local execution.
7) Enumerate tests/monitors to guard against regressions (unit tests on feature builders, metric thresholds, dataset version checks).
8) Provide a reproducibility checklist: random seed policy, config management, environment capture (requirements.txt/conda/pip-tools), and data snapshot guidance.
9) Suggest next-step automation (CI job, scheduled retrain, deployment hooks) only if prerequisites are confirmed; otherwise mark as future work.
10) Translate the agreed actions into TODO items (or verify existing coverage) so `TODO.md` drives the upcoming ML work.

Acceptance:
- Produces a numbered action plan with owners/placeholders where unknown.
- Includes runnable starter commands or code snippets for baselines and tracking setup.
- Flags high-risk assumptions (data freshness, label noise, infra limits) explicitly.
- Stays within ≤ 35 lines to remain quick-start friendly.
- Confirms `TODO.md` captures the training tasks before exiting.