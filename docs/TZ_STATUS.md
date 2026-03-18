# TZ v2.1 Status (17 points)

## Поточний статус

1. Мета — виконано.
2. Поточний стан/baseline — виконано.
3. Definitions/thresholds — виконано.
4. ARBITRAGE v2.1 — виконано.
5. RULES_RISK v2.1 — виконано.
6. Duplicate/Divergence v2.1 — виконано (2-stage + diagnostics + drop reasons + shadow).
7. Формальна модель скорингу — виконано.
8. Execution Simulator MVP — виконано.
9. DB зміни — виконано (включно з міграцією 0018: 7 performance indexes).
10. API/операційні вимоги — виконано.
11. Розв'язання конфліктів логіки — виконано.
12. KPI/DoD — частково (код/метрики є; накопичення 14+ днів і 80% днів рахується з часом).
13. Технічні обмеження — виконано.
14. Міграція і rollback — виконано (включно з feature-flag rollback для top selection).
15. План реалізації — виконано.
16. Acceptance scenarios — виконано (автотести + operational checks).
17. Бізнес-результат — у процесі підтвердження живими даними (потребує періоду спостереження).

## Висновок

Реалізація: `16/17` функціонально закрито.

Єдиний пункт, що потребує часу, а не додаткового коду: п.17 (підтвердження стабільного бізнес-ефекту на історії).

---

## Додаткові покращення (Phase 1-3, 2026-03)

### Виконано понад TZ v2.1:

- **Stage 7 FallbackAdapter** — groq→gemini→openrouter з retry-after (замість одного провайдера)
- **Polymarket CLOB оптимізація** — max_rows 10k→3k, поріг ліквідності $100→$1000; синк скоротився з ~25 хв до ~8 хв
- **Dry-run simulator Phase 2+3** — mark-to-market, stop-loss, time-exit, take-profit, resolution tracking
- **Перша реальна позиція** відкрита 2026-03-18: OKC Thunder (Polymarket, spread 2%, ev 1.95%)
- **Celery**: stale-job захист, retry на всі tasks, злиття label jobs в один батч
- **Log rotation** — RotatingFileHandler 50MB×5
- **DB indexes** — міграція 0018 (7 індексів для hot query paths)
- **Manifold pagination** — cursor-based pagination (до 3000 ринків)
- **Signal widening** — SIGNAL_ARBITRAGE_MIDPOINT_BAND=0.25 (±25% від 50%), MAX_CANDIDATES=25
- **OpenRouter model fix** — `gemini-2.5-flash-preview` → `google/gemini-2.5-flash`
