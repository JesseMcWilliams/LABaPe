#!/usr/bin/env bash
# The stack's secret stores, OpenBao and Conjur Open Source (Claude_Docs/
# Planning_Web-Interface-Design.md §26). Run as the service user that owns
# container/ (no root): it needs the stack's CA (labape-api) up.
#
#   stores/stores.sh [setup]     set up or refresh: certificates, secret files,
#                                initialize and unseal, policies and logins,
#                                LABaPe's store settings, user units, checks
#   stores/stores.sh unseal      start the stores and unseal OpenBao (at boot)
#   stores/stores.sh tls-renew   new certificate from the platform CA when due
#   stores/stores.sh status      sealed or not, and LABaPe's store checks
#
# Secret zero (question 105) stays in stores/secrets/ (directory 0700): OpenBao's
# unseal key and root token, Conjur's data key, database password and admin
# key, and the update agent's AppRole login. LABaPe's own logins go straight
# into its settings and are never written here (LABaPe rotates them).
# Nothing secret is printed or put on a command line.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C="$(dirname "$HERE")"                       # container/
S="$HERE/secrets" TLS="$HERE/tls"
MODE="${1:-setup}"
case "$MODE" in setup|unseal|tls-renew|status) ;; *) sed -n '2,17p' "$0" >&2; exit 1 ;; esac

say() { echo "labape-stores: $*"; }
die() { echo "labape-stores: $*" >&2; exit 1; }
envget() { grep -m1 "^$1=" "$C/.env" 2>/dev/null | cut -d= -f2- || true; }
envput() {
  if grep -q "^$1=" "$C/.env"; then sed -i "s|^$1=.*|$1=$2|" "$C/.env"; else echo "$1=$2" >> "$C/.env"; fi
}
sget() { [ -s "$S/$1" ] && cat "$S/$1" || true; }
sput() { (umask 077; printf '%s\n' "$2" > "$S/$1.tmp"); chmod 644 "$S/$1.tmp"; mv "$S/$1.tmp" "$S/$1"; }
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
json() {   # json KEY...: one value from the JSON on stdin (list indexes are numbers)
  python3 -c 'import json, sys
d = json.load(sys.stdin)
for k in sys.argv[1:]:
    d = d[int(k)] if isinstance(d, list) else d[k]
print(d)' "$@"
}

if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(podman compose); fi
dc() { (cd "$C" && COMPOSE_PROFILES=stores "${COMPOSE[@]}" "$@"); }

BPORT="$(envget LABAPE_OPENBAO_PORT)"; BPORT="${BPORT:-8200}"
CPORT="$(envget LABAPE_CONJUR_PORT)"; CPORT="${CPORT:-8444}"
BAO="https://127.0.0.1:$BPORT" CONJUR="https://127.0.0.1:$CPORT"

# --- HTTP, with secrets in headers or bodies that never reach a command line -----------
# bao_api METHOD PATH [TOKEN] < body: the response body; fails on HTTP errors.
bao_api() {
  local tok="${3:-}"
  if [ -n "$tok" ]; then
    curl -fsS --cacert "$TLS/root.crt" -X "$1" -H @<(printf 'X-Vault-Token: %s\n' "$tok") \
      -H 'Content-Type: application/json' --data-binary @- "$BAO/v1/$2"
  else
    curl -fsS --cacert "$TLS/root.crt" -X "$1" -H 'Content-Type: application/json' --data-binary @- "$BAO/v1/$2"
  fi
}
root_api() { bao_api "$1" "$2" "$(sget openbao_root_token)"; }
# conjur_api METHOD PATH TOKEN < body
conjur_api() {
  curl -fsS --cacert "$TLS/root.crt" -X "$1" -H @<(printf 'Authorization: Token token="%s"\n' "$3") \
    --data-binary @- "$CONJUR$2"
}
conjur_login() {   # conjur_login <login> < api key: a base64 access token
  local login; login="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1")"
  tr -d '\r\n' | curl -fsS --cacert "$TLS/root.crt" -H "Accept-Encoding: base64" --data-binary @- \
    "$CONJUR/authn/labape/$login/authenticate"   # the key without its file's newline
}

# --- certificates from the platform CA -----------------------------------------------------
names() {   # the names and addresses clients use: in the stack, on this host, on the LAN
  local lan; lan="$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2 || true)"
  printf '%s\n' labape-openbao labape-conjur-tls localhost 127.0.0.1 "$(hostname -s)" \
    "$(hostname -f 2>/dev/null || hostname)" "$(envget LABAPE_HOSTNAME)" "$lan" | awk 'NF && !seen[$0]++'
}
sign() {
  local san="" n
  while read -r n; do
    if [[ "$n" =~ ^[0-9.]+$|: ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi
  done < <(names)
  install -d -m 0755 "$TLS"
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$TLS/server.key.new" \
    -subj "/CN=labape-openbao" -addext "subjectAltName=${san%,}" -out "$TLS/server.csr" 2>/dev/null
  (cd "$C" && "${COMPOSE[@]}" exec -T labape-api labape ca-sign --with-root) < "$TLS/server.csr" > "$TLS/bundle.pem" \
      2> "$TLS/ca-sign.log" \
    || { grep -v ' INFO ' "$TLS/ca-sign.log" >&2; rm -f "$TLS/server.key.new" "$TLS/bundle.pem" "$TLS/ca-sign.log"
         die "the platform CA didn't sign the request (is labape-api up?)"; }
  rm -f "$TLS/ca-sign.log" "$TLS/server.csr"
  python3 - "$TLS/bundle.pem" "$TLS" <<'PY'
import re, sys
certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", open(sys.argv[1]).read(), re.S)
open(sys.argv[2] + "/server.crt.new", "w").write("\n".join(certs[:-1]) + "\n")
open(sys.argv[2] + "/root.crt", "w").write(certs[-1] + "\n")
PY
  # Readable inside the containers (OpenBao runs as its own user); stores/ (0700) keeps host users out.
  chmod 0644 "$TLS/server.key.new" "$TLS/server.crt.new" "$TLS/root.crt"
  mv "$TLS/server.key.new" "$TLS/server.key"; mv "$TLS/server.crt.new" "$TLS/server.crt"
  rm -f "$TLS/bundle.pem"
}
due() { ! openssl x509 -checkend $((10 * 86400)) -noout -in "$TLS/server.crt" >/dev/null 2>&1; }
reload_tls() {
  dc kill -s HUP labape-openbao >/dev/null 2>&1 || true
  dc exec -T labape-conjur-tls nginx -s reload >/dev/null 2>&1 || true
}

# --- OpenBao ---------------------------------------------------------------------------------
bao_status() {   # sys/seal-status as JSON; empty when OpenBao isn't answering
  curl -sS --cacert "$TLS/root.crt" --max-time 5 "$BAO/v1/sys/seal-status" 2>/dev/null || true
}
unseal() {
  local st="" i
  for i in $(seq 1 60); do st="$(bao_status)"; [ -n "$st" ] && break; sleep 5; done
  [ -n "$st" ] || die "OpenBao isn't answering on $BAO (docker compose logs labape-openbao)"
  if [ "$(echo "$st" | json initialized)" != True ]; then
    [ -z "$(sget openbao_unseal_key)" ] || die "OpenBao looks uninitialized, but stores/secrets has an unseal key; not initializing again"
    say "initializing OpenBao"
    local out; out="$(echo '{"secret_shares": 1, "secret_threshold": 1}' | bao_api PUT sys/init)"
    sput openbao_unseal_key "$(echo "$out" | json keys_base64 0)"
    sput openbao_root_token "$(echo "$out" | json root_token)"
    st="$(bao_status)"
  fi
  if [ "$(echo "$st" | json sealed)" = True ]; then
    python3 -c 'import json, sys; print(json.dumps({"key": open(sys.argv[1]).read().strip()}))' "$S/openbao_unseal_key" \
      | bao_api PUT sys/unseal >/dev/null || die "unsealing OpenBao failed"
    say "OpenBao unsealed"
  fi
}
enable_kv() {   # enable_kv <mount>
  root_api GET sys/mounts < /dev/null | grep -q "\"$1/\"" \
    || echo '{"type": "kv", "options": {"version": "2"}}' | root_api POST "sys/mounts/$1" >/dev/null
}
policy() {   # policy <name> <file>
  python3 -c 'import json, sys; print(json.dumps({"policy": open(sys.argv[1]).read()}))' "$2" \
    | root_api PUT "sys/policies/acl/$1" >/dev/null
}
role() {   # role <name> <policy>
  echo "{\"token_policies\": \"$2\", \"token_ttl\": \"1h\", \"token_max_ttl\": \"4h\", \"secret_id_ttl\": \"0\"}" \
    | root_api POST "auth/approle/role/$1" >/dev/null
}
role_id() { root_api GET "auth/approle/role/$1/role-id" < /dev/null | json data role_id; }
new_secret_id() { echo '{}' | root_api POST "auth/approle/role/$1/secret-id" | json data secret_id; }
approle_ok() {   # approle_ok <role id> <secret id file>: the login works
  python3 -c 'import json, sys; print(json.dumps({"role_id": sys.argv[1], "secret_id": open(sys.argv[2]).read().strip()}))' \
    "$1" "$2" | bao_api POST auth/approle/login >/dev/null 2>&1
}
configure_bao() {
  enable_kv labape
  enable_kv labape-host
  root_api GET sys/auth < /dev/null | grep -q '"approle/"' || echo '{"type": "approle"}' | root_api POST sys/auth/approle >/dev/null
  policy labape "$HERE/openbao/labape.hcl"
  policy labape-agent "$HERE/openbao/labape-agent.hcl"
  role labape labape
  role labape-agent labape-agent
  sput agent_role_id "$(role_id labape-agent)"
  if [ ! -s "$S/agent_secret_id" ] || ! approle_ok "$(sget agent_role_id)" "$S/agent_secret_id"; then
    sput agent_secret_id "$(new_secret_id labape-agent)"
    say "OpenBao: issued the update agent's login (stores/secrets/agent_secret_id)"
  fi
}

# --- Conjur ----------------------------------------------------------------------------------
CONJUR_HOST_KEY=""   # set when the policy load creates host labape/app; never written to disk
configure_conjur() {
  local i out tok
  for i in $(seq 1 60); do
    curl -fsS --cacert "$TLS/root.crt" -o /dev/null "$CONJUR/" 2>/dev/null && break
    [ "$i" = 60 ] && die "Conjur isn't answering on $CONJUR (docker compose logs labape-conjur)"
    sleep 3
  done
  if [ ! -s "$S/conjur_admin_api_key" ]; then
    out="$(dc exec -T labape-conjur sh -c '. /run/stores/env.sh && conjurctl account create labape' 2>&1)" \
      || die "creating the Conjur account failed: $(echo "$out" | grep -iv 'api key' | tail -1)"
    sput conjur_admin_api_key "$(echo "$out" | sed -n 's/.*API key for admin: *//p' | tr -d '\r')"
    say "Conjur account labape created"
  fi
  tok="$(conjur_login admin < "$S/conjur_admin_api_key")" || die "Conjur refused the admin key in stores/secrets"
  # The answer lists the API keys of roles it creates: keep host labape/app's in memory only.
  out="$(conjur_api POST /policies/labape/policy/root "$tok" < "$HERE/conjur/policy.yml")" \
    || die "loading the Conjur policy failed"
  CONJUR_HOST_KEY="$(echo "$out" | python3 -c 'import json, sys
roles = json.load(sys.stdin).get("created_roles", {})
print(next((r["api_key"] for k, r in roles.items() if k.endswith(":host:labape/app")), ""))')"
}
conjur_rotate_app_key() {   # a new API key for host labape/app (the old one stops working)
  local tok; tok="$(conjur_login admin < "$S/conjur_admin_api_key")"
  conjur_api PUT "/authn/labape/api_key?role=host:labape/app" "$tok" < /dev/null
}

# --- LABaPe's settings -----------------------------------------------------------------------
labape() { (cd "$C" && "${COMPOSE[@]}" exec -T labape-api labape "$@"); }
settings_json() {   # the store settings LABaPe should have, plus any logins given as files
  python3 - "$TLS/root.crt" "$(role_id labape)" "${1:-}" "${2:-}" <<'PY'
import json, sys
root = open(sys.argv[1]).read()
creds = {}
for name, path in (("openbao", sys.argv[3]), ("conjur", sys.argv[4])):
    if path:
        creds[name] = open(path).read().strip()
print(json.dumps({
    "stores": {
        "openbao": {"url": "https://labape-openbao:8200", "mount": "labape", "path_prefix": "",
                    "auth_mount": "approle", "role_name": "labape", "role_id": sys.argv[2],
                    "verify_tls": True, "ca_bundle": root},
        "conjur": {"url": "https://labape-conjur-tls:8443", "account": "labape", "login": "host/labape/app",
                   "authenticator": "authn", "branch": "labape/secrets", "verify_tls": True, "ca_bundle": root},
    },
    "credentials": creds,
}))
PY
}
configure_labape() {
  local needs tmp bao_file="" conjur_file=""
  needs="$(settings_json | labape stores-configure --needs 2>/dev/null | tail -1)" \
    || die "labape stores-configure failed (is labape-api up?)"
  tmp="$(mktemp -d)"; chmod 700 "$tmp"
  trap 'rm -rf "$tmp"' RETURN
  if [ "$(echo "$needs" | json openbao)" = True ]; then
    new_secret_id labape > "$tmp/openbao"; bao_file="$tmp/openbao"
  fi
  if [ "$(echo "$needs" | json conjur)" = True ]; then
    if [ -n "$CONJUR_HOST_KEY" ]; then printf '%s' "$CONJUR_HOST_KEY" > "$tmp/conjur"
    else conjur_rotate_app_key > "$tmp/conjur"; fi
    conjur_file="$tmp/conjur"
  fi
  settings_json "$bao_file" "$conjur_file" | labape stores-configure 2>/dev/null | grep '^labape:' || true
}

# --- checks ------------------------------------------------------------------------------------
smoke() {
  local tok value
  tok="$(python3 -c 'import json, sys; print(json.dumps({"role_id": open(sys.argv[1]).read().strip(), "secret_id": open(sys.argv[2]).read().strip()}))' \
      "$S/agent_role_id" "$S/agent_secret_id" | bao_api POST auth/approle/login | json auth client_token)" \
    || die "the update agent's OpenBao login doesn't work"
  value="smoke-$(date +%s)"
  echo "{\"data\": {\"value\": \"$value\"}}" | bao_api POST labape-host/data/smoke "$tok" >/dev/null
  [ "$(bao_api GET labape-host/data/smoke "$tok" < /dev/null | json data data value)" = "$value" ] \
    || die "OpenBao: the agent's value didn't read back"
  say "OpenBao: the update agent signed in, wrote and read back under labape-host/"
  labape stores-configure --check < /dev/null 2>/dev/null | grep '^labape:' || true
}

# --- user units: unseal at start, renew the certificate --------------------------------------
install_units() {
  command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1 \
    || { say "no systemd user session: run 'stores/stores.sh unseal' after each boot"; return 0; }
  local u="$HOME/.config/systemd/user"
  mkdir -p "$u"
  cat > "$u/labape-stores.service" <<UNIT
[Unit]
Description=LABaPe: start the secret stores and unseal OpenBao

[Service]
Type=oneshot
ExecStart=$HERE/stores.sh unseal

[Install]
WantedBy=default.target
UNIT
  cat > "$u/labape-stores-tls.service" <<UNIT
[Unit]
Description=LABaPe: renew the secret stores' certificate when due

[Service]
Type=oneshot
ExecStart=$HERE/stores.sh tls-renew
UNIT
  cat > "$u/labape-stores-tls.timer" <<UNIT
[Unit]
Description=LABaPe: daily check of the secret stores' certificate

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable labape-stores.service >/dev/null 2>&1
  systemctl --user enable --now labape-stores-tls.timer >/dev/null 2>&1
}

# --- modes -------------------------------------------------------------------------------------
case "$MODE" in
  unseal) dc up -d labape-openbao labape-conjur-db labape-conjur labape-conjur-tls >/dev/null 2>&1 || true
          unseal; exit 0 ;;
  tls-renew) due || exit 0; sign; reload_tls; say "certificate renewed"; exit 0 ;;
  status)
    st="$(bao_status)"; say "OpenBao: ${st:-not answering}"
    labape stores-configure --check < /dev/null 2>/dev/null | grep '^labape:' || true; exit 0 ;;
esac

# setup
for tool in curl openssl python3; do command -v "$tool" >/dev/null 2>&1 || die "$tool is missing"; done
[ -f "$C/.env" ] || die "no container/.env (run container/setup.sh first)"
chmod 700 "$HERE"
install -d -m 0700 "$S"
[ -s "$S/conjur_db_password" ] || sput conjur_db_password "$(gen)"
[ -s "$S/conjur_data_key" ] || sput conjur_data_key "$(openssl rand -base64 32)"
if [ ! -s "$TLS/server.crt" ] || due; then sign; say "certificate from the platform CA"; fi
envput COMPOSE_PROFILES stores
say "starting the stores"
for try in 1 2 3; do   # a registry timeout shouldn't end the run
  dc pull -q labape-openbao labape-conjur-db labape-conjur labape-conjur-tls >/dev/null 2>&1 && break
  [ "$try" = 3 ] && die "pulling the images failed three times"
  say "pulling the images failed; trying again in 15 seconds"; sleep 15
done
dc up -d labape-openbao labape-conjur-db labape-conjur labape-conjur-tls >/dev/null
reload_tls
unseal
configure_bao
configure_conjur
configure_labape
install_units
smoke
say "ready: OpenBao $BAO and Conjur $CONJUR (on the host), labape-openbao:8200 and labape-conjur-tls:8443 in the stack"
