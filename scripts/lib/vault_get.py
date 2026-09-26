#!/usr/bin/env python3
"""Decrypt secrets.vault.yml and print one key's value (or the whole
thing as JSON) to stdout. Wraps `ansible-vault view` rather than
reimplementing vault decryption — Claude_Docs/Reference_Credentials.md §6.

Usage: vault_get.py <vault-file> <vault-password-file> [key]
"""
import json
import subprocess
import sys

import yaml


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(f"usage: {sys.argv[0]} <vault-file> <vault-password-file> [key]", file=sys.stderr)
        return 2

    vault_file, pass_file = sys.argv[1], sys.argv[2]
    key = sys.argv[3] if len(sys.argv) == 4 else None

    result = subprocess.run(
        ["ansible-vault", "view", vault_file, "--vault-password-file", pass_file],
        capture_output=True,
        text=True,
        check=True,
    )
    data = yaml.safe_load(result.stdout)

    if key is None:
        json.dump(data, sys.stdout)
        return 0

    if key not in data:
        print(f'labape: {vault_file} has no key "{key}"', file=sys.stderr)
        return 1

    print(data[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
