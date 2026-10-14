#!/usr/bin/env bash
# stack-backup.sh: the whole LABaPe stack in one encrypted archive
# (Claude_Docs/Planning_Web-Interface-Design.md §32, batch B1).
#
#   stack-backup.sh [--dest DIR] [--keep-days N] [--no-syslog] [--recordings] [--repo DIR]
#
# Run on the stack host as the stack's service user; the update agent runs it
# for the web interface's schedule and Back up now. What goes in:
#   db/        pg_dump of LABaPe's database, the syslog collector's (unless
#              --no-syslog), Authentik's, and Conjur's when it runs
#   volumes/   the platform CA, OpenBao's data, LABaPe's data volume (job
#              logs; not its database dumps), the collector's certificates,
#              Caddy's data, and the session recordings with --recordings
#   files/     container/.env, the bootstrap secrets (container/secrets/ or
#              the memory-only LABAPE_SECRETS_DIR), the stores' secrets and
#              certificates, config/, engine-secrets/, Authentik's .env, the
#              installer's saved answers, and ~/.config/labape (not the key)
#   manifest.json  versions, and every file's size and SHA-256
# The tar stream is sealed with the backup key (labape seal: AES-256-GCM)
# inside the LABaPe image, so the host needs only the container runtime. The
# key is ~/.config/labape/backup.key (LABAPE_BACKUP_KEY), made on the first
# run; it's never in an archive, so keep a copy somewhere else.
#
# Afterwards the archive is checked: it's opened again and every file's hash
# compared with the manifest, and the LABaPe and Authentik dumps are loaded
# into scratch databases. The result goes into the archive's sidecar
# (<archive>.json, no secrets), which the web interface lists. Archives older
# than --keep-days go (the newest always stays). The last line printed is the
# archive's path.
set -euo pipefail

REPO="${LABAPE_REPO:-$HOME/LABaPe-web}"
DEST=/data/backups/stack
KEEP=14
SYSLOG=1
RECORDINGS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:?}"; shift ;;
    --keep-days) KEEP="${2:?}"; shift ;;
    --no-syslog) SYSLOG=0 ;;
    --recordings) RECORDINGS=1 ;;
    --repo) REPO="${2:?}"; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "stack-backup: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$KEEP" =~ ^[0-9]+$ ]] || { echo "stack-backup: --keep-days takes a number" >&2; exit 2; }

C="$REPO/container"
AK="$REPO/tools/authentik-test"
KEY="${LABAPE_BACKUP_KEY:-$HOME/.config/labape/backup.key}"
if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); RT=docker; else COMPOSE=(podman compose); RT=podman; fi
envval() { grep -m1 "^$1=" "$C/.env" 2>/dev/null | cut -d= -f2- || true; }
IMAGE="$(envval LABAPE_IMAGE)"; IMAGE="${IMAGE:-labape:local}"
PROJECT="$(envval COMPOSE_PROJECT_NAME)"; PROJECT="${PROJECT:-labape}"
dc() { (cd "$C" && "${COMPOSE[@]}" "$@"); }
# Authentik's PostgreSQL by its compose labels: compose itself would need Authentik's .env, which an
# installer run with sudo leaves as root's.
AKPG="$($RT ps -q --filter label=com.docker.compose.project=authentik-test           --filter label=com.docker.compose.service=postgresql 2>/dev/null | head -1)"
akc() { [ "$1" = exec ] && shift; [ "$1" = -T ] && shift; shift; $RT exec -i "$AKPG" "$@"; }   # akc exec -T <svc> cmd...
running() { [ -n "$(dc ps -q --status running "$1" 2>/dev/null)" ]; }
tool() { $RT run --rm -i --network none -v "$KEY:/k:ro" "$IMAGE" labape seal --key-file /k "$@"; }
say() { echo "stack-backup: $*" >&2; }
warn() { say "$*"; echo "$*" >> "$W/warnings"; }   # a part that should be there and isn't: the check fails

[ -f "$C/.env" ] || { say "no stack at $C (use --repo)"; exit 1; }
[[ "$DEST" = /* ]] || { say "--dest must be an absolute path"; exit 2; }
# The data root is usually root's (e.g. /data): make the folder through the container runtime,
# which the service user may already use as root, and give it to the service user.
if ! mkdir -p "$DEST" 2>/dev/null; then
  anc="$DEST"; while [ ! -d "$anc" ]; do anc="$(dirname "$anc")"; done
  rel="${DEST#"$anc"}"; rel="${rel#/}"
  $RT run --rm --network none --user 0:0 -v "$anc:/p" "$IMAGE" \
    sh -c "mkdir -p '/p/$rel' && chown $(id -u):$(id -g) '/p/$rel' && chmod 750 '/p/$rel'"
  say "made $DEST"
fi
mkdir -p "$(dirname "$KEY")"
if [ ! -s "$KEY" ]; then
  (umask 077; $RT run --rm --network none "$IMAGE" labape backup-key > "$KEY.tmp" && mv "$KEY.tmp" "$KEY")
  say "made a new backup key at $KEY: keep a copy somewhere other than this host"
fi
chmod 600 "$KEY"

W="$(mktemp -d "$DEST/.work-XXXXXX")"
trap 'rm -rf "$W"' EXIT
chmod 700 "$W"
mkdir -p "$W/db" "$W/volumes" "$W/files"

# --- databases ------------------------------------------------------------------
say "dumping the databases"
dc exec -T postgres pg_dump -U labape -Fc labape > "$W/db/labape.dump"
if [ $SYSLOG = 1 ] && running labape-syslog-db; then
  dc exec -T labape-syslog-db pg_dump -h /var/run/postgresql -U postgres -Fc syslog > "$W/db/syslog.dump"
fi
if running labape-conjur-db; then
  dc exec -T labape-conjur-db pg_dump -U postgres -Fc postgres > "$W/db/conjur.dump"
fi
if [ -n "$AKPG" ]; then
  akc exec -T postgresql pg_dump -U authentik -Fc authentik > "$W/db/authentik.dump"
elif [ -f "$AK/.env" ]; then
  warn "Authentik is set up here ($AK/.env) but its database isn't running: not in this backup"
fi

# --- volumes --------------------------------------------------------------------
say "copying the volumes"
VOLS=(labape-ca labape-openbao labape-data labape-syslog-tls caddy-data)
[ $RECORDINGS = 1 ] && VOLS+=(labape-recordings)
for v in "${VOLS[@]}"; do
  $RT volume inspect "${PROJECT}_$v" >/dev/null 2>&1 || continue
  $RT run --rm --network none --user 0:0 -v "${PROJECT}_$v:/v:ro" "$IMAGE" \
    tar -C /v --exclude=./backups -czf - . > "$W/volumes/$v.tgz"
done

# --- files ----------------------------------------------------------------------
say "copying the configuration and secrets"
copy() {   # copy <label> <path>: a file or folder, if it exists
  [ -e "$2" ] || return 0
  mkdir -p "$W/files/$1"
  if [ -z "$(find "$2" ! -readable -print -quit 2>/dev/null)" ]; then
    cp -a "$2" "$W/files/$1/"
  else
    # Root's files (an installer run with sudo writes Authentik's .env and its saved answers
    # as root, 0600): read through the container runtime, like the folder above.
    $RT run --rm --network none --user 0:0 -v "$(dirname "$2"):/s:ro" "$IMAGE" \
      tar -C /s -cf - "$(basename "$2")" | tar -C "$W/files/$1" -xf -
  fi
}
SECRETS="$(envval LABAPE_SECRETS_DIR)"
copy container "$C/.env"
copy bootstrap "${SECRETS:-$C/secrets}"
copy stores "$C/stores/secrets"
copy stores "$C/stores/tls"
cfg="$(envval LABAPE_CONFIG_PATH)"; cfg="${cfg:-./config}"; [[ "$cfg" = /* ]] || cfg="$C/$cfg"
copy container "$cfg"
eng="$(envval LABAPE_ENGINE_SECRETS_PATH)"; eng="${eng:-./engine-secrets}"; [[ "$eng" = /* ]] || eng="$C/$eng"
copy container "$eng"
copy authentik "$AK/.env"
copy installer "$REPO/deploy/state"
if [ -d "$HOME/.config/labape" ]; then
  mkdir -p "$W/files/agent"
  (cd "$HOME/.config/labape" && tar --exclude="$(basename "$KEY")" --exclude='*.key.tmp' -cf - .) | tar -C "$W/files/agent" -xf -
fi

# --- manifest -------------------------------------------------------------------
VERSION="$(git -C "$REPO" describe --tags --always 2>/dev/null || echo unknown)"
COMMIT="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
REV="$(dc exec -T labape-api labape db-status 2>/dev/null | awk '/^database:/{print $2}' || true)"
NAME="labape-stack-$(hostname -s)-$(date -u +%Y%m%d-%H%M%S)"
python3 - "$W" "$NAME" "$VERSION" "$COMMIT" "${REV:-}" "$(envval LABAPE_HOSTNAME)" "$SYSLOG" "$RECORDINGS" <<'PY'
import datetime, hashlib, json, os, socket, sys
w, name, version, commit, rev, web, syslog, rec = sys.argv[1:9]
files = []
for root, dirs, names in os.walk(w):
    dirs.sort()
    for n in sorted(names):
        p = os.path.join(root, n)
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        files.append({"path": os.path.relpath(p, w).replace(os.sep, "/"), "size": os.path.getsize(p), "sha256": h.hexdigest()})
m = {"format": 1, "name": name, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "host": socket.gethostname(), "web_hostname": web, "version": version, "commit": commit, "db_revision": rev,
     "includes": {"syslog": syslog == "1", "recordings": rec == "1"},
     "parts": sorted({f["path"].split("/")[0] + "/" + f["path"].split("/")[1] for f in files if f["path"].count("/")}),
     "files": files}
json.dump(m, open(os.path.join(w, "manifest.json"), "w"), indent=1)
PY

# --- seal -----------------------------------------------------------------------
say "sealing $NAME"
ARCHIVE="$DEST/$NAME.tar.sealed"
tar -C "$W" -cf - . | tool > "$ARCHIVE.part"
mv "$ARCHIVE.part" "$ARCHIVE"
chmod 600 "$ARCHIVE"

# --- check ----------------------------------------------------------------------
say "checking the archive"
CHECK="$W/check.json"
# (A file, not `python3 -`: the program would take stdin, which is the archive here.)
cat > "$W/check.py" <<'PY'
import hashlib, json, sys, tarfile
want = {f["path"]: f["sha256"] for f in json.load(open(sys.argv[1]))["files"]}
got, problems = {}, []
try:
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as t:
        for m in t:
            if not m.isfile():
                continue
            h = hashlib.sha256()
            f = t.extractfile(m)
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
            got[m.name.removeprefix("./")] = h.hexdigest()
except (tarfile.TarError, OSError) as exc:
    problems.append(f"reading the archive: {exc}")
for p, h in want.items():
    if got.get(p) != h:
        problems.append(f"{p}: {'missing' if p not in got else 'hash differs'}")
print(json.dumps({"opened": bool(got), "files": len(want), "problems": problems[:20]}))
PY
tool --open < "$ARCHIVE" | python3 "$W/check.py" "$W/manifest.json" > "$CHECK" || true
[ -s "$CHECK" ] || echo '{"opened": false, "files": 0, "problems": ["the archive did not open"]}' > "$CHECK"

load_check() {   # load_check <label> <dump> <compose fn> <service> <user>: restore into a scratch database
  local label="$1" dump="$2" fn="$3" svc="$4" user="$5" n
  [ -f "$dump" ] || return 0
  $fn exec -T "$svc" sh -c "dropdb -U $user --if-exists labape_restore_check && createdb -U $user labape_restore_check" >/dev/null 2>&1 || {
    echo "$label=could not create a scratch database"; return 0; }
  if $fn exec -T "$svc" pg_restore -U "$user" -d labape_restore_check --no-owner --no-privileges --exit-on-error < "$dump" >/dev/null 2>"$W/restore-$label.err"; then
    n="$($fn exec -T "$svc" psql -U "$user" -d labape_restore_check -tAc "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null | tr -d '[:space:]')"
    echo "$label=ok:${n:-?}"
  else
    echo "$label=failed:$(tail -c 300 "$W/restore-$label.err" | tr '\n"' ' _')"
  fi
  $fn exec -T "$svc" dropdb -U "$user" --if-exists labape_restore_check >/dev/null 2>&1 || true
}
LOADS="$(load_check labape "$W/db/labape.dump" dc postgres labape
         [ -n "$AKPG" ] && load_check authentik "$W/db/authentik.dump" akc postgresql authentik || true)"

python3 - "$W/manifest.json" "$CHECK" "$ARCHIVE" "$LOADS" "$KEY" "$(id -un)" "$W/warnings" <<'PY'
import json, os, sys
manifest, check, archive, loads, key, user, warnings = sys.argv[1:8]
m = json.load(open(manifest))
c = json.load(open(check))
c["restores"] = {}
for line in loads.splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        c["restores"][k] = v
if os.path.isfile(warnings):
    c["problems"] += [w for w in open(warnings).read().splitlines() if w]
c["ok"] = not c["problems"] and all(v.startswith("ok") for v in c["restores"].values())
side = {k: v for k, v in m.items() if k != "files"}
side.update(archive=os.path.basename(archive), size=os.path.getsize(archive), file_count=len(m["files"]),
            check=c, key_file=key, service_user=user)
tmp = archive + ".json.tmp"
json.dump(side, open(tmp, "w"), indent=1)
os.replace(tmp, archive + ".json")
print(json.dumps(c), file=sys.stderr)
PY

# --- retention ------------------------------------------------------------------
newest="$(ls -1t "$DEST"/labape-stack-*.tar.sealed 2>/dev/null | head -1)"
find "$DEST" -maxdepth 1 -name 'labape-stack-*.tar.sealed' -mtime +"$KEEP" -print | while read -r old; do
  [ "$old" = "$newest" ] && continue
  rm -f "$old" "$old.json"
  say "removed $(basename "$old") (older than $KEEP days)"
done

python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["check"]["ok"] else 3)' "$ARCHIVE.json" \
  || { say "the archive was written but its check failed (see $ARCHIVE.json)"; echo "$ARCHIVE"; exit 3; }
echo "$ARCHIVE"
