#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
set -a
if [ -f .env ]; then . ./.env; fi
set +a
for target in api web; do
  docker build --network "${BUILD_NETWORK:-default}" \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    --build-arg "IMAGE_PREFIX=${IMAGE_PREFIX:-}" \
    -f "deploy/Dockerfile.$target" -t "account-manager-$target:local" .
done
