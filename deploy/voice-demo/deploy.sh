#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose_file="$repo_dir/deploy/voice-demo/docker-compose.yml"
env_file="$repo_dir/deploy/voice-demo/.env"
server_env_file="$repo_dir/deploy/voice-demo/server.env"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy .env.example or run generate-env.sh first." >&2
  exit 1
fi

if [[ ! -f "$server_env_file" ]]; then
  echo "Missing $server_env_file." >&2
  exit 1
fi

cd "$repo_dir"
compose=(docker compose --env-file "$env_file" --env-file "$server_env_file" -f "$compose_file")
"${compose[@]}" config --quiet
"${compose[@]}" pull postgres redis coturn gateway edge
"${compose[@]}" up -d --build --remove-orphans
"${compose[@]}" ps
