# TZ Stage 10 Status

- updated_at: 2026-03-16T10:10:20.915167+00:00
- batch_generated_at: 2026-03-16T10:10:20.727219+00:00
- final_decision: WARN
- recommended_action: fix_stage10_failed_checks
- failed_checks_count: 7

## Replay

- rows_total: 2595
- events_total: 1046
- event_target: 100
- event_target_reached: True
- leakage_violations_count: 2595
- leakage_violation_rate: 1.0
- data_insufficient_timeline_share: 1.0
- post_cost_ev_ci_low_80: 0.0
- reason_code_stability: 1.0

## Timeline Quality

- timeline_rows_total: 0
- data_insufficient_timeline_share: 1.0

## Timeline Backfill Plan

- markets_scanned: 4827
- manifold_readiness: 0.0
- metaculus_readiness: 0.0

## Module Audit

- candidates_total: 5
- security_pass_count: 1
- security_fail_count: 4
- allowed_for_replay_count: 1
- stage10_llm_budget_ratio: 0.06464
- stage10_llm_mode: normal

## Failed Checks

- leakage_violations_count_eq_0
- data_insufficient_timeline_share_le_20pct
- post_cost_ev_ci_low_80_gt_0
- core_category_positive_ev_candidate_ge_1
- scenario_sweeps_positive_ge_12
- walkforward_negative_window_share_le_30pct
- core_categories_each_ge_20
