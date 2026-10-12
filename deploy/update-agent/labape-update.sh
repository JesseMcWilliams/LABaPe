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
# rotate <vault_password|bootstrap_ssh_key>: the engine's own secrets
#   (phase R2), which every job copies when it starts, so no restart and no
#   maintenance mode: a new value is made and checked beside the old one,
#   then swapped in; nothing changes when a step fails. The VM key goes to
#   the deployed environments' Linux VMs first (`labape push-vm-key`), then
#   into ~/.ssh as well, for the command line; 7 previous keys are kept.
#   The vault password also goes to the command line's vault on this host
#   (LABAPE_CLI_VAULT and LABAPE_VAULT_PASS_FILE, which install.sh writes to
#   ~/.config/labape/agent.env), rekeyed beside the engine's and swapped
#   with it; LABAPE_CLI_VAULT=none leaves it alone.
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

# --- rotate without restarts (phase R2) ---------------------------------------------
ES="$(grep -m1 '^LABAPE_ENGINE_SECRETS_PATH=' "$C/.env" 2>/dev/null | cut -d= -f2-)"
ES="${ES:-./engine-secrets}"; case "$ES" in /*) ;; *) ES="$C/${ES#./}" ;; esac
KEEP_KEYS=7
CLI_VAULT="${LABAPE_CLI_VAULT:-$REPO/secrets.vault.yml}"
CLI_PASS="${LABAPE_VAULT_PASS_FILE:-$HOME/.labape-vault-pass}"
# cli_opens: the command line's vault opens with its password file (checked in a one-off container).
cli_opens() {
  (cd "$C" && "${COMPOSE[@]}" run --rm --no-deps -v "$CLI_VAULT:/cli/vault.yml:ro" -v "$CLI_PASS:/cli/pass:ro" \
     labape-worker ansible-vault view --vault-password-file /cli/pass /cli/vault.yml >/dev/null)
}
etool() { (cd "$C" && "${COMPOSE[@]}" run --rm --no-deps -v "$ES:/es" labape-worker "$@"); }
r2fail() { status failed "$1" "$2"; finish; exit 1; }   # maintenance mode was never turned on
prune() {   # prune <dir>: keep the newest $KEEP_KEYS key pairs
  ls -1 "$1"/labape_bootstrap-*.pub 2>/dev/null | sort -r | tail -n +$((KEEP_KEYS + 1)) | while read -r pub; do
    rm -f "$pub" "${pub%.pub}"; done
}

rotate_engine() {
  local kind="$1" stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  case "$kind" in
    vault_password)
      status running applying "re-encrypting the engine vault with a new password" "$kind"
      (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$ES/vault-pass.new"
       cp -p "$ES/secrets.vault.yml" "$ES/secrets.vault.yml.new")
      etool ansible-vault rekey --vault-password-file /es/vault-pass --new-vault-password-file /es/vault-pass.new \
          /es/secrets.vault.yml.new >/dev/null \
        && etool ansible-vault view --vault-password-file /es/vault-pass.new /es/secrets.vault.yml.new >/dev/null \
        || { rm -f "$ES/vault-pass.new" "$ES/secrets.vault.yml.new"; r2fail applying "re-encrypting the vault failed; nothing changed"; }
      # The command line's vault (its own contents) gets the same new password, prepared beside the engine's.
      local cli=0
      if [ "$CLI_VAULT" != none ] && [ -f "$CLI_VAULT" ]; then
        (umask 077; cp "$CLI_VAULT" "$ES/cli-vault.new"; cp "$CLI_PASS" "$ES/cli-pass.cur")
        etool ansible-vault rekey --vault-password-file /es/cli-pass.cur --new-vault-password-file /es/vault-pass.new \
            /es/cli-vault.new >/dev/null \
          && etool ansible-vault view --vault-password-file /es/vault-pass.new /es/cli-vault.new >/dev/null \
          || { rm -f "$ES/vault-pass.new" "$ES/secrets.vault.yml.new" "$ES/cli-vault.new" "$ES/cli-pass.cur"
               r2fail applying "the command line's vault ($CLI_VAULT) didn't re-encrypt with its password file ($CLI_PASS); nothing changed"; }
        cli=1
      else
        echo "labape-update: no command-line vault to update ($CLI_VAULT)"
      fi
      # Jobs copy both files when they start and check the pair opens (retrying), so swap them back to back.
      # The command line's files are rewritten in place (cat), keeping their owner and mode.
      cp -p "$ES/vault-pass" "$ES/vault-pass.old"; cp -p "$ES/secrets.vault.yml" "$ES/secrets.vault.yml.old"
      if [ $cli = 1 ]; then
        cp -p "$CLI_VAULT" "$CLI_VAULT.old"; cp -p "$CLI_PASS" "$CLI_PASS.old"
        cat "$ES/cli-vault.new" > "$CLI_VAULT" && cat "$ES/vault-pass.new" > "$CLI_PASS"
      fi
      mv "$ES/secrets.vault.yml.new" "$ES/secrets.vault.yml" && mv "$ES/vault-pass.new" "$ES/vault-pass"
      rm -f "$ES/cli-vault.new" "$ES/cli-pass.cur"
      if etool ansible-vault view --vault-password-file /es/vault-pass /es/secrets.vault.yml >/dev/null \
         && { [ $cli = 0 ] || cli_opens; }; then
        rm -f "$ES/vault-pass.old" "$ES/secrets.vault.yml.old"
        [ $cli = 0 ] || rm -f "$CLI_VAULT.old" "$CLI_PASS.old"
      else
        mv "$ES/secrets.vault.yml.old" "$ES/secrets.vault.yml"; mv "$ES/vault-pass.old" "$ES/vault-pass"
        if [ $cli = 1 ]; then
          cat "$CLI_VAULT.old" > "$CLI_VAULT"; cat "$CLI_PASS.old" > "$CLI_PASS"; rm -f "$CLI_VAULT.old" "$CLI_PASS.old"
        fi
        r2fail checking "the swapped vault didn't open; the old one is back"
      fi ;;
    bootstrap_ssh_key)
      local K="$ES/ssh/labape_bootstrap" H="$ES/ssh/history"
      status running applying "making a new VM key and adding it on the environments' Linux VMs" "$kind"
      rm -f "$K.new" "$K.new.pub"
      ssh-keygen -q -t ed25519 -N '' -C "labape-bootstrap-$stamp" -f "$K.new" \
        || r2fail applying "ssh-keygen failed; nothing changed"
      labape push-vm-key --new /run/labape-secrets/ssh/labape_bootstrap.new \
        || { rm -f "$K.new" "$K.new.pub"; r2fail applying "adding the new key on the VMs failed (see the update agent log); the old key stays"; }
      mkdir -p "$H"; chmod 700 "$H"
      cp -p "$K" "$H/labape_bootstrap-$stamp"; cp -p "$K.pub" "$H/labape_bootstrap-$stamp.pub"
      mv "$K.new.pub" "$K.pub" && mv "$K.new" "$K"
      prune "$H"
      # Option b (question 90): the command line on this host uses the same key.
      mkdir -p "$HOME/.ssh/labape-history"; chmod 700 "$HOME/.ssh" "$HOME/.ssh/labape-history"
      [ -f "$HOME/.ssh/labape_bootstrap" ] && cp -p "$HOME/.ssh/labape_bootstrap" "$HOME/.ssh/labape-history/labape_bootstrap-$stamp" \
        && cp -p "$HOME/.ssh/labape_bootstrap.pub" "$HOME/.ssh/labape-history/labape_bootstrap-$stamp.pub"
      install -m 0600 "$K" "$HOME/.ssh/labape_bootstrap"; install -m 0644 "$K.pub" "$HOME/.ssh/labape_bootstrap.pub"
      prune "$HOME/.ssh/labape-history" ;;
  esac
  status succeeded done "rotated $kind" "$kind"
  finish
}

if [ "$ACTION" = rotate ]; then
  case "$TARGET" in
    vault_password|bootstrap_ssh_key) rotate_engine "$TARGET" ;;
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
