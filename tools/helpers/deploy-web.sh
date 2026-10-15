#!/usr/bin/env bash
# deploy-web.sh: put this working tree (uncommitted changes included) on the
# lab host's web stack: sync it into the stack's clone, build labape:local
# (stamped with git describe, so the UI type-check runs too), recreate the
# API and worker, and print /api/health.
#
# Leaves the lab clone dirty. Once the change is merged, reset it there:
#   git -C ~/LABaPe-web fetch && git -C ~/LABaPe-web reset --hard origin/main
#   ~/LABaPe-web/deploy/update-agent/install.sh    # the agent runs from a copy
# Don't run it while jobs are running: recreating the worker kills them.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lab.sh"
cd "$(git -C "$HERE" rev-parse --show-toplevel)"

# A throwaway index, so the archive includes uncommitted and untracked files
# without touching the real staging area.
idx="$(mktemp)"
trap 'rm -f "$idx"' EXIT
cp .git/index "$idx"
GIT_INDEX_FILE="$idx" git add -A 2>/dev/null
tree="$(GIT_INDEX_FILE="$idx" git write-tree)"
version="$(git describe --tags --always --dirty)"

# R=... is expanded on the lab host, so a ~ in LABAPE_LAB_REPO works.
git -c core.autocrlf=false archive "$tree" | "${LAB_SSH[@]}" "V='$version'; R=$LAB_REPO; U='$LAB_URL';"'
  tar -xf - -C "$R" && cd "$R"
  if docker build --build-arg LABAPE_VERSION="$V" -f container/Dockerfile -t labape:local . > /tmp/labape-build.log 2>&1; then
    echo "build=ok $V"
  else
    echo "build=FAILED (full log: /tmp/labape-build.log on the lab host)"; grep -E "error TS|ERROR" /tmp/labape-build.log | head -20; exit 1
  fi
  cd container
  # The stack syslog collector (design §29.1) has its own image; start it and its database too.
  if ! docker compose build labape-syslog >> /tmp/labape-build.log 2>&1; then
    echo "syslog image build=FAILED (full log: /tmp/labape-build.log on the lab host)"; tail -20 /tmp/labape-build.log; exit 1
  fi
  docker compose up -d labape-syslog-db labape-syslog labape-mailpit >/dev/null 2>&1
  docker compose up -d --force-recreate labape-api labape-worker >/dev/null 2>&1
  docker compose up -d caddy >/dev/null 2>&1          # picks up compose changes (ports, environment)
  # The Caddyfile is bind-mounted and tar replaced it (a new inode): the container still has the old one, and a
  # reload re-reads that. Recreate Caddy when the file it sees differs.
  CF="$(grep -m1 "^LABAPE_CADDYFILE=" .env | cut -d= -f2-)"; CF="${CF:-Caddyfile}"
  docker compose exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null | cmp -s - "caddy/$CF" \
    || docker compose up -d --force-recreate caddy >/dev/null 2>&1
  sleep 15; curl -sk "$U/api/health"; echo'
