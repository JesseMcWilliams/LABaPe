# Sourced by the entry scripts: the command line's vault password and VM key
# from the platform store (design §26, question 102 c), via store_get.py.
#
#   labape_cli_secrets "$ROOT_DIR"
#
# Does nothing when LABAPE_VAULT_PASS_FILE is already set (the web interface's
# jobs, or a script started by another one), or when this host has no store
# login (~/.config/labape/store.env): then ~/.labape-vault-pass and the vault's
# ansible_ssh_private_key_path are used as before. Otherwise it writes them to
# a per-user directory in memory ($XDG_RUNTIME_DIR, else /run/user/<uid>),
# refreshed on every run; its fixed path keeps the inventories' key path valid.

labape_cli_secrets() {
  local root="$1" dir
  [ -z "${LABAPE_VAULT_PASS_FILE:-}" ] || return 0
  [ -f "${LABAPE_STORE_ENV:-$HOME/.config/labape/store.env}" ] || return 0
  dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  [ -d "$dir" ] && [ -w "$dir" ] || { echo "labape: no per-user runtime directory ($dir) for the store's secrets" >&2; exit 1; }
  dir="$dir/labape-cli"
  (umask 077; mkdir -p "$dir")
  python3 "$root/scripts/lib/store_get.py" vault-pass --vault "$root/secrets.vault.yml" --out "$dir/vault-pass" || exit 1
  python3 "$root/scripts/lib/store_get.py" ssh --out-dir "$dir/ssh" || exit 1
  export LABAPE_VAULT_PASS_FILE="$dir/vault-pass"
  export LABAPE_SSH_PRIVATE_KEY_PATH="$dir/ssh/labape_bootstrap"
  # Previous VM keys, for VMs that were off during a rotation (like the worker's jobs).
  if [ -z "${ANSIBLE_SSH_COMMON_ARGS:-}" ]; then
    local k args=""
    for k in $(ls -1r "$dir/ssh/history"/labape_bootstrap-* 2>/dev/null | grep -v '\.pub$'); do args+=" -i $k"; done
    [ -z "$args" ] || export ANSIBLE_SSH_COMMON_ARGS="${args# }"
  fi
}
