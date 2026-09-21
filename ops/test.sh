#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s ops -p 'test_*.py'
python3 ops/init.py
./ops/compose.sh up -d --wait db
# The test database is isolated; refuse to let pytest touch a non-test database.
if ! ./ops/compose.sh exec -T db psql -U account_manager -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='account_manager_test'" | grep -q 1; then
  ./ops/compose.sh exec -T db createdb -U account_manager account_manager_test
fi
./ops/compose.sh run --rm --no-deps -e DB_NAME=account_manager_test api alembic upgrade head
./ops/compose.sh run --rm --no-deps -e DB_NAME=account_manager_test -e PUBLIC_ORIGIN= -v "$PWD/backend:/app" api pytest -q
./ops/compose.sh run --rm --no-deps -v "$PWD/backend:/app" api ruff check --no-cache app tests migrations
