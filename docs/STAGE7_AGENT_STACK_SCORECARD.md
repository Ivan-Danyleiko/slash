# Stage 7 Agent Stack Scorecard

## Мета

Порівняти стек-кандидати Stage 7 за єдиною матрицею (8 осей) та визначити:
1. `primary`,
2. `secondary/fallback`,
3. `reject/ops-only`.

## Фіксовані ваги

1. Integration Fit: `0.15`
2. Tooling & Control: `0.15`
3. Observability: `0.15`
4. Governance Fit: `0.20`
5. Security: `0.15`
6. Reliability/Latency: `0.10`
7. Cost: `0.05`
8. Vendor Risk: `0.05`

## Поточний результат (Phase B)

Джерело: `artifacts/research/stage7_batch_20260314_122815.json` та наступні batch-runs.

Попередній ranking:
1. `langgraph` — `adopt` (primary candidate)
2. `plain_llm_api` — `pilot` (secondary/fallback)
3. `llamaindex_workflows` — `pilot`
4. `crewai` — `pilot` (restricted)
5. `autogen` — `pilot` (vendor-risk watch)
6. `n8n` — `adopt_for_orchestration_only`

## Примітка

Scorecard комбінує:
1. prior-оцінки,
2. емпіричний overlay з `stage7_harness` (pass-rate, idempotency, latency budget).

