#!/usr/bin/env bash
# lab-live.sh <script> [args...]
# Copy tools/helpers/live/ to the lab host (~/labape-test-tools) and run one
# of its scripts there:
#   *.py  in a throwaway labape:local container on the host network, signed in
#         through the test Authentik (its .env is mounted read-only; the test
#         password is read there and never printed); the test secret stores'
#         LABaPe logins (~/.config/labape/secrets-test.env, if present) too;
#   *.sh  on the lab host itself, from the stack's clone.
# Example: tools/helpers/lab-live.sh env_password.py 3 list
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lab.sh"
[ $# -ge 1 ] || { sed -n '2,10p' "$0" >&2; exit 1; }
script="$1"; shift
[ -f "$HERE/live/$script" ] || { echo "no such script: live/$script" >&2; ls "$HERE/live" >&2; exit 1; }

"${LAB_SSH[@]}" 'mkdir -p ~/labape-test-tools'
"${LAB_SCP[@]}" "$HERE"/live/*.py "$HERE"/live/*.sh "$LABAPE_LAB_SSH:labape-test-tools/"
"${LAB_SSH[@]}" "sed -i 's/\r\$//' ~/labape-test-tools/*"

args=""; [ $# -eq 0 ] || args="$(printf ' %q' "$@")"
case "$script" in
  *.py)
    # $R and $HOME expand on the lab host (a ~ in R=... does too).
    remote="R=$LAB_REPO; docker run --rm --network host"
    remote+=' -v "$HOME/labape-test-tools:/tools:ro" -v "$R/tools/authentik-test/.env:/authentik.env:ro"'
    remote+=' $(f="$HOME/.config/labape/secrets-test.env"; [ -f "$f" ] && echo "-v $f:/secrets-test.env:ro")'
    remote+=" -e LABAPE='$LAB_URL' -e AK_ENV=/authentik.env"
    remote+=" --entrypoint python3 labape:local /tools/$script$args"
    remote+=' 2>&1 | grep -v -E "InsecureRequestWarning|warnings.warn"'
    "${LAB_SSH[@]}" "$remote" ;;
  *.sh)
    "${LAB_SSH[@]}" "LABAPE_LAB_REPO=$LAB_REPO bash ~/labape-test-tools/$script$args" ;;
  *) echo "unknown script type: $script" >&2; exit 1 ;;
esac
