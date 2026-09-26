#!/usr/bin/env python3
"""Claude_Docs/Reference_Validate-Setup.md §4 (Hyper-V) / User_Docs/Install-OpenTofu-WSL.md §5.

Confirms WinRM is reachable and authenticates against a Windows host —
the Hyper-V host itself before tofu/backends/hyperv exists (M2), or any
Windows VM once M3 lands (User_Docs/Install-Ansible-WSL.md §3).

Usage:
  test-winrm-connectivity.py                    # reads hyperv_host/hyperv_user/
                                                 # hyperv_password from the vault
  test-winrm-connectivity.py <host> <user>      # explicit target; password from
                                                 # -p, HYPERV_PASSWORD, or a prompt

Tries HTTPS (5986) with NTLM and certificate validation disabled — the
self-signed-cert setup User_Docs/Install-OpenTofu-WSL.md §3 walks
through, same reasoning as Claude_Docs/Reference_Credentials.md §2's insecure=true.
"""
import argparse
import getpass
import os
import subprocess
import sys


def vault_get(root_dir: str, key: str) -> str | None:
    vault_file = os.path.join(root_dir, "secrets.vault.yml")
    pass_file = os.environ.get(
        "LABAPE_VAULT_PASS_FILE", os.path.expanduser("~/.labape-vault-pass")
    )
    if not (os.path.isfile(vault_file) and os.path.isfile(pass_file)):
        return None
    result = subprocess.run(
        [sys.executable, os.path.join(root_dir, "scripts/lib/vault_get.py"), vault_file, pass_file, key],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", nargs="?", help="Windows host IP/hostname — omit to read hyperv_host from the vault")
    parser.add_argument("user", nargs="?", help="Windows username — omit to read hyperv_user from the vault")
    parser.add_argument("-p", "--password", help="Password (avoid on shared shells — prefer the env var or prompt)")
    args = parser.parse_args()

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    host = args.host or vault_get(root_dir, "hyperv_host")
    user = args.user or vault_get(root_dir, "hyperv_user")
    if not host or not user:
        print(
            "labape: no host/user given and none found in the vault (hyperv_host/hyperv_user) — "
            "skipping (this box may be libvirt-only).",
            file=sys.stderr,
        )
        return 0

    password = (
        args.password
        or os.environ.get("HYPERV_PASSWORD")
        or (vault_get(root_dir, "hyperv_password") if not args.host else None)
        or getpass.getpass(f"WinRM password for {user}@{host}: ")
    )

    try:
        import winrm
    except ImportError:
        print(
            "labape: FAIL — pywinrm not installed. `pip install --user pywinrm` "
            "(or `pipx inject ansible-core pywinrm` if ansible-core is pipx-managed, "
            "User_Docs/Install-Ansible.md §3).",
            file=sys.stderr,
        )
        return 1

    print(f"== WinRM HTTPS to {host} as {user} ==", file=sys.stderr)
    session = winrm.Session(
        f"https://{host}:5986/wsman",
        auth=(user, password),
        transport="ntlm",
        server_cert_validation="ignore",
    )
    try:
        result = session.run_cmd("hostname")
    except Exception as exc:  # noqa: BLE001 - report whatever winrm/requests raised, verbatim
        print(f"labape: FAIL — could not connect/authenticate: {exc}", file=sys.stderr)
        print(
            "labape: check User_Docs/Install-OpenTofu-WSL.md §3 (WinRM/firewall setup) "
            "and §4 (using the host's real LAN IP, not localhost).",
            file=sys.stderr,
        )
        return 1

    if result.status_code == 0:
        print(f"OK: connected — remote hostname reports: {result.std_out.decode().strip()}", file=sys.stderr)
        return 0

    print(
        f"labape: FAIL — connected but command failed (exit {result.status_code}): "
        f"{result.std_err.decode().strip()}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
