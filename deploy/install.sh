#!/usr/bin/env bash
# One-time server setup on a fresh Ubuntu VPS. Run as root: bash deploy/install.sh
set -euo pipefail
apt-get update && apt-get install -y docker.io docker-compose-v2 git sqlite3
cd "$(dirname "$0")/.."
[ -f .env ] || { cp .env.example .env; echo "Fill in .env, then re-run."; exit 1; }
mkdir -p data
docker compose -f deploy/docker-compose.yml up -d --build
# Nightly DB backup, kept 14 days. Point this at off-site storage later.
( crontab -l 2>/dev/null; echo "15 3 * * * sqlite3 $(pwd)/data/tradebot.db \".backup $(pwd)/data/backup-\$(date +\\%a).db\"" ) | sort -u | crontab -
echo "Running. Logs: docker compose -f deploy/docker-compose.yml logs -f"
