#!/usr/bin/env bash
set -euo pipefail

echo "== Prediction Market Scanner doctor =="
echo

echo "[1/6] Containers"
docker compose ps
echo

echo "[2/6] API health"
if curl -fsS http://127.0.0.1:8000/health; then
  echo
else
  echo "API health check failed"
fi
echo

echo "[3/6] DB auth + query"
docker compose exec -T db psql -U postgres -d prediction_scanner -c "select now() as db_time;"
echo

echo "[4/6] Bot -> DB connectivity"
docker compose exec -T bot python -c "from app.db.session import engine; from sqlalchemy import text; c=engine.connect(); print(c.execute(text('select 1')).scalar()); c.close()"
echo

echo "[5/6] Last bot logs (errors first)"
docker compose logs --tail=120 bot | grep -E "ERROR|OperationalError|Traceback|FATAL|Exception" || true
echo

echo "[6/6] Last api logs (errors first)"
docker compose logs --tail=120 api | grep -E "ERROR|OperationalError|Traceback|FATAL|Exception" || true
echo

echo "Doctor check complete."
