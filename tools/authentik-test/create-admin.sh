#!/usr/bin/env bash
# create-admin.sh USER EMAIL: your own administrator login in the stack's Authentik
#
# Creates (or updates) the user, sets its password, makes it a superuser (group "authentik Admins") and a
# LABaPe administrator (group "labape-admins"), so people sign in with their own account and akadmin stays
# for LABaPe's own use (it checks and rotates akadmin's password; Secrets -> Authentik administrator).
#
# The password is read from standard input, or asked for (twice, hidden) when run in a terminal. It is never
# put on a command line. Run it as the stack's service user, from anywhere:
#   sudo -u labape /opt/LABaPe/tools/authentik-test/create-admin.sh jesse jesse@example.com
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ -z "${COMPOSE:-}" ]; then
  if command -v docker >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="podman compose"; fi
fi
USER_NAME="${1:?usage: create-admin.sh USER EMAIL}"
EMAIL="${2:?usage: create-admin.sh USER EMAIL}"
[[ "$USER_NAME" =~ ^[a-z][a-z0-9_.-]{1,31}$ ]] || { echo "create-admin: the user name is lower case letters, digits, . _ - (2-32, starting with a letter)" >&2; exit 1; }
[ "$USER_NAME" != akadmin ] || { echo "create-admin: pick your own name; akadmin is LABaPe's own account" >&2; exit 1; }
[[ "$EMAIL" == *@* ]] || { echo "create-admin: that is not an email address" >&2; exit 1; }

if [ -t 0 ]; then
  read -r -s -p "Password for $USER_NAME (12 or more characters): " PW; echo
  read -r -s -p "Again: " PW2; echo
  [ "$PW" = "$PW2" ] || { echo "create-admin: the two passwords differ" >&2; exit 1; }
else
  IFS= read -r PW || true
fi
[ "${#PW}" -ge 12 ] || { echo "create-admin: the password needs 12 or more characters" >&2; exit 1; }

export AK_NEW_USER="$USER_NAME" AK_NEW_EMAIL="$EMAIL" AK_NEW_PW="$PW"
$COMPOSE exec -T -e AK_NEW_USER -e AK_NEW_EMAIL -e AK_NEW_PW worker ak shell -c "
import os
from authentik.core.models import Group, User
name, email = os.environ['AK_NEW_USER'], os.environ['AK_NEW_EMAIL']
user, created = User.objects.get_or_create(username=name, defaults={'name': name, 'email': email})
user.email, user.is_active, user.type = email, True, 'internal'
if not user.name:
    user.name = name
user.set_password(os.environ['AK_NEW_PW'])
user.save()
for g in ('authentik Admins', 'labape-admins'):
    group = Group.objects.filter(name=g).first()
    if group is not None:
        group.users.add(user)
print('create-admin: ' + ('created ' if created else 'updated ') + name)
" 2>&1 | grep '^create-admin:' || { echo "create-admin: Authentik did not answer; is it running? ($COMPOSE logs worker)" >&2; exit 1; }
