# Project Deep Research Brief

## 1) Executive Summary (2026-03-14)

- Project technical maturity: `high` (core architecture + research stack implemented).
- Business maturity: `medium/low` (formal rollout gate for Stage 6 currently `NO_GO`).
- Main bottleneck: not infrastructure, but stable positive post-cost edge on labeled historical data.

## 2) Consolidated Status by Stages

### TZ v2.1 (Algorithm Strengthening)

- Functional completion: `16/17`.
- Remaining item is time-dependent: business-effect confirmation on live/historical observation window.
- Reference: `docs/TZ_STATUS.md`.

### TZ v3 / Stage 5 (Signal Quality Research)

- Infrastructure: `complete` (signal_history, labeling jobs, research endpoints, tracking, readiness gate).
- Historical backfill: implemented (multi-source ingestion + idempotency + source tagging).
- Formal readiness reached (`PASS`) for Stage 5 process gates.
- Current business reality after optimization:
  - `DIVERGENCE`: often `REMOVE` on available slices.
  - `RULES_RISK`: improved to `MODIFY` (not stable `KEEP` yet).
- Reference: `docs/TZ_V3_STATUS.md`.

### Stage 6 (Agent Decision + Profit Validation)

- Technical implementation: `DONE`.
- Final validation verdict on historical batch: `NO_GO`.
- Last closure artifacts:
  - `artifacts/research/stage6_batch_20260314_070431.json`
  - `artifacts/research/stage6_export_20260314_070431.csv`
- Key outcome:
  - `keep_types=0`
  - `executable_signals_per_day=0.0`
  - rollback trigger activated by negative mean return profile.
- Reference: `docs/TZ_STAGE6_CLOSURE.md`.

## 3) What Is Already Strong

- Deterministic, testable decision pipeline (not black-box).
- Execution-aware scoring and EV logic with risk guardrails.
- Full research observability:
  - signal-level, type-level, platform-level, category-level reports,
  - walk-forward, Monte Carlo, readiness and governance gates.
- Operational safety:
  - feature flags,
  - rollback mechanisms,
  - provider reliability checks,
  - ethical transparency in Telegram delivery.

## 4) What Still Blocks Business Success

1. Not enough robust `KEEP` signal types under post-cost criteria.
2. Limited labeled density in hardest areas (especially Type 3/5 and sub-hour behavior).
3. Sensitivity of EV to execution costs/spread/slippage assumptions.
4. Need sustained shadow period for stable confidence in policy changes.

## 5) Research Priorities (Whole Project)

## P0: Edge Reliability and Data Sufficiency

- Increase labeled sample quality per type/window (not only raw count).
- Target: stable walk-forward windows with meaningful confidence intervals.
- Focus first on types with highest practical upside (`RULES_RISK`, selected divergence slices).

## P0: Cost Model Robustness

- Run sensitivity analysis for spread/slippage/fees by position-size bucket.
- Keep position-size-aware EV thresholds as hard policy constraints.

## P1: Signal Type Strategy

- Formalize each type state:
  - `PRODUCTION_CANDIDATE`,
  - `RESEARCH_ONLY`,
  - `INSUFFICIENT_ARCHITECTURE`.
- For Type 3/5: decide explicitly between:
  - high-frequency collector scope,
  - or long-term `INSUFFICIENT_ARCHITECTURE` status.

## P1: Agent Layer Maturity

- Keep `policy-first` mode as default.
- Use AI components only for assistive tasks:
  - rules ambiguity analysis,
  - text normalization,
  - explanation drafting.
- Do not use LLM outputs as direct EV/probability estimator.

## P1: Deliverables Hardening

- Ensure every batch emits:
  - machine artifact (`json/jsonl/csv`),
  - human-readable executive report (`.md`),
  - coverage metrics (including agent decision coverage).

## 6) 30-Day Deep Research Plan

### Week 1

- Finalize missing reporting artifacts/coverage counters.
- Start daily shadow runbook with strict change log for thresholds.

### Week 2

- Run controlled threshold sweeps for core types.
- Freeze top 1-2 candidate policy profiles for canary evaluation.

### Week 3

- Re-assess Type 3/5 with explicit architecture decision.
- Validate LIMITED_GO criteria on best profile.

### Week 4

- Re-run full Stage 6 batch.
- Publish consolidated verdict: `NO_GO` / `LIMITED_GO` / `GO`.

## 7) Success Definition for Project-Level Deep Research

- At least one profile reaches credible `LIMITED_GO` without violating risk guardrails.
- Trend of labeled quality metrics is positive and reproducible.
- Decision process is auditable end-to-end (data -> policy -> report -> verdict).
- Clear written decision for each signal type and each architecture-limited area.

