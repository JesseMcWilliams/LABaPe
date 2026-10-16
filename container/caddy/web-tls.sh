#!/usr/bin/env bash
# The web interface's certificate with every name of the stack (N1, question 158; design §48.4):
# one certificate from the platform CA whose SANs are LABAPE_HOSTNAME and LABAPE_ALT_NAMES (the host's
# short name, FQDN and LAN address, found once and kept in container/.env), served by Caddy on 443 and
# on Authentik's 9445 whatever name a browser uses (Caddyfile.names).
#
# Usage (as the stack's service user, from anywhere):
#   container/caddy/web-tls.sh setup     find the names, get the certificate, switch Caddy to it,
#                                        install the daily renewal timer (labape-web-tls.timer)
#   container/caddy/web-tls.sh renew     a new certificate when due (10 days left) or the names changed
#   container/caddy/web-tls.sh status    the names and the certificate's end date
# Needs the stack's labape-api (it signs with the platform CA: `labape ca-sign`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C="$(dirname "$HERE")"                       # container/
TLS="$HERE/tls"
MODE="${1:-}"
case "$MODE" in setup|renew|status) ;; *) sed -n '2,13p' "$0" >&2; exit 1 ;; esac
say() { echo "labape-web-tls: $*"; }
die() { echo "labape-web-tls: $*" >&2; exit 1; }
envget() { grep -m1 "^$1=" "$C/.env" 2>/dev/null | cut -d= -f2- || true; }
envset() {   # envset KEY VALUE: replace or add the line in container/.env
  if grep -q "^$1=" "$C/.env"; then sed -i "s|^$1=.*|$1=$2|" "$C/.env"; else echo "$1=$2" >> "$C/.env"; fi
}
if command -v docker >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(podman compose); fi
dc() { (cd "$C" && "${COMPOSE[@]}" "$@"); }

detect() {   # the host's own names and LAN address
  local lan; lan="$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2 || true)"
  printf '%s\n' "$(hostname -s)" "$(hostname -f 2>/dev/null || hostname)" "$lan"
}
names() {    # the main name first, then the others, each once
  local main alt; main="$(envget LABAPE_HOSTNAME)"; alt="$(envget LABAPE_ALT_NAMES)"
  [ -n "$main" ] || die "LABAPE_HOSTNAME isn't set in $C/.env"
  { echo "$main"; tr ',' '\n' <<<"$alt"; } | tr -d ' ' | awk 'NF && !seen[tolower($0)]++'
}
san_of() { local san="" n; while read -r n; do if [[ "$n" =~ ^[0-9.]+$|: ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi; done; echo "${san%,}"; }

sign() {
  local main; main="$(envget LABAPE_HOSTNAME)"
  install -d -m 0755 "$TLS"
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$TLS/web.key.new" \
    -subj "/CN=$main" -addext "subjectAltName=$(names | san_of)" -out "$TLS/web.csr" 2>/dev/null
  dc exec -T labape-api labape ca-sign < "$TLS/web.csr" > "$TLS/web.crt.new" 2> "$TLS/ca-sign.log" \
    || { grep -v ' INFO ' "$TLS/ca-sign.log" >&2; rm -f "$TLS/web.key.new" "$TLS/web.crt.new" "$TLS/ca-sign.log" "$TLS/web.csr"
         die "the platform CA didn't sign the request (is labape-api up?)"; }
  rm -f "$TLS/ca-sign.log" "$TLS/web.csr"
  grep -q "BEGIN CERTIFICATE" "$TLS/web.crt.new" || die "the platform CA gave no certificate"
  # Caddy reads them in its container as root; tls/ itself is the service user's.
  chmod 0644 "$TLS/web.crt.new"; chmod 0640 "$TLS/web.key.new"
  mv "$TLS/web.key.new" "$TLS/web.key"; mv "$TLS/web.crt.new" "$TLS/web.crt"
  names | paste -sd, - > "$TLS/names"
}
due() {
  [ -s "$TLS/web.crt" ] || return 0
  [ "$(names | paste -sd, -)" = "$(cat "$TLS/names" 2>/dev/null)" ] || return 0      # the names changed
  ! openssl x509 -checkend $((10 * 86400)) -noout -in "$TLS/web.crt" >/dev/null 2>&1
}
# Authentik's LABaPe provider must accept a sign-in coming back at any of the names (design §58).
redirects() {
  local ak="$C/../tools/authentik-test"
  [ -f "$ak/.env" ] && [ -f "$ak/sync-redirects.sh" ] || return 0
  bash "$ak/sync-redirects.sh" "$(envget LABAPE_HOSTNAME)" "$(envget LABAPE_ALT_NAMES)" || say "couldn't update Authentik's redirect URIs (run tools/authentik-test/sync-redirects.sh)"
}
reload() { dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force >/dev/null 2>&1 || dc restart caddy >/dev/null; }

timer() {
  local u="$HOME/.config/systemd/user"
  mkdir -p "$u"
  cat > "$u/labape-web-tls.service" <<UNIT
[Unit]
Description=LABaPe: renew the web interface's certificate when due

[Service]
Type=oneshot
ExecStart=$HERE/web-tls.sh renew
UNIT
  cat > "$u/labape-web-tls.timer" <<UNIT
[Unit]
Description=LABaPe: daily check of the web interface's certificate

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now labape-web-tls.timer >/dev/null 2>&1 || say "couldn't start the renewal timer (no user systemd session?)"
}

case "$MODE" in
  setup)
    if [ -z "$(envget LABAPE_ALT_NAMES)" ]; then
      main="$(envget LABAPE_HOSTNAME)"
      envset LABAPE_ALT_NAMES "$(detect | awk -v m="$main" 'NF && tolower($0) != tolower(m) && !seen[tolower($0)]++' | paste -sd, -)"
    fi
    sign
    envset LABAPE_CADDYFILE Caddyfile.names
    dc up -d caddy >/dev/null 2>&1 || die "Caddy didn't start with the new certificate (docker compose logs caddy)"
    timer
    redirects
    say "serving $(names | paste -sd' ' -) with one certificate until $(openssl x509 -enddate -noout -in "$TLS/web.crt" | cut -d= -f2)" ;;
  renew)
    due || exit 0
    sign; reload; redirects; say "certificate renewed for $(names | paste -sd' ' -)" ;;
  status)
    say "names: $(names | paste -sd' ' -)"
    if [ -s "$TLS/web.crt" ]; then
      say "certificate: $(openssl x509 -noout -ext subjectAltName -in "$TLS/web.crt" 2>/dev/null | tail -1 | tr -d ' ')"
      say "valid until $(openssl x509 -enddate -noout -in "$TLS/web.crt" | cut -d= -f2)$(due && echo ' (due for renewal)')"
    else
      say "no certificate yet (run setup)"
    fi
    say "Caddyfile: $(envget LABAPE_CADDYFILE)" ;;
esac
