# Stage 10 Agent Module Shortlist

Дата: 2026-03-16
Призначення: фіксований shortlist кандидатів для Stage 10 security audit + replay benchmark.

## 1. Правила shortlist

1. Усі кандидати спочатку проходять security gate (dependency + static + sandbox).
2. До replay не допускаються кандидати зі статусом `SECURITY_FAIL`.
3. `temperature=0`, обов'язковий `input_hash` cache, structured JSON output.

## 2. Candidate List (Top-5)

1. `plain_openai_tool_calling`
- Тип: plain API adapter
- Роль: baseline/fallback
- Статус: `PENDING_SECURITY_AUDIT`

2. `plain_anthropic_tool_use`
- Тип: plain API adapter
- Роль: baseline/fallback
- Статус: `PENDING_SECURITY_AUDIT`

3. `langgraph_orchestrator_minimal`
- Тип: graph orchestration
- Роль: primary orchestration candidate
- Статус: `PENDING_SECURITY_AUDIT`

4. `llamaindex_workflows_minimal`
- Тип: event-driven workflow
- Роль: secondary orchestration candidate
- Статус: `PENDING_SECURITY_AUDIT`

5. `openclaw_community_trading_candidate`
- Тип: community module (experimental)
- Роль: pilot-only, comparison branch
- Статус: `PENDING_SECURITY_AUDIT`
- Обмеження: тільки sandbox, тільки `SHADOW_ONLY`, без execution privileges

## 3. Rejected By Default (для Stage 10)

1. Будь-який модуль без активного репозиторію/версійності.
2. Будь-який модуль, що вимагає write/execute tools поза allowlist.
3. Будь-який модуль, що не підтримує deterministic replay path.

## 4. Audit Output Contract

Для кожного кандидата обов'язково зберегти:
1. `security_verdict`: `PASS|WARN|FAIL`
2. `dependency_findings_count`
3. `static_findings_count`
4. `sandbox_network_egress_detected`: `true|false`
5. `allowed_for_replay`: `true|false`
6. `notes`
