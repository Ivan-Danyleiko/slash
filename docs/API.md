# API

## Core

1. `GET /health` — healthcheck.
2. `GET /markets` / `GET /markets/{id}` / `GET /markets/{id}/analysis`.
3. `GET /signals` / `GET /signals/{id}` / `GET /signals/top` / `GET /signals/{id}/why`.

## Analytics

1. `GET /analytics/quality?days=7`
2. `GET /analytics/platform-distribution`
3. `GET /analytics/cross-platform-pairs`
4. `GET /analytics/duplicates`
5. `GET /analytics/duplicate-candidates?stage=&limit=`
6. `GET /analytics/duplicate-drop-reasons`
7. `GET /analytics/duplicate-shadow?broad_threshold=&broad_relaxed_fuzzy_min=`
8. `GET /analytics/divergence`
9. `GET /analytics/rules-risk`
10. `GET /analytics/liquidity-risk`

## Analytics Research (Stage 5)

1. `GET /analytics/research/signals`
2. `GET /analytics/research/signals.csv`
3. `GET /analytics/research/divergence-thresholds`
4. `GET /analytics/research/progress`
5. `GET /analytics/research/agent-decisions`
6. `GET /analytics/research/divergence-decision`
7. `POST /analytics/research/divergence-decision/track`
8. `GET /analytics/research/monte-carlo`
9. `GET /analytics/research/result-tables`
10. `GET /analytics/research/experiments`
11. `GET /analytics/research/data-quality`
12. `POST /analytics/research/data-quality/track`
13. `GET /analytics/research/provider-reliability`
14. `POST /analytics/research/provider-reliability/track`
15. `GET /analytics/research/provider-contract-checks`
16. `GET /analytics/research/stack-decision-log`
17. `GET /analytics/research/stack-readiness`
18. `GET /analytics/research/build-vs-buy-estimate`
19. `POST /analytics/research/build-vs-buy-estimate/track`
20. `GET /analytics/research/ab-testing`
21. `POST /analytics/research/ab-testing/track`
22. `GET /analytics/research/ethics`
23. `POST /analytics/research/ethics/track`
24. `GET /analytics/research/ranking-formulas`
25. `POST /analytics/research/ranking-formulas/track`
26. `GET /analytics/research/platform-comparison`
27. `POST /analytics/research/platform-comparison/track`
28. `GET /analytics/research/market-categories`
29. `GET /analytics/research/signal-types`
30. `POST /analytics/research/signal-types/track`
31. `GET /analytics/research/signal-types/optimize`
32. `POST /analytics/research/signal-types/optimize/track`
33. `GET /analytics/research/event-clusters`
34. `POST /analytics/research/event-clusters/track`
35. `GET /analytics/research/signal-lifetime`
36. `POST /analytics/research/signal-lifetime/track`
37. `GET /analytics/research/walkforward`
38. `POST /analytics/research/walkforward/track`
39. `GET /analytics/research/liquidity-safety`
40. `POST /analytics/research/liquidity-safety/track`
41. `GET /analytics/research/final-report`
42. `POST /analytics/research/final-report/track`
43. `GET /analytics/research/export-package`
44. `GET /analytics/research/export-package.csv`
45. `GET /analytics/research/readiness-gate`
46. `POST /analytics/research/readiness-gate/track`
47. `GET /analytics/research/stage6-governance`
48. `POST /analytics/research/stage6-governance/track`
49. `GET /analytics/research/stage6-risk-guardrails`
50. `POST /analytics/research/stage6-risk-guardrails/track`
51. `GET /analytics/research/stage6-type35`
52. `POST /analytics/research/stage6-type35/track`
53. `GET /analytics/research/stage6-final-report`
54. `POST /analytics/research/stage6-final-report/track`
55. `GET /analytics/research/stage7/stack-scorecard`
56. `POST /analytics/research/stage7/stack-scorecard/track`
57. `GET /analytics/research/stage7/harness`
58. `POST /analytics/research/stage7/harness/track`
59. `GET /analytics/research/stage7/shadow`
60. `POST /analytics/research/stage7/shadow/track`
61. `GET /analytics/research/stage7/final-report`
62. `POST /analytics/research/stage7/final-report/track`

## User/Product

1. `GET /me`
2. `GET /plans`

## Admin (x-api-key)

1. `POST /admin/sync-markets`
2. `POST /admin/run-analysis`
3. `POST /admin/quality-snapshot`
4. `POST /admin/send-test-signal`
5. `POST /admin/label-signal-history/15m`
6. `POST /admin/label-signal-history/30m`
7. `POST /admin/provider-contract-checks`
