#!/usr/bin/env bash
# Go back to any earlier LABaPe release by hand (design §23.2): the web
# interface only offers the previous version.
#
#   deploy/labape-rollback.sh --to vX.Y.Z [--restore-backup FILE] [--repo DIR]
#
# FILE is a dump in the backup directory, as the containers see it (for
# example /var/lib/labape/backups/labape-20261011-210000-pre-update-v0.3.0.dump).
# Restore one when the target is older than the database's layout: going
# back without it stops at startup with "written by a newer version".
set -euo pipefail
TO=""; BACKUP=""; REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO="${2:?}"; shift ;;
    --restore-backup) BACKUP="${2:?}"; shift ;;
    --repo) REPO="${2:?}"; shift ;;
    *) sed -n '2,11p' "$0" >&2; exit 1 ;;
  esac
  shift
done
[ -n "$TO" ] || { sed -n '2,11p' "$0" >&2; exit 1; }
if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(podman compose); fi
cd "$REPO/container"
lab() { "${COMPOSE[@]}" exec -T labape-api labape "$@"; }

git -C "$REPO" fetch -q --tags origin || true
git -C "$REPO" rev-parse -q --verify "refs/tags/$TO" >/dev/null || { echo "no tag $TO" >&2; exit 1; }
[ -z "$(git -C "$REPO" status --porcelain)" ] || { echo "the checkout has local changes" >&2; exit 1; }
lab maintenance on --reason "manual rollback to $TO" --source restore --actor "$USER" >/dev/null || true
echo "Backing up the database first..."
lab backup --label "before-rollback-to-$TO" || true
"${COMPOSE[@]}" stop labape-api labape-worker
git -C "$REPO" checkout -q --detach "$TO"
export LABAPE_VERSION="$(git -C "$REPO" describe --tags --always)"
"${COMPOSE[@]}" build
if [ -n "$BACKUP" ]; then
  "${COMPOSE[@]}" run --rm --no-deps labape-worker labape restore "$BACKUP" --yes
fi
"${COMPOSE[@]}" up -d
for _ in $(seq 1 24); do lab health --deep >/dev/null 2>&1 && break; sleep 5; done
lab health --deep && lab maintenance off --source restore --actor "$USER" >/dev/null
echo "Now on $LABAPE_VERSION."
