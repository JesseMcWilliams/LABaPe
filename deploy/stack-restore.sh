#!/usr/bin/env bash
# stack-restore.sh: put a full-stack backup back onto this host's stack
# (Claude_Docs/Planning_Web-Interface-Design.md §33, batch B2).
#
#   sudo deploy/stack-restore.sh ARCHIVE --key-file FILE [--repo DIR] [--user NAME]
#                                [--maintenance] --yes
#
# ARCHIVE is a deploy/stack-backup.sh archive; FILE holds its backup key. The
# stack here must already be installed (deploy/install-labape.py, which also
# runs this with --from-backup): this replaces its whole state with the
# archive's, so the host becomes the stack that was backed up - same web
# address, platform CA, secrets, OpenBao, Authentik users, LABaPe data.
#   1. open the archive and check every file against its manifest
#   2. refuse a backup newer than this checkout (its database layout is unknown here)
#   3. stop the stack and Authentik
#   4. put back .env (keeping this host's own addresses: the syslog and stores
#      bind addresses, and the memory-only secrets folder, which has the
#      service user's uid in it), the bootstrap secrets, the stores' secrets and
#      certificates, config/, engine-secrets/, Authentik's .env, the agent's
#      config, and the backup key
#   5. replace the volumes (platform CA, OpenBao, data, collector certificates, Caddy)
#   6. load the databases into fresh ones, and set each database role's
#      password to the restored one (this host's PostgreSQL volumes were made
#      with the new install's passwords)
#   7. --maintenance: maintenance mode on before anything starts, so a restore
#      drill on a second machine runs no jobs or schedules against the real
#      KVM hosts (it still reads their facts)
#   8. start: OpenBao unsealed with the restored key, the stack, Authentik;
#      wait for /api/health
# Runs as root: the installer leaves some of these files root's. Everything it
# writes keeps the owner and mode of the file it replaces, else it goes to the
# service user (0600).
set -euo pipefail

usage() { sed -n '2,32p' "$0"; exit 2; }
[ "$(id -u)" = 0 ] || { echo "stack-restore: run as root (sudo)" >&2; exit 2; }
ARCHIVE="" KEY="" REPO="" SUSER="" MAINT=0 YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --key-file) KEY="${2:?}"; shift ;;
    --repo) REPO="${2:?}"; shift ;;
    --user) SUSER="${2:?}"; shift ;;
    --maintenance) MAINT=1 ;;
    --yes) YES=1 ;;
    -h|--help) usage ;;
    -*) echo "stack-restore: unknown option $1" >&2; exit 2 ;;
    *) ARCHIVE="$1" ;;
  esac
  shift
done
[ -f "$ARCHIVE" ] && [ -f "$KEY" ] || usage
REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
C="$REPO/container"
AK="$REPO/tools/authentik-test"
[ -f "$C/.env" ] || { echo "stack-restore: no installed stack at $C (install it first, or use --repo)" >&2; exit 1; }
SUSER="${SUSER:-$(stat -c %U "$REPO")}"
SUID="$(id -u "$SUSER")"; SGID="$(id -g "$SUSER")"
SHOME="$(getent passwd "$SUSER" | cut -d: -f6)"
if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); RT=docker; else COMPOSE=(podman compose); RT=podman; fi
envval() { grep -m1 "^$1=" "$C/.env" 2>/dev/null | cut -d= -f2- || true; }
envput() {   # envput KEY VALUE: set (or with an empty VALUE, drop) a line in container/.env
  sed -i "/^$1=/d" "$C/.env"
  [ -z "$2" ] || echo "$1=$2" >> "$C/.env"
}
IMAGE="$(envval LABAPE_IMAGE)"; IMAGE="${IMAGE:-labape:local}"
PROJECT="$(envval COMPOSE_PROJECT_NAME)"; PROJECT="${PROJECT:-labape}"
dc() { (cd "$C" && "${COMPOSE[@]}" "$@"); }
akc() { (cd "$AK" && "${COMPOSE[@]}" "$@"); }
asuser() { runuser -u "$SUSER" -- env XDG_RUNTIME_DIR="/run/user/$SUID" HOME="$SHOME" "$@"; }
say() { echo "stack-restore: $*"; }
die() { echo "stack-restore: $*" >&2; exit 1; }
$RT image inspect "$IMAGE" >/dev/null 2>&1 || die "no $IMAGE image here: install (build) the stack first"

W="$(mktemp -d /var/tmp/labape-restore-XXXXXX)"
trap 'rm -rf "$W"' EXIT
chmod 700 "$W"

# --- 1. open and check -------------------------------------------------------------
say "opening $(basename "$ARCHIVE")"
$RT run --rm -i --network none -v "$(realpath "$KEY"):/k:ro" "$IMAGE" labape seal --open --key-file /k < "$ARCHIVE" \
  | tar -C "$W" --no-same-owner -xf - || die "the archive didn't open (wrong key, or damaged)"
python3 - "$W" <<'PY' || die "the archive doesn't match its manifest"
import hashlib, json, os, sys
w = sys.argv[1]
m = json.load(open(os.path.join(w, "manifest.json")))
bad = []
for f in m["files"]:
    p = os.path.join(w, f["path"])
    if not os.path.isfile(p):
        bad.append(f"{f['path']}: missing")
        continue
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    if h.hexdigest() != f["sha256"]:
        bad.append(f"{f['path']}: hash differs")
for b in bad[:20]:
    print("  " + b, file=sys.stderr)
sys.exit(1 if bad else 0)
PY
mf() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2]); print("" if v is None else v)' "$W/manifest.json" "$1"; }
NAME="$(mf name)"; WEB="$(mf web_hostname)"; REV="$(mf db_revision)"
say "$NAME: taken $(mf created_at) on $(mf host), LABaPe $(mf version), web address $WEB"
[ "$WEB" = "$(envval LABAPE_HOSTNAME)" ] || [ $YES = 1 ] \
  || die "this stack's web address is $(envval LABAPE_HOSTNAME), the backup's $WEB: pass --yes to restore anyway"
[ $YES = 1 ] || die "this replaces the whole stack here; pass --yes"

# --- 2. versions --------------------------------------------------------------------
if [ -n "$REV" ] && [ ! -f "$REPO/app/labape/migrations/versions/$REV.py" ]; then
  die "the backup's database layout ($REV) is newer than this checkout: check out $(mf version) or later, rebuild, and run this again"
fi

# --- 3. stop ------------------------------------------------------------------------
say "stopping the stack"
dc down --remove-orphans >/dev/null 2>&1 || true
COMPOSE_PROFILES=stores dc down >/dev/null 2>&1 || true
[ -f "$AK/compose.yaml" ] && [ -f "$AK/.env" ] && akc down >/dev/null 2>&1 || true

# --- 4. files -----------------------------------------------------------------------
put() {   # put SRC DST: one file, keeping DST's owner and mode when it exists
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then
    install -D -m "$(stat -c %a "$dst")" -o "$(stat -c %u "$dst")" -g "$(stat -c %g "$dst")" "$src" "$dst"
  else
    install -d -m 700 -o "$SUID" -g "$SGID" "$(dirname "$dst")"
    install -m 600 -o "$SUID" -g "$SGID" "$src" "$dst"
  fi
}
put_tree() {   # put_tree SRCDIR DSTDIR: every file under SRCDIR
  local src="$1" dst="$2" f
  [ -d "$src" ] || return 0
  [ -d "$dst" ] || install -d -m 700 -o "$SUID" -g "$SGID" "$dst"
  (cd "$src" && find . -type f -print0) | while IFS= read -r -d '' f; do put "$src/$f" "$dst/${f#./}"; done
}
say "putting back the configuration and secrets"
KEEP_KEYS=(LABAPE_SYSLOG_BIND LABAPE_STORES_BIND)
declare -A keep=()
for k in "${KEEP_KEYS[@]}"; do keep[$k]="$(envval "$k")"; done
put "$W/files/container/.env" "$C/.env"
for k in "${KEEP_KEYS[@]}"; do [ -z "${keep[$k]}" ] || envput "$k" "${keep[$k]}"; done
SECRETS="$(envval LABAPE_SECRETS_DIR)"
if [ -n "$SECRETS" ]; then
  # The memory-only copy the stack reads once the bootstrap secrets are in OpenBao (design §26):
  # /run/user/<uid>/..., so this host's uid.
  SECRETS="/run/user/$SUID/labape-secrets"
  envput LABAPE_SECRETS_DIR "$SECRETS"
fi
boot="$(find "$W/files/bootstrap" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ -n "$boot" ]; then
  if [ -n "$SECRETS" ]; then
    install -d -m 700 -o "$SUID" -g "$SGID" "$SECRETS"
    for f in "$boot"/*; do install -m 644 -o "$SUID" -g "$SGID" "$f" "$SECRETS/$(basename "$f")"; done
  else
    put_tree "$boot" "$C/secrets"
  fi
fi
put_tree "$W/files/stores/secrets" "$C/stores/secrets"
put_tree "$W/files/stores/tls" "$C/stores/tls"
cfg="$(envval LABAPE_CONFIG_PATH)"; cfg="${cfg:-./config}"; [[ "$cfg" = /* ]] || cfg="$C/$cfg"
eng="$(envval LABAPE_ENGINE_SECRETS_PATH)"; eng="${eng:-./engine-secrets}"; [[ "$eng" = /* ]] || eng="$C/$eng"
for d in "$W/files/container"/*/; do
  [ -d "$d" ] || continue
  case "$(basename "$d")" in
    "$(basename "$cfg")") put_tree "$d" "$cfg" ;;
    "$(basename "$eng")") put_tree "$d" "$eng" ;;
  esac
done
[ -f "$W/files/authentik/.env" ] && put "$W/files/authentik/.env" "$AK/.env"
if [ -d "$W/files/agent" ]; then
  put_tree "$W/files/agent" "$SHOME/.config/labape"
  old="$(mf home)"; oldrepo="$(mf repo)"
  for f in "$SHOME/.config/labape/agent.env" "$SHOME/.config/labape/store.env"; do
    [ -f "$f" ] || continue
    [ -z "$oldrepo" ] || [ "$oldrepo" = "$REPO" ] || sed -i "s#$oldrepo#$REPO#g" "$f"
    [ -z "$old" ] || [ "$old" = "$SHOME" ] || sed -i "s#$old#$SHOME#g" "$f"
  done
fi
[ -s "$SHOME/.config/labape/backup.key" ] || put "$KEY" "$SHOME/.config/labape/backup.key"

# --- 5. volumes ---------------------------------------------------------------------
say "replacing the volumes"
for t in "$W/volumes"/*.tgz; do
  [ -f "$t" ] || continue
  v="$(basename "$t" .tgz)"
  $RT volume rm -f "${PROJECT}_$v" >/dev/null 2>&1 || true
  # Compose's own labels, so `compose up` takes the volume as its own.
  $RT volume create --label "com.docker.compose.project=$PROJECT" --label "com.docker.compose.volume=$v" \
    "${PROJECT}_$v" >/dev/null
  $RT run --rm -i --network none --user 0:0 -v "${PROJECT}_$v:/v" "$IMAGE" tar -C /v -xzf - < "$t"
done

# --- 6. databases -------------------------------------------------------------------
secret() { cat "${SECRETS:-$C/secrets}/$1" 2>/dev/null || true; }
wait_pg() {   # wait_pg <compose fn> <service> <psql args...>
  local fn="$1" svc="$2"; shift 2
  for _ in $(seq 60); do $fn exec -T "$svc" pg_isready "$@" >/dev/null 2>&1 && return 0; sleep 2; done
  die "$svc didn't start"
}
load() {   # load <compose fn> <service> <maintenance db> <db> <owner> <dump> <psql args...>
  local fn="$1" svc="$2" mdb="$3" db="$4" owner="$5" dump="$6"; shift 6
  say "loading the $db database ($svc)"
  $fn exec -T "$svc" psql "$@" -d "$mdb" -v ON_ERROR_STOP=1 -q \
    -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" -c "CREATE DATABASE \"$db\" OWNER \"$owner\"" >/dev/null
  $fn exec -T "$svc" pg_restore "$@" -d "$db" --exit-on-error < "$dump" || die "loading $db failed"
}
role_password() {   # role_password <compose fn> <service> <role> <password> <psql args...>
  local fn="$1" svc="$2" role="$3" pw="$4"; shift 4
  [ -n "$pw" ] || return 0
  printf "ALTER ROLE \"%s\" WITH PASSWORD '%s';\n" "$role" "${pw//\'/\'\'}" \
    | $fn exec -T "$svc" psql "$@" -d postgres -v ON_ERROR_STOP=1 -q >/dev/null
}
PGPW="$(secret postgres_password | tr -d '\n')"
dc up -d postgres >/dev/null
wait_pg dc postgres -U labape
load dc postgres postgres labape labape "$W/db/labape.dump" -U labape
role_password dc postgres labape "$PGPW" -U labape
if [ -f "$W/db/syslog.dump" ] && dc config --services 2>/dev/null | grep -qx labape-syslog-db; then
  dc up -d labape-syslog-db >/dev/null
  wait_pg dc labape-syslog-db -h /var/run/postgresql -U postgres
  load dc labape-syslog-db postgres syslog labape_syslog "$W/db/syslog.dump" -h /var/run/postgresql -U postgres
  role_password dc labape-syslog-db postgres "$PGPW" -h /var/run/postgresql -U postgres
fi
if [ -f "$W/db/conjur.dump" ] && COMPOSE_PROFILES=stores dc config --services 2>/dev/null | grep -qx labape-conjur-db; then
  COMPOSE_PROFILES=stores dc up -d labape-conjur-db >/dev/null
  wait_pg dc labape-conjur-db -U postgres
  load dc labape-conjur-db template1 postgres postgres "$W/db/conjur.dump" -U postgres
  role_password dc labape-conjur-db postgres "$(tr -d '\n' < "$C/stores/secrets/conjur_db_password" 2>/dev/null || true)" -U postgres
fi
if [ -f "$W/db/authentik.dump" ] && [ -f "$AK/compose.yaml" ]; then
  akc up -d postgresql >/dev/null
  wait_pg akc postgresql -U authentik
  load akc postgresql postgres authentik authentik "$W/db/authentik.dump" -U authentik
  role_password akc postgresql authentik "$(grep -m1 '^PG_PASS=' "$AK/.env" | cut -d= -f2-)" -U authentik
fi

# --- 7. maintenance (drills) ---------------------------------------------------------------
if [ $MAINT = 1 ]; then
  dc run --rm --no-deps labape-worker labape maintenance on --reason "restored from $NAME (restore drill)" \
    --source restore --actor stack-restore >/dev/null
  say "maintenance mode is on: no jobs or schedules run until an admin turns it off"
fi

# --- 8. start -----------------------------------------------------------------------
say "starting the stores, the stack and Authentik"
chown "$SUID:$SGID" "$C/.env"
asuser bash "$C/stores/stores.sh" unseal || die "starting the stores and unsealing OpenBao failed"
dc up -d >/dev/null
[ -f "$AK/compose.yaml" ] && akc up -d >/dev/null
url="https://$(envval LABAPE_HOSTNAME)/api/health"
for _ in $(seq 60); do
  curl -sk --max-time 5 --resolve "$(envval LABAPE_HOSTNAME):443:127.0.0.1" "$url" | grep -q '"ok":true' && break
  sleep 5
done
curl -sk --max-time 5 --resolve "$(envval LABAPE_HOSTNAME):443:127.0.0.1" "$url" | grep -q '"ok":true' \
  || die "the stack didn't answer $url within 5 minutes; see docker compose logs in $C"
dc exec -T labape-api labape health --deep >/dev/null 2>&1 && say "deep health check passed" \
  || say "the web interface answers, but labape health --deep doesn't pass yet (the worker may still be starting)"
say "restored $NAME: $url"
