#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
project_dir=$(pwd)
env_file="$project_dir/.env"
if [ ! -f "$env_file" ]; then env_file="$project_dir/.env.example"; fi
exec docker compose --env-file "$env_file" \
  -f "$project_dir/deploy/docker-compose.yaml" "$@"
