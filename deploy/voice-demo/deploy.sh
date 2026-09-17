#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose_file="$repo_dir/deploy/voice-demo/docker-compose.yml"
env_file="$repo_dir/deploy/voice-demo/.env"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy .env.example or run generate-env.sh first." >&2
  exit 1
fi

cd "$repo_dir"
docker compose --env-file "$env_file" -f "$compose_file" config --quiet
docker compose --env-file "$env_file" -f "$compose_file" pull postgres redis minio coturn api gateway edge
docker compose --env-file "$env_file" -f "$compose_file" up -d --build --remove-orphans
docker compose --env-file "$env_file" -f "$compose_file" ps
