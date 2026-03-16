# Stage 12 Module and Connector Candidates

Дата: 2026-03-16
Призначення: фіксований список кандидатів для Stage 12 scoring matrix та connector gate.

## 1. Source/Connector Candidates

1. `polymarket_clob_full_depth`
- Тип: execution/data connector
- Пріоритет: `Tier A`
- Ціль: real spread/depth, fill realism
- Початковий статус: `PILOT_SHADOW`

2. `metaculus_history_enhanced`
- Тип: data connector
- Пріоритет: `Tier A`
- Ціль: prediction-history, calibration support
- Початковий статус: `PILOT_SHADOW`

3. `manifold_bets_timeline_enhanced`
- Тип: data connector
- Пріоритет: `Tier A`
- Ціль: probability(t) reconstruction
- Початковий статус: `PILOT_SHADOW`

4. `kalshi_connector`
- Тип: regulated venue/data
- Пріоритет: `Tier B` (тільки при доступному compliance)
- Ціль: macro/politics depth + fee realism
- Початковий статус: `PILOT_SHADOW`

5. `smarkets_connector`
- Тип: regulated exchange
- Пріоритет: `Tier B`
- Ціль: diversified execution venue
- Початковий статус: `PILOT_SHADOW`

6. `betfair_connector`
- Тип: regulated exchange
- Пріоритет: `Tier B`
- Ціль: diversified execution venue
- Початковий статус: `PILOT_SHADOW`

## 2. Agent/Module Candidates

1. `langgraph_orchestrator_minimal`
- Роль: primary orchestration candidate
- Початковий статус: `PILOT_SHADOW`

2. `plain_openai_tool_calling`
- Роль: baseline/fallback
- Початковий статус: `PILOT_SHADOW`

3. `plain_anthropic_tool_use`
- Роль: baseline/fallback
- Початковий статус: `PILOT_SHADOW`

4. `llamaindex_workflows_minimal`
- Роль: secondary candidate
- Початковий статус: `PILOT_SHADOW`

5. `openclaw_community_trading_candidate`
- Роль: experimental branch
- Початковий статус: `EXPERIMENTAL_ONLY`
- Обмеження: sandbox-only, no execution privileges

## 3. Candidate Promotion Rules

1. `PILOT_SHADOW -> ADOPT`:
- `weighted_score >= 4.0`
- `Security >= 4`
- `Compliance >= 3`
- без critical regressions в Stage7/8/9 suites

2. `PILOT_SHADOW -> REJECT`:
- `weighted_score < 3.0` або
- `Security < 3` або
- `Compliance < 2` або
- критичний security incident

3. `EXPERIMENTAL_ONLY` не може перейти в `ADOPT` без окремого governance approval.

## 4. Mandatory Per-Candidate Artifacts

1. `scorecard_row`
2. `security_audit_summary`
3. `oos_contribution_report`
4. `regression_test_report`
5. `final_verdict`
