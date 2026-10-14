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
# Which store holds LABaPe's own secrets (question 104): LABAPE_PLATFORM_STORE in .env.
PLATFORM="$(envget LABAPE_PLATFORM_STORE)"; PLATFORM="${PLATFORM:-openbao}"
case "$PLATFORM" in openbao|conjur) ;; *) die "LABAPE_PLATFORM_STORE must be openbao or conjur, not $PLATFORM" ;; esac
# Conjur is optional (question 140; LABAPE_CONJUR=no in .env); OpenBao always runs.
CONJUR_ON="$(envget LABAPE_CONJUR)"; CONJUR_ON="${CONJUR_ON:-yes}"
if [ "$CONJUR_ON" = no ]; then
  [ "$PLATFORM" = openbao ] || die "LABAPE_PLATFORM_STORE=conjur needs Conjur (LABAPE_CONJUR=no in .env)"
  STORE_SERVICES=(labape-openbao)
else
  STORE_SERVICES=(labape-openbao labape-conjur-db labape-conjur labape-conjur-tls)
fi
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
  # Raft takes a moment after the unseal to make this node active; until then writes fail with
  # "cannot write to readonly storage" (HTTP 500). sys/health is 200 only on the active node.
  for i in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' --cacert "$TLS/root.crt" --max-time 5 "$BAO/v1/sys/health")" = 200 ] && return 0
    sleep 2
  done
  die "OpenBao didn't become active within 2 minutes of the unseal (docker compose logs labape-openbao)"
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
CONJUR_HOST_KEY="" CONJUR_CLI_KEY=""   # set when the policy load creates those hosts; never written to disk
wait_conjur() {   # Conjur takes a minute or more after a start; nginx answers 502 until then
  local i
  for i in $(seq 1 100); do
    curl -fsS --cacert "$TLS/root.crt" -o /dev/null "$CONJUR/" 2>/dev/null && return 0
    sleep 3
  done
  die "Conjur isn't answering on $CONJUR (docker compose logs labape-conjur)"
}
configure_conjur() {
  local out tok
  wait_conjur
  if [ ! -s "$S/conjur_admin_api_key" ]; then
    out="$(dc exec -T labape-conjur sh -c '. /run/stores/env.sh && conjurctl account create labape' 2>&1)" \
      || die "creating the Conjur account failed: $(echo "$out" | grep -iv 'api key' | tail -1)"
    sput conjur_admin_api_key "$(echo "$out" | sed -n 's/.*API key for admin: *//p' | tr -d '\r')"
    say "Conjur account labape created"
  fi
  tok="$(conjur_login admin < "$S/conjur_admin_api_key")" || die "Conjur refused the admin key in stores/secrets"
  # The answer lists the API keys of the hosts it creates: the app's and the command line's stay
  # in memory (they go to LABaPe's settings and store.env); the agent's is secret zero.
  out="$(conjur_api POST /policies/labape/policy/root "$tok" < "$HERE/conjur/policy.yml")" \
    || die "loading the Conjur policy failed"
  created() { echo "$out" | python3 -c 'import json, sys
roles = json.load(sys.stdin).get("created_roles", {})
print(next((r["api_key"] for k, r in roles.items() if k.endswith(":host:labape/" + sys.argv[1])), ""))' "$1"; }
  CONJUR_HOST_KEY="$(created app)"; CONJUR_CLI_KEY="$(created cli)"
  i="$(created agent)"
  [ -z "$i" ] || sput agent_conjur_api_key "$i"
  if [ ! -s "$S/agent_conjur_api_key" ] || ! conjur_login host/labape/agent < "$S/agent_conjur_api_key" >/dev/null 2>&1; then
    sput agent_conjur_api_key "$(conjur_rotate_key agent)"
    say "Conjur: issued the update agent's key (stores/secrets/agent_conjur_api_key)"
  fi
}
conjur_rotate_key() {   # conjur_rotate_key <host>: a new API key for host labape/<host> (the old one stops working)
  local tok; tok="$(conjur_login admin < "$S/conjur_admin_api_key")"
  conjur_api PUT "/authn/labape/api_key?role=host:labape/$1" "$tok" < /dev/null
}
conjur_rotate_app_key() { conjur_rotate_key app; }

# --- LABaPe's settings -----------------------------------------------------------------------
labape() { (cd "$C" && "${COMPOSE[@]}" exec -T labape-api labape "$@"); }
settings_json() {   # the store settings LABaPe should have, plus any logins given as files
  python3 - "$TLS/root.crt" "$(role_id labape)" "${1:-}" "${2:-}" "$CONJUR_ON" <<'PY'
import json, sys
root = open(sys.argv[1]).read()
creds = {}
for name, path in (("openbao", sys.argv[3]), ("conjur", sys.argv[4])):
    if path:
        creds[name] = open(path).read().strip()
stores = {"openbao": {"url": "https://labape-openbao:8200", "mount": "labape", "path_prefix": "",
                      "auth_mount": "approle", "role_name": "labape", "role_id": sys.argv[2],
                      "verify_tls": True, "ca_bundle": root}}
if sys.argv[5] != "no":
    stores["conjur"] = {"url": "https://labape-conjur-tls:8443", "account": "labape", "login": "host/labape/app",
                        "authenticator": "authn", "branch": "labape/secrets", "verify_tls": True, "ca_bundle": root}
print(json.dumps({"stores": stores, "credentials": creds}))
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
  if [ "$CONJUR_ON" != no ] && [ "$(echo "$needs" | json conjur)" = True ]; then
    if [ -n "$CONJUR_HOST_KEY" ]; then printf '%s' "$CONJUR_HOST_KEY" > "$tmp/conjur"
    else conjur_rotate_app_key > "$tmp/conjur"; fi
    conjur_file="$tmp/conjur"
  fi
  settings_json "$bao_file" "$conjur_file" | labape stores-configure 2>/dev/null | grep '^labape:' || true
}

# --- the engine secrets and the command line (phases S2, S3) ----------------------------------
STORE_ENV="$HOME/.config/labape/store.env"
REPO="$(dirname "$C")"
# The command line's own vault: the one the update agent was told about, else this checkout's.
CLI_VAULT="$(grep -m1 '^LABAPE_CLI_VAULT=' "$HOME/.config/labape/agent.env" 2>/dev/null | cut -d= -f2- || true)"
CLI_VAULT="${CLI_VAULT:-$REPO/secrets.vault.yml}"
CLI_PASS="$HOME/.labape-vault-pass"
configure_cli() {   # the command line's read-only login to the platform store, in ~/.config/labape/store.env (0600)
  local sid="" key="" old_kind=""
  [ -f "$STORE_ENV" ] && old_kind="$(grep -m1 '^LABAPE_STORE_KIND=' "$STORE_ENV" | cut -d= -f2- || true)"
  if [ "$PLATFORM" = conjur ]; then
    [ "${old_kind:-openbao}" = conjur ] && key="$(grep -m1 '^LABAPE_STORE_API_KEY=' "$STORE_ENV" | cut -d= -f2- || true)"
    if [ -z "$key" ] || ! printf '%s' "$key" | conjur_login host/labape/cli >/dev/null 2>&1; then
      key="${CONJUR_CLI_KEY:-$(conjur_rotate_key cli)}"
      say "Conjur: issued the command line's key ($STORE_ENV)"
    fi
    (umask 077; mkdir -p "$(dirname "$STORE_ENV")"
     printf '%s\n' "# The command line's login to the platform store (container/stores/stores.sh; design §26)" \
       "LABAPE_STORE_KIND=conjur" "LABAPE_STORE_URL=$CONJUR" "LABAPE_STORE_CA=$TLS/root.crt" \
       "LABAPE_STORE_API_KEY=$key" > "$STORE_ENV.tmp")
  else
    policy labape-cli "$HERE/openbao/labape-cli.hcl"
    role labape-cli labape-cli
    [ "${old_kind:-openbao}" = openbao ] && [ -f "$STORE_ENV" ] \
      && sid="$(grep -m1 '^LABAPE_STORE_SECRET_ID=' "$STORE_ENV" | cut -d= -f2- || true)"
    if [ -z "$sid" ] || ! python3 -c 'import json, sys; print(json.dumps({"role_id": sys.argv[1], "secret_id": sys.argv[2]}))' \
         "$(role_id labape-cli)" "$sid" | bao_api POST auth/approle/login >/dev/null 2>&1; then
      sid="$(new_secret_id labape-cli)"
      say "OpenBao: issued the command line's login ($STORE_ENV)"
    fi
    (umask 077; mkdir -p "$(dirname "$STORE_ENV")"
     printf '%s\n' "# The command line's login to the platform store (container/stores/stores.sh; design §26)" \
       "LABAPE_STORE_KIND=openbao" "LABAPE_STORE_URL=$BAO" "LABAPE_STORE_CA=$TLS/root.crt" \
       "LABAPE_STORE_ROLE_ID=$(role_id labape-cli)" "LABAPE_STORE_SECRET_ID=$sid" > "$STORE_ENV.tmp")
  fi
  chmod 600 "$STORE_ENV.tmp"; mv "$STORE_ENV.tmp" "$STORE_ENV"
}
ES_DIR() { local es; es="$(envget LABAPE_ENGINE_SECRETS_PATH)"; es="${es:-./engine-secrets}"
           case "$es" in /*) echo "$es" ;; *) echo "$C/${es#./}" ;; esac; }
ENGINE_NAMES="vault_password vault_password_history vault ssh_key ssh_key_pub ssh_history"
conjur_cli_permits() {   # the command line may read the engine secrets (the variables exist now)
  local tok body="- !permit
  role: !host /labape/cli
  privileges: [read, execute]
  resources:"
  for n in $ENGINE_NAMES; do body+="
    - !variable platform/engine/$n"; done
  tok="$(conjur_login admin < "$S/conjur_admin_api_key")"
  printf '%s\n' "$body" | conjur_api POST "/policies/labape/policy/labape%2Fsecrets" "$tok" >/dev/null \
    || die "granting the command line read access in Conjur failed"
}
import_engine() {   # the vault, its password and the VM key into the platform store (moved there if it changed)
  labape engine-secrets import --store "$PLATFORM" 2>/dev/null | grep '^labape:' | tail -1 \
    || die "importing or moving the engine secrets failed (labape engine-secrets import --store $PLATFORM)"
  [ "$PLATFORM" != conjur ] || conjur_cli_permits
  labape engine-secrets check 2>/dev/null | grep '^labape:' || die "the engine secrets in the store don't check out"
}
check_cli() {   # the command line reads them; its own vault opens with the store's password (re-encrypted if not)
  python3 "$REPO/scripts/lib/store_get.py" check || die "the command line's store login doesn't work"
  [ -f "$CLI_VAULT" ] || { say "no command-line vault at $CLI_VAULT; skipped"; return 0; }
  local d; d="$(mktemp -d)"
  python3 "$REPO/scripts/lib/store_get.py" vault-pass --vault "$CLI_VAULT" --out "$d/pw" --try-file "$CLI_PASS" \
    || { rm -rf "$d"; die "the command line's vault ($CLI_VAULT) doesn't open with the store's password"; }
  rm -rf "$d"
  say "the command line reads its vault password and VM key from the store; $CLI_VAULT opens with it"
}
# --- the bootstrap secrets (phase S4) --------------------------------------------------------
# container/secrets/ moves into the platform store (only the agent's login reads it). The
# stack then reads a memory-only copy in LABAPE_SECRETS_DIR, which `unseal` refills at boot.
hs() { python3 "$HERE/hostsecrets.py" "$@"; }
runtime_secrets_dir() {
  local d="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  [ -d "$d" ] && [ -w "$d" ] || die "no per-user runtime directory ($d): is lingering on for $(id -un)?"
  echo "$d/labape-secrets"
}
healthy_within() {   # healthy_within <seconds>: labape health --deep passes
  local deadline=$((SECONDS + $1))
  while [ $SECONDS -lt $deadline ]; do
    labape health --deep >/dev/null 2>&1 && return 0
    sleep 10
  done
  return 1
}
move_bootstrap() {
  local dir running cur tmp
  dir="$(envget LABAPE_SECRETS_DIR)"
  if [ -n "$dir" ]; then
    cur="$(hs active)"
    if [ "$cur" = "$PLATFORM" ]; then
      hs same "$dir" || die "the stack's secrets differ from $cur's copy; not changing anything"
      sput platform_store_active "$cur"
      say "the bootstrap secrets are in $cur; the stack reads them from $dir"
      return 0
    fi
    # The platform store changed (question 104): copy them over, check, then switch. The
    # running stack keeps its files; a boot before the switch still reads the old store.
    hs --store "$cur" same "$dir" || die "the stack's secrets differ from $cur's copy; not moving them"
    hs --store "$PLATFORM" push "$dir"
    tmp="$(mktemp -d)"; hs --store "$PLATFORM" pull "$tmp/s" >/dev/null
    hs --store "$PLATFORM" same "$dir" || { rm -rf "$tmp"; die "$PLATFORM's copy differs; still on $cur"; }
    rm -rf "$tmp"
    sput platform_store_active "$PLATFORM"
    hs --store "$cur" drop || say "the old copy in $cur couldn't be deleted; delete it by hand"
    say "the bootstrap secrets moved from $cur to $PLATFORM"
    return 0
  fi
  running="$(labape maintenance status 2>/dev/null | tail -1 | python3 -c 'import json, sys; print(json.load(sys.stdin)["running_jobs"])')"
  [ "$running" = 0 ] || die "$running job(s) running; moving the bootstrap secrets restarts the API and worker: try again later"
  dir="$(runtime_secrets_dir)"
  hs --store "$PLATFORM" push "$C/secrets"
  hs --store "$PLATFORM" pull "$dir"
  hs --store "$PLATFORM" same "$C/secrets" || die "$PLATFORM's copy differs from container/secrets/; nothing switched"
  sput platform_store_active "$PLATFORM"
  envput LABAPE_SECRETS_DIR "$dir"
  say "restarting the stack on the copy from $PLATFORM ($dir)"
  (cd "$C" && "${COMPOSE[@]}" up -d --force-recreate postgres labape-ca labape-api labape-worker >/dev/null 2>&1) || true
  if ! healthy_within 240; then
    sed -i '/^LABAPE_SECRETS_DIR=/d' "$C/.env"
    (cd "$C" && "${COMPOSE[@]}" up -d --force-recreate postgres labape-ca labape-api labape-worker >/dev/null 2>&1) || true
    die "the stack wasn't healthy on the copy from $PLATFORM; back on container/secrets/"
  fi
  say "the bootstrap secrets are in $PLATFORM; container/secrets/ is now an old copy (back it up and delete it)"
}
start_stack() {   # at boot: the memory-only copy from the platform store, then everything up
  local dir store; dir="$(envget LABAPE_SECRETS_DIR)"
  [ -n "$dir" ] || return 0
  store="$(hs active)"
  [ "$store" != conjur ] || wait_conjur
  hs pull "$dir"
  (cd "$C" && "${COMPOSE[@]}" up -d >/dev/null 2>&1) || die "starting the stack failed (docker compose up -d in $C)"
  say "the stack started with its secrets from $store"
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
  unseal) dc up -d "${STORE_SERVICES[@]}" >/dev/null 2>&1 || true
          unseal; start_stack; exit 0 ;;
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
  dc pull -q "${STORE_SERVICES[@]}" >/dev/null 2>&1 && break
  [ "$try" = 3 ] && die "pulling the images failed three times"
  say "pulling the images failed; trying again in 15 seconds"; sleep 15
done
dc up -d "${STORE_SERVICES[@]}" >/dev/null
reload_tls
unseal
configure_bao
[ "$CONJUR_ON" = no ] || configure_conjur
configure_labape
install_units
smoke
configure_cli
import_engine
check_cli
move_bootstrap
if [ "$CONJUR_ON" = no ]; then
  say "ready: OpenBao $BAO (on the host), labape-openbao:8200 in the stack (Conjur not installed)"
else
  say "ready: OpenBao $BAO and Conjur $CONJUR (on the host), labape-openbao:8200 and labape-conjur-tls:8443 in the stack"
fi
