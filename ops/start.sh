#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 ops/init.py
./ops/build.sh
./ops/compose.sh up -d --no-build --wait
