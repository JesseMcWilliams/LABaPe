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
# rotate <master_key|postgres_password|ca_password>: secrets whose files the
#   containers only read (design §24, phase R3): maintenance on -> new value ->
#   apply -> recreate the containers using it -> health checks (the CA check
#   signs a test request) -> maintenance off; everything put back on failure.
# authentik_sso: sign-on between linked stacks (design §47.2): tools/authentik-test/sso.sh.
# stores_setup: stores/stores.sh setup (secret health, S1: new store logins for LABaPe).
# openbao_people: stores/stores.sh people-policies <envs>: each environment's OpenBao policy and
#   owners group (design §49.2), written with the root token from a fixed template.
# openbao_vms: stores/stores.sh vm-policies <file>: each VM's OpenBao policy and certificate sign-in
#   (design §49.3), from the request's `vms` (stores.sh checks every name).
# full_backup: the whole stack in one sealed archive (deploy/stack-backup.sh,
#   design §32); no maintenance mode, nothing stops.
# The engine's own secrets (vault password, VM key) are rotated by the worker,
# in the platform store (design §26, S2/S3); the agent's file-based rotation of
# them (phase R2) was removed on 2026-10-12, once every install had moved.
set -uo pipefail

# shellcheck source=/dev/null
[ -f "$HOME/.config/labape/agent.env" ] && . "$HOME/.config/labape/agent.env"

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
REQ_ID="$(field "$U/processing.json" id)"
STARTED="$(date -u +%FT%TZ)"
FROM_VER="$(git -C "$REPO" describe --tags --always 2>/dev/null)"

# status <state> <step> <message> [to]
status() {
  python3 - "$U/status.json" "$1" "$2" "$3" "${4:-$TARGET}" "$FROM_VER" "$STARTED" "$BY" "$ACTION" "$REQ_ID" <<'PY'
import datetime, json, os, sys
path, state, step, message, to, frm, started, by, action, req_id = sys.argv[1:11]
s = {"id": req_id, "state": state, "step": step, "message": message, "action": action, "from": frm, "to": to,
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

drain() {   # wait for running jobs (maintenance is on)
  local deadline=$((SECONDS + DRAIN_SECONDS)) running
  while :; do
    running="$(labape maintenance status | python3 -c 'import json,sys; print(json.load(sys.stdin)["running_jobs"])')"
    [ "$running" = 0 ] && return 0
    [ $SECONDS -lt $deadline ] || fail maintenance "$running job(s) still running after ${DRAIN_SECONDS}s"
    sleep 15
  done
}

# --- rotate (phase R3) -----------------------------------------------------------
# The bootstrap secrets: container/secrets/, or, once stores.sh has moved them into
# OpenBao (design §26, S4), the memory-only folder LABAPE_SECRETS_DIR names. Then
# each rotation writes the store right after swapping the files (and again when it
# puts the old ones back), so the stack comes back with the same values after a boot.
S="$(grep -m1 '^LABAPE_SECRETS_DIR=' "$C/.env" 2>/dev/null | cut -d= -f2-)"
IN_STORE=0; [ -n "$S" ] && IN_STORE=1
S="${S:-$C/secrets}"
store_push() { [ $IN_STORE = 0 ] || python3 "$C/stores/hostsecrets.py" push "$S"; }
recreate() { (cd "$C" && "${COMPOSE[@]}" up -d --force-recreate "$@"); }   # new secret files need new mounts
tool() { (cd "$C" && "${COMPOSE[@]}" run --rm --no-deps -v "$S:/hs:ro" labape-worker labape "$@"); }
pg_password() { printf "ALTER ROLE labape WITH PASSWORD '%s';\n" "$1" \
  | (cd "$C" && "${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -q -U labape -d labape); }
ca_exec() { (cd "$C" && "${COMPOSE[@]}" exec -T labape-ca "$@"); }
CA_VOLUME="${COMPOSE_PROJECT_NAME:-labape}_labape-ca"
ca_tool() { (cd "$C" && "${COMPOSE[@]}" run --rm --no-deps -v "$S:/hs:ro" -v "$CA_VOLUME:/home/step" labape-worker labape "$@"); }

rotate() {
  local kind="$1" new old f
  case "$kind" in master_key|postgres_password|ca_password) ;; *) fail checking "unknown secret $kind" ;; esac
  status running maintenance "rotating $kind: maintenance on, waiting for running jobs" "$kind"
  labape maintenance on --reason "rotating $kind (restarts services)" --source rotation >/dev/null \
    || fail maintenance "couldn't turn maintenance mode on"
  drain
  new="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  status running applying "applying a new $kind" "$kind"
  case "$kind" in
    master_key)
      (umask 022; printf '%s\n' "$new" > "$S/secret_key.new")
      tool rewrap-master --from /hs/secret_key --to /hs/secret_key.new || { rm -f "$S/secret_key.new"; fail applying "re-wrapping the data keys failed; nothing changed"; }
      cp -p "$S/secret_key" "$S/secret_key.old" && mv "$S/secret_key.new" "$S/secret_key"
      store_push || { mv "$S/secret_key.old" "$S/secret_key"; tool rewrap-master --from /hs/secret_key.old --to /hs/secret_key 2>/dev/null
                      store_push; fail applying "writing the new master key to OpenBao failed; the old one is back"; }
      recreate labape-api labape-worker
      if wait_healthy 120; then rm -f "$S/secret_key.old"; else
        cp -p "$S/secret_key" "$S/secret_key.new"; mv "$S/secret_key.old" "$S/secret_key"
        tool rewrap-master --from /hs/secret_key.new --to /hs/secret_key; rm -f "$S/secret_key.new"
        store_push || true
        recreate labape-api labape-worker; wait_healthy 120 || true
        fail health "the new master key failed the health checks; the old one is back"
      fi ;;
    postgres_password)
      old="$(tr -d '\n' < "$S/postgres_password")"
      pg_password "$new" || fail applying "ALTER ROLE failed; nothing changed"
      for f in postgres_password database_url tofu_pg_conn; do cp -p "$S/$f" "$S/$f.old"; sed -i "s|$old|$new|" "$S/$f"; done
      store_push || { pg_password "$old"; for f in postgres_password database_url tofu_pg_conn; do mv "$S/$f.old" "$S/$f"; done
                      store_push; fail applying "writing the new database password to OpenBao failed; the old one is back"; }
      recreate labape-api labape-worker
      if wait_healthy 120; then for f in postgres_password database_url tofu_pg_conn; do rm -f "$S/$f.old"; done; else
        pg_password "$old"; for f in postgres_password database_url tofu_pg_conn; do mv "$S/$f.old" "$S/$f"; done
        store_push || true
        recreate labape-api labape-worker; wait_healthy 120 || true
        fail health "the new database password failed the health checks; the old one is back"
      fi ;;
    ca_password)
      printf '%s' "$new" | ca_exec sh -c 'umask 077; cat > /home/step/.pw.new'
      ca_exec bash /labape/repass.sh /run/secrets/ca_password /home/step/.pw.new \
        || { ca_exec rm -f /home/step/.pw.new; fail applying "re-encrypting the CA keys failed; nothing changed"; }
      ca_tool ca-repass --config /home/step/config/ca.json --old /hs/ca_password --new /home/step/.pw.new || {
        ca_exec bash /labape/repass.sh /home/step/.pw.new /run/secrets/ca_password; ca_exec rm -f /home/step/.pw.new
        fail applying "re-encrypting the signing provisioner failed; the CA keys were put back"; }
      cp -p "$S/ca_password" "$S/ca_password.old"; (umask 022; printf '%s\n' "$new" > "$S/ca_password.tmp"); mv "$S/ca_password.tmp" "$S/ca_password"
      # The running CA still reads the old file (a replaced file keeps its old mount), so undoing is the same two steps back.
      store_push || {
        ca_exec bash /labape/repass.sh /home/step/.pw.new /run/secrets/ca_password
        ca_tool ca-repass --config /home/step/config/ca.json --old /hs/ca_password --new /hs/ca_password.old
        ca_exec rm -f /home/step/.pw.new; mv "$S/ca_password.old" "$S/ca_password"
        store_push; fail applying "writing the new CA password to OpenBao failed; the old one is back"; }
      recreate labape-ca labape-api labape-worker
      sleep 10
      if wait_healthy 150; then ca_exec rm -f /home/step/.pw.new; rm -f "$S/ca_password.old"; else
        tr -d '\n' < "$S/ca_password.old" | ca_exec sh -c 'umask 077; cat > /home/step/.pw.old'
        ca_exec bash /labape/repass.sh /run/secrets/ca_password /home/step/.pw.old
        ca_tool ca-repass --config /home/step/config/ca.json --old /hs/ca_password --new /home/step/.pw.old
        ca_exec rm -f /home/step/.pw.new /home/step/.pw.old
        mv "$S/ca_password.old" "$S/ca_password"
        store_push || true
        recreate labape-ca labape-api labape-worker; wait_healthy 150 || true
        fail health "the new CA password failed the health checks; the old one is back"
      fi ;;
  esac
  labape maintenance off --source rotation >/dev/null 2>&1 || true
  status succeeded done "rotated $kind" "$kind"
  finish
}

if [ "$ACTION" = full_backup ]; then
  status running backup "backing up the whole stack"
  args=(--repo "$REPO" --dest "$(field "$U/processing.json" dest)" --keep-days "$(field "$U/processing.json" keep_days)")
  [ "$(field "$U/processing.json" syslog)" = False ] && args+=(--no-syslog)
  [ "$(field "$U/processing.json" recordings)" = True ] && args+=(--recordings)
  out="$(bash "$REPO/deploy/stack-backup.sh" "${args[@]}")"; rc=$?
  last="${out##*$'\n'}"
  case $rc in
    0) status succeeded done "backed up the stack to $(basename "$last")" ;;
    3) status failed check "wrote $(basename "$last"), but its check failed; see the backup list" ;;
    *) status failed backup "the stack backup failed (exit $rc); see agent.log" ;;
  esac
  finish
  exit 0
fi

if [ "$ACTION" = stores_setup ]; then
  # Secret health (S1): LABaPe's login to a store stopped working; stores.sh setup gives it a new one
  # with the stores' root credentials kept on this host (and changes nothing that works).
  status running applying "stores.sh setup: new logins for LABaPe where needed"
  if out="$(bash "$C/stores/stores.sh" setup 2>&1)"; then
    status succeeded done "stores.sh setup: ${out##*$'\n'}"
  else
    status failed applying "stores.sh setup failed: ${out##*$'\n'}"
  fi
  finish
  exit 0
fi

if [ "$ACTION" = openbao_people ]; then
  # People's access to their environments in OpenBao (design §49.2): LABaPe names the environments
  # (stores.sh checks each name); the policies come from stores.sh's template, not from LABaPe.
  envs="$(field "$U/processing.json" envs)"
  status running applying "OpenBao: policies for ${envs:-no} environment(s)"
  # shellcheck disable=SC2086  # one word per environment name; stores.sh refuses anything else
  if out="$(bash "$C/stores/stores.sh" people-policies $envs 2>&1)"; then
    status succeeded done "${out##*$'\n'}"
  else
    status failed applying "OpenBao people policies failed: ${out##*$'\n'}"
  fi
  finish
  exit 0
fi

if [ "$ACTION" = openbao_vms ]; then
  # VMs reading their secrets (design §49.3): LABaPe's list of VMs and secret names; the policies
  # and sign-in roles come from stores.sh's template.
  f="$(mktemp)"
  python3 -c 'import json, sys; json.dump({"vms": json.load(open(sys.argv[1])).get("vms", [])}, open(sys.argv[2], "w"))' \
    "$U/processing.json" "$f"
  status running applying "OpenBao: VM policies"
  if out="$(bash "$C/stores/stores.sh" vm-policies "$f" 2>&1)"; then
    status succeeded done "${out##*$'\n'}"
  else
    status failed applying "OpenBao VM policies failed: ${out##*$'\n'}"
  fi
  rm -f "$f"
  finish
  exit 0
fi

if [ "$ACTION" = authentik_sso ]; then
  # Sign-on between linked stacks (design §47.2): the Authentik installed with LABaPe on this host.
  op="$(field "$U/processing.json" op)"
  status running applying "sign-on: $op"
  [ -f "$REPO/tools/authentik-test/.env" ] || { status failed checking "no Authentik installed with LABaPe on this host"; finish; exit 0; }
  if out="$(bash "$REPO/tools/authentik-test/sso.sh" "$U/processing.json" 2>&1)"; then
    status succeeded done "${out##*$'\n'}"
  else
    status failed applying "sign-on $op failed: ${out##*$'\n'}"
  fi
  finish
  exit 0
fi

if [ "$ACTION" = rotate ]; then
  case "$TARGET" in
    vault_password|bootstrap_ssh_key)
      status failed checking "$TARGET is rotated by the worker in the secret store now, not by the update agent"
      finish ;;
    *) rotate "$TARGET" ;;
  esac
  exit 0
fi

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
drain

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
