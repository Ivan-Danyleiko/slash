# Stage 6 Deep Research Brief

## 1) Поточний стан (на 2026-03-14)

- Infrastructure readiness: `~97%` (ядро Stage 6 реалізоване).
- Business readiness: `~20%` (rollout заблоковано валідно).
- Останній batch: `artifacts/research/stage6_batch_20260314_070431.json`.
- Фактичний verdict: `NO_GO`.

Ключові метрики з фінального batch:
- `final_decision=NO_GO`
- `recommended_action=block_rollout_and_research`
- `governance_decision=NO_GO`
- `circuit_breaker_level=OK`
- `rollback_triggered=true`
- `keep_types=0`
- `executable_signals_per_day=0.0`
- `type35_decision_counts={"INSUFFICIENT_DATA": 2}`

## 2) Що вже зроблено добре

- ExecutionSimulatorV2 з empirical EV (на labeled returns), fallback на v1 при low sample size.
- Agent Decision Layer (deterministic policy engine) з рішеннями `KEEP/MODIFY/REMOVE/SKIP`.
- Appendix C ranking (1-в-1) + feature flag для безпечного rollback.
- Walk-forward з embargo + bootstrap CI + low_confidence.
- 15m/30m sub-hour labeling jobs і аналітика покриття.
- GO/LIMITED_GO/NO_GO governance, overfit checks, risk guardrails (SOFT/HARD/PANIC + statistical rollback).
- Type 3/5 dedicated report з явним `INSUFFICIENT_*` verdict.

## 3) Короткий список незакритого

1. Немає артефактів формату:
- `stage6_agent_decisions_<ts>.jsonl`
- `stage6_final_report_<ts>.md`

2. Не зафіксована метрика `agent_decision_coverage >= 95%`:
- потрібен лічильник: скільки сигналів пройшли policy engine vs bypass.

3. Критерій shadow window `>=14 days` ще не може бути закритий миттєво:
- потрібен час накопичення.

4. Бізнес-даних недостатньо для KEEP:
- core типи поки не дають стабільний post-cost EV.

## 4) Що дослідити для покращення сервісу

### 4.1 Дані та якість вибірки (пріоритет P0)

- Підняти labeled coverage у test windows (особливо для core типів).
- Окремо контролювати:
  - `labeled_6h_share`, `labeled_24h_share`, `subhour_coverage`.
- Запустити ціль: `>=100 labeled samples per type per walk-forward window`.

Очікуваний ефект: менше noisy EV, стабільні рішення governance.

### 4.2 Cost realism (P0)

- Перевірити sensitivity EV до:
  - spread,
  - slippage,
  - bridge/gas fee амортизації,
  - position size buckets.
- Зафіксувати робочі EV-пороги по розміру позиції (small/medium/large).

Очікуваний ефект: зменшення false-KEEP та rollback-trigger.

### 4.3 Signal quality tuning (P0)

- Перебрати пороги по RULES_RISK і DIVERGENCE в research-режимі.
- Перевірити варіант `LIMITED_GO` для `MODIFY` типів (під контролем risk guardrails).
- Додати чітку таблицю причин REMOVE/MODIFY для кожного типу.

Очікуваний ефект: перехід 0 KEEP -> >=1 KEEP (шлях до LIMITED_GO).

### 4.4 Type 3/5 architecture gap (P1)

- Визначити, чи потрібен high-frequency collector (1-5 min polling для top-N).
- Якщо ні: офіційно тримати verdict `INSUFFICIENT_ARCHITECTURE` і не змішувати з data-quality проблемою.

Очікуваний ефект: чесний статус типів без штучного "покращення".

### 4.5 Governance/monitoring hardening (P1)

- Додати `agent_decision_coverage` у batch + dashboards.
- Додати обов'язкові артефакти `.jsonl` та `.md` у stage6 batch script.
- Автоматизувати daily summary для shadow прогону.

Очікуваний ефект: повне відповідність deliverables + швидший аудит.

## 5) Що дослідити для покращення AI-агента

Важливо: на поточному обсязі даних базовий контур має лишатися deterministic policy-first.

### 5.1 Agent as policy orchestrator (P0)

- Агент приймає лише структуровані фічі з проекту:
  - execution metrics,
  - quality metrics,
  - walk-forward stability,
  - risk guardrails,
  - provider reliability.
- Агент не оцінює probabilities "з нуля".

### 5.2 AI-допомога для rules ambiguity (P1)

- Використати AI тільки для допоміжних задач:
  - ambiguity detection у rules,
  - нормалізація тексту ринків,
  - пояснення decision reason.
- Не використовувати AI-модель як джерело EV/price forecast.

### 5.3 Anti-overfit protocol (P1)

- Для будь-якої "розумної" моделі:
  - walk-forward only,
  - embargo enforced,
  - overfit sanity checks,
  - manual review при аномально високих метриках.

## 6) План Deep Research (2-4 тижні)

Week 1:
- Додати missing artifacts (`.jsonl`, `.md`) і `agent_decision_coverage`.
- Запустити щоденний shadow batch + лог змін порогів.

Week 2:
- Threshold sweeps для RULES_RISK/DIVERGENCE з post-cost EV фокусом.
- Вибрати 1-2 stable policy configs для canary.

Week 3:
- Оцінити Type 3/5: або HF-scope, або formal `INSUFFICIENT_ARCHITECTURE`.
- Перевірити LIMITED_GO критерії для найкращого конфігу.

Week 4:
- Фінальний re-run Stage 6 batch.
- Рішення: `NO_GO` / `LIMITED_GO` / `GO` з доказами в артефактах.

## 7) Definition of Done для цього deep research

- Є `stage6_agent_decisions_<ts>.jsonl`.
- Є `stage6_final_report_<ts>.md`.
- Є метрика `agent_decision_coverage` і її тренд.
- Є мінімум 1 конфіг, що дає кандидат на `LIMITED_GO` без порушення guardrails.
- Є письмове рішення щодо Type 3/5 (HF implementation або formal architecture limitation).
