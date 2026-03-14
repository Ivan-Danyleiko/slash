#!/usr/bin/env bash
set -e

alembic upgrade head
python scripts/seed.py
