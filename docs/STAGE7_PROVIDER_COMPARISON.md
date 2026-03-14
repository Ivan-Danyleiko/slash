# Stage 7 Provider Comparison (30d/200)

- generated_at: 2026-03-14T18:08:07.942073+00:00
- database_url: sqlite:///artifacts/research/stage5_xplat3.db
- shadow_lookback_days: 30
- shadow_limit: 200

## Summary

- primary_candidate_now: groq
- fallback_candidate_now: gemini
- note: all profiles are currently NO_GO on business checks; ranking is based on operational quality only

## Table

| profile | provider | final_decision | llm_calls | cache_hits | latency_p95_ms | reason_code_stability | post_hoc_precision | sweeps | bootstrap_lb_pos_80 | wf_neg_share | adapter_error_rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gemini | plain_llm_api:gemini | NO_GO | 0 | 200 | 16.3262 | 1.0 | 0.0 | 0/18 | False | 1.0 | 0.255 |
| groq | plain_llm_api:groq | NO_GO | 0 | 200 | 16.2767 | 1.0 | 0.0 | 0/18 | False | 1.0 | 0.255 |
| openrouter | plain_llm_api:openrouter | NO_GO | 0 | 200 | 17.4141 | 1.0 | 0.0 | 0/18 | False | 1.0 | 0.255 |

## Failed Checks

- gemini: stage6_not_no_go, sweeps_positive_in_12_of_18, bootstrap_ci_lower_bound_positive_80, walkforward_negative_window_share_le_30pct
- groq: stage6_not_no_go, sweeps_positive_in_12_of_18, bootstrap_ci_lower_bound_positive_80, walkforward_negative_window_share_le_30pct
- openrouter: stage6_not_no_go, sweeps_positive_in_12_of_18, bootstrap_ci_lower_bound_positive_80, walkforward_negative_window_share_le_30pct

## Artifacts

- gemini: `artifacts/research/stage7_batch_20260314_180515.json`
- groq: `artifacts/research/stage7_batch_20260314_180606.json`
- openrouter: `artifacts/research/stage7_batch_20260314_180654.json`
