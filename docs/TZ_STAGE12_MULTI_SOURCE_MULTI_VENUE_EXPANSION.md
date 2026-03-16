# ТЗ Stage 12: Multi-Source / Multi-Venue Expansion

## 1. Мета

Масштабувати систему після стабільного Stage 11:
1. Підключати нові data sources та execution venues поступово.
2. Збільшувати edge через якісніші дані, а не через агресивніший ризик.
3. Підтримувати єдиний security/compliance стандарт на кожен новий connector.

## 2. In Scope

1. Source expansion:
   - Metaculus/Manifold/Polymarket quality hardening,
   - нові джерела (за scorecard).
2. Venue expansion:
   - regulated та/або crypto venues через feature flags.
3. Agent/module expansion:
   - оцінка готових модулів до production.
4. Unified routing policy по venue/category.

## 3. Out of Scope

1. Підключення venue без legal/compliance check.
2. Auto-trading для connector, який не пройшов acceptance.
3. Full autonomous AI override без deterministic fallback.

## 4. Connector Acceptance Gate

1. Data quality:
   - field completeness,
   - schema stability,
   - latency/reliability.
2. Execution realism (для venue):
   - fill availability,
   - slippage measurability,
   - fee model correctness.
3. Security:
   - secret handling,
   - access scope,
   - dependency vulnerability scan.
4. Compliance:
   - ToS allowance,
   - regional restrictions,
   - KYC requirements.

Verdict:
1. `ADOPT`.
2. `PILOT_SHADOW`.
3. `REJECT`.

## 5. Scoring Matrix (формалізовано)

Осі оцінки (0..5):
1. Integration Fit.
2. Data Quality Contribution.
3. Execution Value Contribution.
4. Security Posture.
5. Compliance Fit.
6. Reliability/Latency.
7. Cost/TCO.
8. Vendor Risk.

Ваги:
1. Integration Fit: `0.15`
2. Data Quality Contribution: `0.20`
3. Execution Value Contribution: `0.20`
4. Security Posture: `0.15`
5. Compliance Fit: `0.10`
6. Reliability/Latency: `0.10`
7. Cost/TCO: `0.05`
8. Vendor Risk: `0.05`

Розрахунок:
1. `weighted_score = sum(score_i * weight_i)`.

Пороги:
1. `ADOPT` якщо `weighted_score >= 4.0` і Security>=4 і Compliance>=3.
2. `PILOT_SHADOW` якщо `3.0 <= weighted_score < 4.0`.
3. `REJECT` якщо `<3.0` або Security<3 або Compliance<2.

## 6. Методологія source contribution

Для кожного джерела `S`:
1. Locked evaluation set.
2. Run A: pipeline без `S`.
3. Run B: pipeline з `S`.
4. Порівняння:
   - `delta_precision_at_25`,
   - `delta_post_cost_ev`,
   - `delta_brier_skill_score`.
5. `source_contribution_delta_ev` = `EV(B) - EV(A)`.

Тільки out-of-sample windows.

## 7. Agent/Module Security & Tuning Protocol

1. Fixed candidate list у `docs/STAGE12_MODULE_CANDIDATES.md`.
2. Кожен кандидат проходить:
   - provenance check,
   - dependency/code security scan,
   - sandbox run,
   - replay benchmark.
3. Тільки після `SECURITY_PASS` дозволений tuning.
4. Regressions => downgrade status.

## 8. Pipeline regression guard

Після підключення кожного connector:
1. Stage7 regression suite.
2. Stage8 regression suite.
3. Stage9 regression suite.
4. Якщо будь-який critical regression -> auto rollback connector status.

## 9. Метрики Stage 12

1. `source_contribution_delta_ev`.
2. `consensus_coverage` (2+ і 3-source).
3. `connector_uptime`.
4. `data_contract_break_rate`.
5. `security_findings_open_count`.
6. `cost_per_decision`.
7. `precision_at_k_delta`.

## 10. Acceptance Criteria (Stage 12)

1. Мінімум 2 нові connectors пройшли gate (>= `PILOT_SHADOW`).
2. Мінімум 1 connector отримав `ADOPT` і позитивний out-of-sample contribution.
3. Жоден новий connector не збільшив critical incidents.
4. Security baseline виконано для 100% нових модулів.
5. Після підключення не погіршились Stage 11 KPI поза tolerance.

## 11. DB schema / migrations

1. Таблиці:
   - `stage12_connector_scorecards`,
   - `stage12_module_audits`,
   - `stage12_connector_runs`.
2. Міграція: `0015_stage12_connectors_and_audits.py`.

## 12. Deliverables

1. `docs/STAGE12_CONNECTOR_SCORECARD.md`.
2. `docs/STAGE12_AGENT_MODULE_AUDIT.md`.
3. `docs/STAGE12_ROLLOUT_PLAN.md`.
4. `artifacts/research/stage12_batch_<timestamp>.json`.
5. `artifacts/research/stage12_connectors_<timestamp>.csv`.

## 13. API

1. `GET /analytics/research/stage12/connectors`.
2. `GET /analytics/research/stage12/modules`.
3. `GET /analytics/research/stage12/routing`.
4. `POST /analytics/research/stage12/track`.

## 14. Governance process

1. Щотижневий security re-scan `ADOPT/PILOT` модулів.
2. Щоденний connector health review.
3. Quarterly vendor/compliance review:
   - owner: tech lead + ops + legal,
   - output: updated scorecard + connector status decisions.
