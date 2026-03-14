# Rollback Plan

## 1) Швидкий rollback топ-відбору (без деплою)

1. В `.env` виставити:
   - `SIGNAL_TOP_USE_V2_SELECTION=false`
2. Перезапустити `api`, `worker`, `bot`.
3. Перевірити `/signals/top`.

Це повертає legacy selection без V2 gating.

## 2) Rollback ранжування у v2

1. В `.env` виставити:
   - `SIGNAL_TOP_USE_V2_SELECTION=true`
   - `SIGNAL_TOP_V2_RANK_BY_SCORE_TOTAL=false`

Це лишає v2 gating, але повертає сортування за legacy `rank_score`.

## 3) Duplicate tuning rollback

1. Повернути conservative пороги broad у `.env`:
   - `SIGNAL_DUPLICATE_BROAD_THRESHOLD=75`
   - `SIGNAL_DUPLICATE_BROAD_RELAXED_FUZZY_MIN=88`
2. Перезапустити `api`, `worker`.

## 4) DB rollback (alembic)

1. Перевірити current revision: `alembic current`
2. Відкотити: `alembic downgrade <revision>`
3. Верифікувати API і worker.

Увага: DB rollback застосовувати лише при потребі схематичного відкату.
