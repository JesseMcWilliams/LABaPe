#!/usr/bin/env bash
# LABaPe update agent (Claude_Docs/Planning_Web-Interface-Design.md §23.2).
#
# Runs on the host as the service user, started by labape-update.path when
# the web interface writes container/update/request.json. Installed as a
# copy in ~/.local/libexec (it checks other versions out while it runs).
#
# update <tag>: signed tag only -> maintenance on -> wait for running jobs ->
#   database backup -> keep the running image as labape:previous -> check
#   out the tag, build, start -> health checks for LABAPE_HEALTH_SECONDS ->
#   maintenance off. Failing health checks roll back automatically.
# rollback: back to the version on standby (one step only), restoring the
#   pre-update backup when the database layout changed.
set -uo pipefail

REPO="${LABAPE_REPO:-$HOME/LABaPe-web}"
C="$REPO/container"
U="$C/update"
SIGNERS="${LABAPE_ALLOWED_SIGNERS:-$HOME/.config/labape/allowed_signers}"
HEALTH_SECONDS="${LABAPE_HEALTH_SECONDS:-300}"
DRAIN_SECONDS="${LABAPE_DRAIN_SECONDS:-1800}"
LOG="$U/agent.log"
# Caddy answers only for the configured host name, so check through it.
WEB_HOST="$(grep -m1 '^LABAPE_HOSTNAME=' "$C/.env" 2>/dev/null | cut -d= -f2-)"
if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); RT=docker; else COMPOSE=(podman compose); RT=podman; fi

[ -f "$U/request.json" ] || exit 0
mv "$U/request.json" "$U/processing.json" || exit 0
exec >>"$LOG" 2>&1
echo "=== $(date -u +%FT%TZ) labape-update: $(cat "$U/processing.json" | tr -d '\n')"

field() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" "$1" "$2"; }
ACTION="$(field "$U/processing.json" action)"
TARGET="$(field "$U/processing.json" target)"
BY="$(field "$U/processing.json" requested_by)"
STARTED="$(date -u +%FT%TZ)"
FROM_VER="$(git -C "$REPO" describe --tags --always 2>/dev/null)"

# status <state> <step> <message> [to]
status() {
  python3 - "$U/status.json" "$1" "$2" "$3" "${4:-$TARGET}" "$FROM_VER" "$STARTED" "$BY" "$ACTION" <<'PY'
import datetime, json, os, sys
path, state, step, message, to, frm, started, by, action = sys.argv[1:10]
s = {"state": state, "step": step, "message": message, "action": action, "from": frm, "to": to,
     "requested_by": by, "started_at": started,
     "finished_at": None if state == "running" else datetime.datetime.now(datetime.timezone.utc).isoformat()}
tmp = path + ".tmp"
json.dump(s, open(tmp, "w"), indent=2)
os.replace(tmp, path)
PY
  echo "labape-update: [$1] $2: $3"
}
labape() { (cd "$C" && "${COMPOSE[@]}" exec -T labape-api labape "$@"); }
up() { (cd "$C" && "${COMPOSE[@]}" up -d); }
healthy() {
  labape health --deep >/dev/null 2>&1     && curl -sk --max-time 10 --resolve "${WEB_HOST:-localhost}:443:127.0.0.1" "https://${WEB_HOST:-localhost}/api/health" | grep -q '"ok":true'
}

# wait_healthy <seconds>: healthy within 2 minutes, then healthy every check until the end.
wait_healthy() {
  local deadline=$((SECONDS + $1)) first=$((SECONDS + 120)) seen=0
  while [ $SECONDS -lt $deadline ]; do
    if healthy; then seen=1; elif [ $seen = 1 ] || [ $SECONDS -gt $first ]; then return 1; fi
    sleep 15
  done
  [ $seen = 1 ]
}

finish() { rm -f "$U/processing.json"; }

rollback() {   # rollback <why> <final state>
  local why="$1" final="$2"
  [ -f "$U/previous.json" ] || { status failed rollback "$why; no previous version on standby"; finish; exit 1; }
  local ref ver rev backup
  ref="$(field "$U/previous.json" ref)"; ver="$(field "$U/previous.json" version)"
  rev="$(field "$U/previous.json" db_revision)"; backup="$(field "$U/previous.json" backup)"
  status running rollback "$why; going back to $ver" "$ver"
  labape maintenance on --reason "rolling back to $ver" --source update >/dev/null 2>&1 || true
  (cd "$C" && "${COMPOSE[@]}" stop labape-api labape-worker)
  git -C "$REPO" checkout -q --detach "$ref"
  $RT tag labape:previous labape:local
  local now_rev
  now_rev="$(cd "$C" && "${COMPOSE[@]}" run --rm --no-deps labape-worker labape db-status 2>/dev/null | awk '/^database:/{print $2}')"
  if [ -n "$rev" ] && [ "$now_rev" != "$rev" ]; then
    status running restore "the database layout changed ($rev -> $now_rev); restoring $(basename "$backup")" "$ver"
    (cd "$C" && "${COMPOSE[@]}" run --rm --no-deps labape-worker labape restore "$backup" --yes) \
      || { status failed restore "restoring $backup failed; see agent.log" "$ver"; finish; exit 1; }
  fi
  up
  if wait_healthy 180; then
    labape maintenance off --source update >/dev/null 2>&1 || true
    rm -f "$U/previous.json"          # one step back only
    status "$final" done "$why; back on $ver" "$ver"
  else
    status failed rollback "$why; the previous version $ver didn't come up healthy either (maintenance stays on)" "$ver"
  fi
  finish
}

fail() { labape maintenance off --source update >/dev/null 2>&1 || true; status failed "$1" "$2"; finish; exit 1; }

if [ "$ACTION" = rollback ]; then
  rollback "rollback requested by $BY" succeeded
  exit 0
fi

# --- update ------------------------------------------------------------------
status running checking "checking $TARGET"
[ -z "$(git -C "$REPO" status --porcelain)" ] || fail checking "the checkout at $REPO has local changes; not switching versions"
git -C "$REPO" fetch -q --tags origin || echo "labape-update: fetch failed (offline?); using local tags"
git -C "$REPO" rev-parse -q --verify "refs/tags/$TARGET" >/dev/null || fail checking "no tag $TARGET"
[ -s "$SIGNERS" ] || fail checking "no allowed signers file at $SIGNERS"
git -C "$REPO" -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-tag "$TARGET" \
  || fail checking "$TARGET isn't signed by an allowed key; refused"

status running maintenance "maintenance on; waiting for running jobs"
labape maintenance on --reason "updating from $FROM_VER to $TARGET" --source update >/dev/null || fail maintenance "couldn't turn maintenance mode on"
deadline=$((SECONDS + DRAIN_SECONDS))
while :; do
  running="$(labape maintenance status | python3 -c 'import json,sys; print(json.load(sys.stdin)["running_jobs"])')"
  [ "$running" = 0 ] && break
  [ $SECONDS -lt $deadline ] || fail maintenance "$running job(s) still running after ${DRAIN_SECONDS}s; not updating"
  sleep 15
done

status running backup "backing up the database"
backup="$(labape backup --label "pre-update-${FROM_VER//[^A-Za-z0-9.]/-}")" || fail backup "the database backup failed"
rev="$(labape db-status | awk '/^database:/{print $2}')"
$RT tag labape:local labape:previous || fail backup "couldn't keep the running image as labape:previous"
python3 - "$U/previous.json" "$(git -C "$REPO" rev-parse HEAD)" "$FROM_VER" "$rev" "$backup" <<'PY'
import datetime, json, sys
path, ref, ver, rev, backup = sys.argv[1:6]
json.dump({"ref": ref, "version": ver, "db_revision": rev, "backup": backup, "image": "labape:previous",
           "kept_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(path, "w"), indent=2)
PY

status running building "building $TARGET"
git -C "$REPO" checkout -q --detach "$TARGET" || { rollback "checking out $TARGET failed" rolled_back; exit 1; }
export LABAPE_VERSION="$(git -C "$REPO" describe --tags --always)"
(cd "$C" && "${COMPOSE[@]}" build) || { rollback "building $TARGET failed" rolled_back; exit 1; }
status running starting "starting $TARGET"
up || { rollback "starting $TARGET failed" rolled_back; exit 1; }
status running health "health checks for ${HEALTH_SECONDS}s"
if wait_healthy "$HEALTH_SECONDS"; then
  labape maintenance off --source update >/dev/null 2>&1 || true
  status succeeded done "updated from $FROM_VER to $TARGET; $FROM_VER stays on standby for a rollback"
  finish
else
  rollback "$TARGET failed its health checks" rolled_back
fi
