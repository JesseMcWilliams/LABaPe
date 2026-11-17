#!/bin/bash
# Starts the API and the worker as the host's service user instead of root (plan: Planning_Root-Image.md).
#
# The container starts as root only so this script can prepare what a non-root process needs, then drops to
# LABAPE_UID:LABAPE_GID (and the extra groups in LABAPE_EXTRA_GIDS, the host's kvm and libvirt gids) with
# setpriv and runs the command. Without LABAPE_UID (an install that predates it, or rootless Podman, where
# root in the container already is the service user) it runs the command as it is.
#
#   LABAPE_UID, LABAPE_GID   the service user's ids (container/.env, written by setup.sh and the installer)
#   LABAPE_EXTRA_GIDS        comma-separated supplementary gids (blank entries ignored)
#   LABAPE_OWN_DIRS          space-separated folders this container writes; each is made the service user's once
set -eu

uid="${LABAPE_UID:-}"
if [ "$(id -u)" != 0 ] || [ -z "$uid" ] || [ "$uid" = 0 ]; then
  exec "$@"
fi
gid="${LABAPE_GID:-$uid}"
home=/var/lib/labape/home

# A passwd and group entry for the ids: ssh, git and Ansible refuse a uid they can't name.
getent group "$gid" >/dev/null || echo "labape:x:$gid:" >> /etc/group
getent passwd "$uid" >/dev/null || echo "labape:x:$uid:$gid::$home:/bin/bash" >> /etc/passwd
name="$(getent passwd "$uid" | cut -d: -f1)"

# The folders this container writes belong to the service user. Once per folder: the marker records who owns it.
# A read-only mount, or a folder that isn't mounted here, is skipped.
for d in ${LABAPE_OWN_DIRS:-/var/lib/labape /var/lib/labape-transcripts /var/lib/labape-recordings /var/lib/labape-mailpit /var/lib/labape-syslog/conf /var/lib/labape-syslog/tls}; do
  [ -d "$d" ] || continue
  marker="$d/.labape-owner"
  [ "$(cat "$marker" 2>/dev/null)" = "$uid:$gid" ] && continue
  if chown -R -h "$uid:$gid" "$d" 2>/dev/null && { echo "$uid:$gid" > "$marker" && chown "$uid:$gid" "$marker"; } 2>/dev/null; then
    echo "labape: $d now belongs to $uid:$gid" >&2
  fi
done
mkdir -p "$home" && chown "$uid:$gid" "$home" 2>/dev/null || true

groups="$gid"
IFS=,
for g in ${LABAPE_EXTRA_GIDS:-}; do
  case "$g" in ''|*[!0-9]*) ;; *) groups="$groups,$g" ;; esac
done
unset IFS

export HOME="$home" USER="$name" LOGNAME="$name"
exec setpriv --reuid "$uid" --regid "$gid" --groups "$groups" "$@"
