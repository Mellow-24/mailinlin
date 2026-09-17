#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$deploy_dir/.env"

if [[ -e "$env_file" ]]; then
  echo "Refusing to overwrite $env_file" >&2
  exit 1
fi

server_ip="${1:-}"
if [[ -z "$server_ip" ]]; then
  echo "Usage: $0 <public-ip>" >&2
  exit 1
fi

random_secret() {
  openssl rand -hex 32
}

umask 077
{
  printf 'VOICE_DEMO_HOST=%s.sslip.io\n' "$server_ip"
  printf 'SERVER_IP=%s\n' "$server_ip"
  printf 'VOICE_DEMO_LOCAL_PORT=18080\n'
  printf 'ACME_EMAIL=littermore@163.com\n'
  printf 'POSTGRES_PASSWORD=%s\n' "$(random_secret)"
  printf 'REDIS_PASSWORD=%s\n' "$(random_secret)"
  printf 'MINIO_ROOT_USER=voice_demo\n'
  printf 'MINIO_ROOT_PASSWORD=%s\n' "$(random_secret)"
  printf 'TURN_SECRET=%s\n' "$(random_secret)"
  printf 'OSS_JWT_SECRET=%s\n' "$(random_secret)"
  printf 'YISHUI_VOICE_DEMO_TOKEN=replace-after-database-restore\n'
  printf 'DOGRAH_API_IMAGE=ghcr.io/dograh-hq/dograh-api:latest\n'
  printf 'LOG_LEVEL=INFO\n'
  printf 'FORCE_TURN_RELAY=false\n'
} > "$env_file"

echo "Created $env_file with mode 600"
