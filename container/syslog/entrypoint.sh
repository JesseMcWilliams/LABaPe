#!/bin/bash
# Run rsyslogd and restart it when LABaPe changes its settings or certificates
# (rsyslog doesn't re-read its configuration on HUP). A configuration that
# doesn't validate is reported and the running one is kept.
set -uo pipefail

CONF=/etc/rsyslog-labape
TLS=/tls
RUN=/run/labape-syslog
mkdir -p "$RUN"

say() { echo "labape-syslog: $*"; }

prepare() {
  # TLS 6514 only once LABaPe has issued the certificates; client certificates
  # required unless Settings turned that off (question 122).
  if [ -s "$TLS/server.crt" ] && [ -s "$TLS/server.key" ] && [ -s "$TLS/root.crt" ]; then
    mode=x509/certvalid
    [ -f "$CONF/client-certs-optional" ] && mode=anon
    cat > "$RUN/tls-input.conf" <<EOF
input(type="imtcp" port="6514" ruleset="labape"
      StreamDriver.Name="ossl" StreamDriver.Mode="1" StreamDriver.AuthMode="$mode"
      StreamDriver.CAFile="$TLS/root.crt" StreamDriver.CertFile="$TLS/server.crt" StreamDriver.KeyFile="$TLS/server.key")
EOF
  else
    : > "$RUN/tls-input.conf"
  fi
  # Forwarding over TLS trusts the system roots and the platform root.
  cat /etc/ssl/certs/ca-certificates.crt "$TLS/root.crt" > "$RUN/ca-bundle.pem" 2>/dev/null || true
}

state() { cat "$CONF"/* "$TLS"/*.crt 2>/dev/null | md5sum | cut -d' ' -f1; }

start() {
  prepare
  if ! rsyslogd -N1 -f /etc/rsyslog.conf >"$RUN/check.log" 2>&1; then
    say "configuration rejected:"; cat "$RUN/check.log"
    return 1
  fi
  rm -f /run/rsyslogd.pid
  rsyslogd -n -iNONE -f /etc/rsyslog.conf &
  PID=$!
  say "rsyslogd running (pid $PID)$( [ -s "$RUN/tls-input.conf" ] && echo ', TLS on 6514' || echo ', TLS waits for certificates')"
}

trap 'kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; exit 0' TERM INT

PID=""
until start; do
  # The rendered part is wrong: run without it until LABaPe writes a good one.
  mv "$CONF/forward.conf" "$RUN/forward.conf.rejected" 2>/dev/null || sleep 10
done
last=$(state)
while true; do
  sleep 10
  if ! kill -0 "$PID" 2>/dev/null; then
    say "rsyslogd exited; restarting"; start || sleep 10; last=$(state); continue
  fi
  now=$(state)
  if [ "$now" != "$last" ]; then
    prepare
    if rsyslogd -N1 -f /etc/rsyslog.conf >"$RUN/check.log" 2>&1; then
      say "settings or certificates changed; restarting rsyslogd"
      kill "$PID"; wait "$PID" 2>/dev/null
      start
    else
      say "new configuration rejected; keeping the running one:"; cat "$RUN/check.log"
    fi
    last=$now
  fi
done
