#!/usr/bin/env bash
# Stand up throwaway OpenBao and CyberArk Conjur (Open Source) instances for
# building LABaPe's external secret stores (design §11, phase 10d). Run as
# root on a Linux host with Docker (installed here on Debian/Ubuntu if missing).
#
#   sudo tools/secrets-test/deploy.sh [options]   set up or refresh, then smoke-test
#   sudo tools/secrets-test/deploy.sh --unseal    unseal OpenBao (the boot unit runs this)
#   sudo tools/secrets-test/deploy.sh --tls-reload   copy the certificate again and reload
#   sudo tools/secrets-test/deploy.sh --tls-renew    re-issue it from the stack when due (stack mode)
#   sudo tools/secrets-test/deploy.sh --down [--purge]   stop; --purge also deletes data and secrets
#
# Options (remembered in .env):
#   --tls-host            use the platform CA certificate LABaPe gave this VM
#                         (/etc/labape/tls/host.crt, renewed on the VM). Default.
#   --tls-from-stack DIR  this host runs the LABaPe stack in DIR (its container/
#                         folder): make a key here and have the platform CA sign
#                         it with `labape ca-sign`; a daily timer renews it.
#   --names a,b,c         names and addresses for --tls-from-stack (default: the
#                         short host name, the full one and the primary address)
#   --bind ADDRESS        publish the ports on this address only (default 0.0.0.0;
#                         on the LABaPe host use its LAN address, because Caddy
#                         holds 127.0.0.1:8443 for break-glass)
#
# OpenBao listens on 8200 and Conjur on 8443 (OPENBAO_PORT, CONJUR_PORT in .env).
# Everything secret goes to .env (mode 0600, git-ignored) and is never printed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
ENVF="$HERE/.env"
MODE=deploy PURGE=false
TLS_SOURCE_ARG="" STACK_DIR_ARG="" NAMES_ARG="" BIND_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --unseal) MODE=unseal ;;
    --tls-reload) MODE=tls-reload ;;
    --tls-renew) MODE=tls-renew ;;
    --down) MODE=down ;;
    --purge) PURGE=true ;;
    --tls-host) TLS_SOURCE_ARG=host ;;
    --tls-from-stack) TLS_SOURCE_ARG=stack; STACK_DIR_ARG="$(cd "${2:?}" && pwd)"; shift ;;
    --names) NAMES_ARG="${2:?}"; shift ;;
    --bind) BIND_ARG="${2:?}"; shift ;;
    *) sed -n '2,25p' "$0" >&2; exit 1 ;;
  esac
  shift
done
[ "$(id -u)" = 0 ] || { echo "secrets-test: run as root (sudo)" >&2; exit 1; }

say() { echo "secrets-test: $*"; }
die() { echo "secrets-test: $*" >&2; exit 1; }
get() { grep -m1 "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }
drop() { [ -f "$ENVF" ] && sed -i "/^$1=/d" "$ENVF"; return 0; }   # drop KEY: remove one .env line
put() {   # put KEY VALUE: set one .env line
  touch "$ENVF"; chmod 600 "$ENVF"
  if grep -q "^$1=" "$ENVF"; then sed -i "s|^$1=.*|$1=$2|" "$ENVF"; else echo "$1=$2" >> "$ENVF"; fi
}
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
json() {   # json KEY...: one value from the JSON on stdin (list indexes are numbers)
  python3 -c 'import json, sys
d = json.load(sys.stdin)
for k in sys.argv[1:]:
    d = d[int(k)] if isinstance(d, list) else d[k]
print(d)' "$@"
}

if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(podman compose); fi
dc() { "${COMPOSE[@]}" --project-directory "$HERE" -f "$HERE/compose.yaml" "$@"; }

# --- TLS ---------------------------------------------------------------------------
TLS="$HERE/tls"
HOST_TLS=/etc/labape/tls
copy_host_tls() {
  [ -s "$HOST_TLS/host.crt" ] && [ -s "$HOST_TLS/host.key" ] && [ -s "$HOST_TLS/root.crt" ] \
    || die "no platform certificate in $HOST_TLS (deploy this VM from the LABaPe web interface, or use --tls-from-stack)"
  install -d -m 0755 "$TLS"
  install -m 0644 "$HOST_TLS/host.crt" "$TLS/server.crt"
  install -m 0644 "$HOST_TLS/root.crt" "$TLS/root.crt"
  install -m 0400 "$HOST_TLS/host.key" "$TLS/server.key"
}
stack_sign() {   # a new key and certificate from the LABaPe stack in STACK_DIR
  local dir names cn san="" n
  dir="$(get STACK_DIR)"; names="$(get TLS_NAMES)"; cn="${names%%,*}"
  IFS=, read -ra list <<< "$names"
  for n in "${list[@]}"; do
    if [[ "$n" =~ ^[0-9.]+$|: ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi
  done
  install -d -m 0755 "$TLS"
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$TLS/server.key.new" \
    -subj "/CN=$cn" -addext "subjectAltName=${san%,}" -out "$TLS/server.csr" 2>/dev/null
  # The CLI logs its database checks on stderr: shown only when signing fails.
  (cd "$dir" && "${COMPOSE[@]}" exec -T labape-api labape ca-sign --with-root) < "$TLS/server.csr" > "$TLS/bundle.pem" \
      2> "$TLS/ca-sign.log" \
    || { grep -v ' INFO ' "$TLS/ca-sign.log" >&2; rm -f "$TLS/server.key.new" "$TLS/bundle.pem" "$TLS/ca-sign.log"
         die "the platform CA didn't sign the request (labape ca-sign in $dir)"; }
  rm -f "$TLS/ca-sign.log"
  python3 - "$TLS/bundle.pem" "$TLS" <<'PY'
import re, sys
certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n?", open(sys.argv[1]).read(), re.S)
open(sys.argv[2] + "/server.crt.new", "w").write("".join(c if c.endswith("\n") else c + "\n" for c in certs[:-1]))
open(sys.argv[2] + "/root.crt", "w").write(certs[-1] if certs[-1].endswith("\n") else certs[-1] + "\n")
PY
  chmod 0400 "$TLS/server.key.new"; chmod 0644 "$TLS/server.crt.new" "$TLS/root.crt"
  mv "$TLS/server.key.new" "$TLS/server.key"; mv "$TLS/server.crt.new" "$TLS/server.crt"
  rm -f "$TLS/server.csr" "$TLS/bundle.pem"
}
own_tls() {   # OpenBao runs as its own user; nginx's master reads as root
  local uid; uid="$(docker run --rm --entrypoint id openbao/openbao:2.7.1 -u openbao 2>/dev/null || echo 100)"
  chown "$uid" "$TLS/server.key"
}
trust_root() {   # the host trusts the platform root (the VM role does this already)
  if [ -d /usr/local/share/ca-certificates ] && ! cmp -s "$TLS/root.crt" /usr/local/share/ca-certificates/labape-root-ca.crt; then
    install -m 0644 "$TLS/root.crt" /usr/local/share/ca-certificates/labape-root-ca.crt && update-ca-certificates >/dev/null 2>&1
  elif [ -d /etc/pki/ca-trust/source/anchors ] && ! cmp -s "$TLS/root.crt" /etc/pki/ca-trust/source/anchors/labape-root-ca.crt; then
    install -m 0644 "$TLS/root.crt" /etc/pki/ca-trust/source/anchors/labape-root-ca.crt && update-ca-trust >/dev/null 2>&1
  fi
}
server_name() {   # the certificate's first DNS name, for clients that connect by address
  openssl x509 -in "$TLS/server.crt" -noout -ext subjectAltName 2>/dev/null | grep -o 'DNS:[^,]*' | head -1 | cut -d: -f2
}
fetch_tls() { if [ "$(get TLS_SOURCE)" = stack ]; then stack_sign; else copy_host_tls; fi; own_tls; trust_root; }
reload_tls() {
  dc kill -s HUP openbao >/dev/null 2>&1 || true
  dc exec -T conjur-tls nginx -s reload >/dev/null 2>&1 || true
}

# --- OpenBao ------------------------------------------------------------------------
bao() { dc exec -T -e BAO_TOKEN="$(get OPENBAO_ROOT_TOKEN)" openbao bao "$@"; }
bao_state() {   # 0 unsealed, 2 sealed, 1 not answering
  local i rc
  for i in $(seq 1 30); do
    rc=0; dc exec -T openbao bao status >/dev/null 2>&1 || rc=$?
    [ "$rc" = 0 ] || [ "$rc" = 2 ] && return "$rc"
    sleep 2
  done
  return 1
}
unseal() {
  local rc=0; bao_state || rc=$?
  [ "$rc" = 1 ] && die "OpenBao isn't answering (docker compose logs openbao)"
  local status   # `bao status` exits 2 when sealed, so read its output rather than its exit code
  status="$(dc exec -T openbao bao status -format=json 2>/dev/null || true)"
  if [ "$(echo "$status" | json initialized 2>/dev/null)" != True ]; then
    [ -z "$(get OPENBAO_UNSEAL_KEY)" ] || die "OpenBao looks uninitialized but .env has an unseal key; not initializing again"
    say "initializing OpenBao (one unseal key: test use only)"
    out="$(dc exec -T openbao bao operator init -key-shares=1 -key-threshold=1 -format=json)"
    put OPENBAO_UNSEAL_KEY "$(echo "$out" | json unseal_keys_b64 0)"
    put OPENBAO_ROOT_TOKEN "$(echo "$out" | json root_token)"
    rc=2
  fi
  if [ "$rc" = 2 ]; then
    dc exec -T openbao bao operator unseal "$(get OPENBAO_UNSEAL_KEY)" >/dev/null || die "unsealing OpenBao failed"
    say "OpenBao unsealed"
  fi
}
configure_bao() {
  bao secrets list -format=json | grep -q '"labape/"' || bao secrets enable -path=labape -version=2 kv >/dev/null
  bao auth list -format=json | grep -q '"approle/"' || bao auth enable approle >/dev/null
  bao policy write labape - >/dev/null <<'HCL'
# LABaPe's own secrets: KV v2 under labape/
path "labape/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
path "labape/metadata/*" { capabilities = ["read", "list", "delete"] }
path "labape/metadata"   { capabilities = ["list"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
# Rotating its own AppRole secret ID (LABaPe design §25, P3)
path "auth/approle/role/labape/secret-id"         { capabilities = ["update"] }
path "auth/approle/role/labape/secret-id/destroy" { capabilities = ["update"] }
HCL
  bao write auth/approle/role/labape token_policies=labape token_ttl=1h token_max_ttl=4h secret_id_ttl=0 >/dev/null
  put OPENBAO_ROLE_ID "$(bao read -field=role_id auth/approle/role/labape/role-id)"
  [ -n "$(get OPENBAO_SECRET_ID)" ] || [ "$(get LABAPE_OWNS_LOGINS)" = 1 ] \
    || put OPENBAO_SECRET_ID "$(bao write -f -field=secret_id auth/approle/role/labape/secret-id)"
}

# --- Conjur -------------------------------------------------------------------------
configure_conjur() {
  local i out
  for i in $(seq 1 60); do
    dc exec -T conjur curl -fsS -o /dev/null http://localhost:80/ 2>/dev/null && break
    [ "$i" = 60 ] && die "Conjur isn't answering (docker compose logs conjur)"
    sleep 3
  done
  if [ -z "$(get CONJUR_ADMIN_API_KEY)" ]; then
    out="$(dc exec -T conjur conjurctl account create labape 2>&1)" \
      || die "creating the Conjur account failed: $(echo "$out" | tail -1)"
    put CONJUR_ADMIN_API_KEY "$(echo "$out" | sed -n 's/.*API key for admin: *//p' | tr -d '\r')"
    say "Conjur account labape created"
  fi
  # The first load prints the new host's API key: keep it, never show it.
  out="$(dc exec -T conjur conjurctl policy load labape /policy/labape.yml 2>&1)" \
    || die "loading the Conjur policy failed: $(echo "$out" | grep -iv api_key | tail -1)"
  i="$(echo "$out" | sed -n 's/.*"api_key": *"\([^"]*\)".*/\1/p' | head -1)"
  if [ -n "$i" ]; then put CONJUR_HOST_API_KEY "$i"; fi
  # Lost before LABaPe ever had it: issue host labape/app a new key as admin.
  # Never once LABaPe owns the key (forget_rotated): that would lock it out.
  if [ -z "$(get CONJUR_HOST_API_KEY)" ] && [ "$(get LABAPE_OWNS_LOGINS)" != 1 ]; then
    local tok
    tok="$(conjur_token admin "$(get CONJUR_ADMIN_API_KEY)")"
    put CONJUR_HOST_API_KEY "$(curl -fsS --cacert "$TLS/root.crt" --resolve "$NAME:$CPORT:$ADDR" -X PUT \
      -H "Authorization: Token token=\"$tok\"" "https://$NAME:$CPORT/authn/labape/api_key?role=host:labape/app")"
  fi
}
conjur_token() {   # conjur_token <login> <api key>: a base64 access token
  local login; login="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1")"
  curl -fsS --cacert "$TLS/root.crt" --resolve "$NAME:$CPORT:$ADDR" -H "Accept-Encoding: base64" \
    --data-binary "$2" "https://$NAME:$CPORT/authn/labape/$login/authenticate"
}

# --- LABaPe's own logins, once LABaPe has rotated them -------------------------------
# LABaPe rotates its AppRole secret ID and its Conjur host key (LABaPe design
# §25.3). From then on the copies here are stale: drop them and record
# LABAPE_OWNS_LOGINS=1, so this script never issues them again (a new Conjur
# host key would lock LABaPe out). Only a refused sign-in counts: a store that
# isn't answering yet (at boot) leaves everything as it is.
forget_rotated() {
  local name addr code sid key
  name="$(get SERVER_NAME)"; addr="$(get BIND_ADDRESS)"; [ "$addr" = 0.0.0.0 ] && addr=127.0.0.1
  [ -n "$name" ] || return 0
  sid="$(get OPENBAO_SECRET_ID)"
  if [ -n "$sid" ]; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$name:$(get OPENBAO_PORT):$addr" -X POST \
      -d "{\"role_id\":\"$(get OPENBAO_ROLE_ID)\",\"secret_id\":\"$sid\"}" \
      "https://$name:$(get OPENBAO_PORT)/v1/auth/approle/login" 2>/dev/null || true)"
    case "$code" in
      400|401|403) drop OPENBAO_SECRET_ID; put LABAPE_OWNS_LOGINS 1
                   say "OpenBao: LABaPe has rotated its secret ID; removed the stale copy from .env" ;;
    esac
  fi
  key="$(get CONJUR_HOST_API_KEY)"
  if [ -n "$key" ]; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --resolve "$name:$(get CONJUR_PORT):$addr" \
      --data-binary "$key" "https://$name:$(get CONJUR_PORT)/authn/labape/host%2Flabape%2Fapp/authenticate" \
      2>/dev/null || true)"
    case "$code" in
      401|403) drop CONJUR_HOST_API_KEY; put LABAPE_OWNS_LOGINS 1
               say "Conjur: LABaPe has rotated the host's API key; removed the stale copy from .env" ;;
    esac
  fi
}

# --- smoke test, as LABaPe would use them ------------------------------------------------
smoke() {
  local tok value sid
  # A throwaway secret ID, destroyed afterwards: once LABaPe rotates its own,
  # the OPENBAO_SECRET_ID in .env no longer signs in (LABaPe design §25, P3).
  sid="$(bao write -f -field=secret_id auth/approle/role/labape/secret-id)"
  tok="$(curl -fsS --resolve "$NAME:$BPORT:$ADDR" -X POST \
    -d "{\"role_id\":\"$(get OPENBAO_ROLE_ID)\",\"secret_id\":\"$sid\"}" \
    "https://$NAME:$BPORT/v1/auth/approle/login" | json auth client_token)"
  bao write auth/approle/role/labape/secret-id/destroy secret_id="$sid" >/dev/null
  value="smoke-$(date +%s)"
  curl -fsS --resolve "$NAME:$BPORT:$ADDR" -H "X-Vault-Token: $tok" -o /dev/null \
    -d "{\"data\":{\"value\":\"$value\"}}" "https://$NAME:$BPORT/v1/labape/data/secrets-test/smoke"
  [ "$(curl -fsS --resolve "$NAME:$BPORT:$ADDR" -H "X-Vault-Token: $tok" \
        "https://$NAME:$BPORT/v1/labape/data/secrets-test/smoke" | json data data value)" = "$value" ] \
    || die "OpenBao smoke test: the value didn't read back"
  say "OpenBao: AppRole sign-in, write and read back under labape/ worked (system trust store)"
  if ! tok="$(conjur_token host/labape/app "$(get CONJUR_HOST_API_KEY)" 2>/dev/null)"; then
    say "Conjur: the host key in .env no longer signs in (LABaPe rotates it); host smoke test skipped"
    return 0
  fi
  curl -fsS --resolve "$NAME:$CPORT:$ADDR" -H "Authorization: Token token=\"$tok\"" -o /dev/null -X POST \
    --data-binary '- !variable secrets-test/smoke' "https://$NAME:$CPORT/policies/labape/policy/labape%2Fsecrets"
  curl -fsS --resolve "$NAME:$CPORT:$ADDR" -H "Authorization: Token token=\"$tok\"" -o /dev/null \
    --data-binary "$value" "https://$NAME:$CPORT/secrets/labape/variable/labape%2Fsecrets%2Fsecrets-test%2Fsmoke"
  [ "$(curl -fsS --resolve "$NAME:$CPORT:$ADDR" -H "Authorization: Token token=\"$tok\"" \
        "https://$NAME:$CPORT/secrets/labape/variable/labape%2Fsecrets%2Fsecrets-test%2Fsmoke")" = "$value" ] \
    || die "Conjur smoke test: the value didn't read back"
  say "Conjur: host labape/app signed in, declared a variable, set it and read it back (system trust store)"
}

# --- systemd units: unseal at boot, follow certificate renewals ------------------------------
install_units() {
  command -v systemctl >/dev/null 2>&1 || return 0
  cat > /etc/systemd/system/labape-secrets-test.service <<UNIT
[Unit]
Description=LABaPe secrets-test: unseal OpenBao after boot
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$HERE/deploy.sh --unseal

[Install]
WantedBy=multi-user.target
UNIT
  if [ "$(get TLS_SOURCE)" = stack ]; then
    cat > /etc/systemd/system/labape-secrets-test-tls.service <<UNIT
[Unit]
Description=LABaPe secrets-test: renew the certificate from the platform CA when due

[Service]
Type=oneshot
ExecStart=$HERE/deploy.sh --tls-renew
UNIT
    cat > /etc/systemd/system/labape-secrets-test-tls.timer <<UNIT
[Unit]
Description=LABaPe secrets-test: daily certificate check

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    rm -f /etc/systemd/system/labape-secrets-test-tls.path
    systemctl daemon-reload
    systemctl enable --now labape-secrets-test-tls.timer >/dev/null 2>&1
  else
    cat > /etc/systemd/system/labape-secrets-test-tls.service <<UNIT
[Unit]
Description=LABaPe secrets-test: pick up the renewed host certificate

[Service]
Type=oneshot
ExecStart=$HERE/deploy.sh --tls-reload
UNIT
    cat > /etc/systemd/system/labape-secrets-test-tls.path <<UNIT
[Unit]
Description=LABaPe secrets-test: watch the host certificate

[Path]
PathChanged=$HOST_TLS/host.crt

[Install]
WantedBy=multi-user.target
UNIT
    rm -f /etc/systemd/system/labape-secrets-test-tls.timer
    systemctl daemon-reload
    systemctl enable --now labape-secrets-test-tls.path >/dev/null 2>&1
  fi
  systemctl enable labape-secrets-test.service >/dev/null 2>&1
}

# --- modes --------------------------------------------------------------------------
case "$MODE" in
  down)
    dc down $($PURGE && echo -v) || true
    if $PURGE; then
      systemctl disable --now labape-secrets-test.service labape-secrets-test-tls.path labape-secrets-test-tls.timer \
        >/dev/null 2>&1 || true
      rm -f /etc/systemd/system/labape-secrets-test*.{service,path,timer}; systemctl daemon-reload 2>/dev/null || true
      rm -rf "$TLS" "$ENVF"
      say "stopped and purged (data volumes, .env, tls/ and the systemd units)"
    else
      say "stopped (data kept; deploy.sh starts it again and unseals)"
    fi
    exit 0 ;;
  unseal) dc up -d >/dev/null 2>&1 || true; unseal; forget_rotated; exit 0 ;;   # never rely on the restart policy alone
  tls-reload)
    [ "$(get TLS_SOURCE)" = stack ] || copy_host_tls
    own_tls; reload_tls; say "certificate reloaded"; exit 0 ;;
  tls-renew)
    if openssl x509 -checkend $((10 * 86400)) -noout -in "$TLS/server.crt" >/dev/null 2>&1; then exit 0; fi
    stack_sign; own_tls; reload_tls; say "certificate renewed"; exit 0 ;;
esac

# deploy
if ! command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || die "install Docker (with the compose plugin) first"
  say "installing Docker"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose >/dev/null
  systemctl enable --now docker >/dev/null
  COMPOSE=(docker compose)
fi
"${COMPOSE[@]}" version >/dev/null 2>&1 || die "docker compose (v2) is missing"
for tool in curl openssl python3; do
  command -v "$tool" >/dev/null 2>&1 && continue
  command -v apt-get >/dev/null 2>&1 || die "install $tool first"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$tool" >/dev/null
done

[ -n "$TLS_SOURCE_ARG" ] && put TLS_SOURCE "$TLS_SOURCE_ARG"
[ -n "$(get TLS_SOURCE)" ] || put TLS_SOURCE host
[ -n "$STACK_DIR_ARG" ] && put STACK_DIR "$STACK_DIR_ARG"
[ "$(get TLS_SOURCE)" = stack ] && [ -z "$(get STACK_DIR)" ] && die "--tls-from-stack needs the stack's container/ folder"
if [ -n "$NAMES_ARG" ]; then put TLS_NAMES "$NAMES_ARG"
elif [ "$(get TLS_SOURCE)" = stack ] && [ -z "$(get TLS_NAMES)" ]; then
  put TLS_NAMES "$(hostname -s),$(hostname -f 2>/dev/null || hostname),$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2)"
fi
[ -n "$BIND_ARG" ] && put BIND_ADDRESS "$BIND_ARG"
[ -n "$(get BIND_ADDRESS)" ] || put BIND_ADDRESS 0.0.0.0
[ -n "$(get OPENBAO_PORT)" ] || put OPENBAO_PORT 8200
[ -n "$(get CONJUR_PORT)" ] || put CONJUR_PORT 8443
[ -n "$(get CONJUR_DB_PASSWORD)" ] || put CONJUR_DB_PASSWORD "$(gen)"
[ -n "$(get CONJUR_DATA_KEY)" ] || put CONJUR_DATA_KEY "$(openssl rand -base64 32)"

if [ "$(get TLS_SOURCE)" = stack ] && openssl x509 -checkend $((10 * 86400)) -noout -in "$TLS/server.crt" >/dev/null 2>&1; then
  own_tls; trust_root          # still good: keep it
else
  fetch_tls
fi
put SERVER_NAME "$(server_name)"
NAME="$(get SERVER_NAME)"; BPORT="$(get OPENBAO_PORT)"; CPORT="$(get CONJUR_PORT)"
ADDR="$(get BIND_ADDRESS)"; [ "$ADDR" = 0.0.0.0 ] && ADDR=127.0.0.1
[ -n "$NAME" ] || die "the certificate has no DNS name"

say "starting the containers"
for try in 1 2 3; do   # a registry timeout shouldn't end the run (seen on the LABaPe host)
  dc pull -q >/dev/null 2>&1 && break
  [ "$try" = 3 ] && die "pulling the images failed three times (docker compose pull in $HERE)"
  say "pulling the images failed; trying again in 15 seconds"; sleep 15
done
dc up -d >/dev/null
reload_tls
unseal
forget_rotated
configure_bao
configure_conjur
install_units
smoke
OWNED=""; [ "$(get LABAPE_OWNS_LOGINS)" = 1 ] && OWNED="its login is LABaPe's own (rotated; no copy here)"
cat <<EOF
secrets-test: ready (credentials in $ENVF, mode 0600; never commit it).
  OpenBao: https://$NAME:$BPORT  (or by address; the certificate covers: $(openssl x509 -in "$TLS/server.crt" -noout -ext subjectAltName | tail -1 | tr -d ' '))
           KV v2 at labape/, AppRole role labape: OPENBAO_ROLE_ID, ${OWNED:-OPENBAO_SECRET_ID}; root: OPENBAO_ROOT_TOKEN
  Conjur:  https://$NAME:$CPORT  account labape
           host labape/app (owns policy labape/secrets): ${OWNED:-CONJUR_HOST_API_KEY}; admin: CONJUR_ADMIN_API_KEY
  Trust:   the LABaPe platform root (tls/root.crt), also added to this host's trust store.
EOF
