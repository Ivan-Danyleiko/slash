# Testing And Acceptance

## Acceptance scenarios (ТЗ п.16)

1. `momentum` не створюється при `recent_move < threshold`.
2. `uncertainty_liquid` має score cap (`SIGNAL_MODE_UNCERTAINTY_MAX_SCORE`).
3. `missing_rules_risk` не перевищує daily cap.
4. При досягненні cap береться top-N за quality.
5. `created_at` не змінюється при refresh сигналу.
6. `updated_at` змінюється при refresh сигналу.
7. `/analytics/quality` повертає агрегати за період.

## Автотести

Нові/оновлені тести:

1. `tests/test_engine_acceptance.py`:
   - сценарії 1,2,3,4,5,6.
2. `tests/test_top_selection.py`:
   - v2/legacy rollback behaviour.
3. `tests/test_duplicate_detector.py`:
   - duplicate strict/aggressive/entity logic.

## Останній прогін

На сервері в контейнері `api`:

`python -m pytest -q tests/test_engine_acceptance.py tests/test_duplicate_detector.py tests/test_top_selection.py`

Результат: `11 passed`.

## Operational checks

1. `POST /admin/run-analysis`
2. `GET /analytics/quality?days=7`
3. `GET /analytics/duplicate-shadow`
4. `GET /signals/top`

