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
# Nightly commit of the knowledge base so every lesson and situation change is versioned on the server.
( crontab -l 2>/dev/null; echo "30 3 * * * cd $(pwd) && git add knowledge && git -c user.name=tradebot -c user.email=bot@localhost commit -qm 'Nightly knowledge snapshot' || true" ) | sort -u | crontab -
# Auto-update every 10 minutes: pull new research (inbox/) and code; rebuild only when code changed.
cat > deploy/auto_update.sh <<'UPD'
#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
before=$(git rev-parse HEAD)
git -c user.name=tradebot -c user.email=bot@localhost pull -q --rebase --autostash origin main || exit 0
after=$(git rev-parse HEAD)
[ "$before" = "$after" ] && exit 0
if git diff --name-only "$before" "$after" | grep -qE '^(src/|pyproject.toml|uv.lock|deploy/Dockerfile)'; then
  docker compose -f deploy/docker-compose.yml up -d --build
fi
UPD
chmod +x deploy/auto_update.sh
( crontab -l 2>/dev/null; echo "*/10 * * * * $(pwd)/deploy/auto_update.sh >> $(pwd)/data/auto_update.log 2>&1" ) | sort -u | crontab -
echo "Running. Logs: docker compose -f deploy/docker-compose.yml logs -f"
