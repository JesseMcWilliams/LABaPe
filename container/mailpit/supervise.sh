#!/bin/sh
# Runs the stack's Mailpit with the settings LABaPe writes into /config, and restarts it when they change
# (Claude_Docs/Planning_Web-Interface-Design.md §52). Mailpit reads its relay options only at start, and
# LABaPe has no way to restart containers, so this loop does it: Admin -> Mail saves the settings, LABaPe
# rewrites /config/mailpit.env and /config/relay.yaml, and within a few seconds Mailpit runs with them.
# Stored messages live in /data and survive the restart.
#
# /config/mailpit.env holds KEY=VALUE lines; only the keys below are taken, as plain text (never run).
# After a start that lasted, the revision (LABAPE_REV) is written to /config/applied so LABaPe can tell.
CONF="${LABAPE_MAILPIT_CONF:-/config}"          # the overrides are for the tests
MAILPIT="${LABAPE_MAILPIT_BIN:-/mailpit}"
ENVF="$CONF/mailpit.env"
KEYS="MP_SMTP_RELAY_CONFIG MP_SMTP_RELAY_ALL MP_SMTP_RELAY_MATCHING MP_MAX_MESSAGES MP_MAX_AGE"
pid=""

stop() {
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  exit 0
}
trap stop TERM INT

signature() { cksum "$ENVF" "$CONF/relay.yaml" 2>/dev/null; }

while true; do
  for k in $KEYS; do unset "$k"; done
  rev=""
  if [ -f "$ENVF" ]; then
    while IFS= read -r line; do
      key="${line%%=*}"
      case " $KEYS " in
        *" $key "*) export "$line" ;;
        *) [ "$key" = LABAPE_REV ] && rev="${line#LABAPE_REV=}" ;;
      esac
    done < "$ENVF"
  fi
  seen="$(signature)"
  "$MAILPIT" &
  pid=$!
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    [ -n "$rev" ] && printf '%s\n' "$rev" > "$CONF/applied"
    while kill -0 "$pid" 2>/dev/null; do
      sleep 3
      if [ "$(signature)" != "$seen" ]; then
        echo "labape-mailpit: the settings changed; restarting"
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        break
      fi
    done
  fi
  wait "$pid" 2>/dev/null
  pid=""
  sleep 1
done
