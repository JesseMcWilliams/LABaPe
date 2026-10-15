#!/usr/bin/env python3
"""Take the software inventory of the lab stack's worker image for the SBOM (sbom/image-snapshot.json).

Usage: sbom_snapshot.py [--out FILE] [--print-version]
  --out FILE        write the snapshot there (default sbom/image-snapshot.json)
  --print-version   only print the worker image's version (LABAPE_VERSION, from `git describe`) and exit

Needs LABAPE_LAB_SSH (user@host of the lab host) and optionally LABAPE_LAB_KEY, as the other lab
helpers do. Over SSH it asks the running worker container for its Python packages, Debian packages,
OpenTofu, Packer and step versions, Ansible collections, the mirrored OpenTofu providers and Packer
plugins, and the digests of the images the stack runs, plus the syslog image's Debian packages.
Changes nothing on the lab. tools/helpers/release.sh runs this, then tools/helpers/sbom.py.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIND_WORKER = "$(docker ps --format '{{.Names}}' | grep -m1 labape-worker)"


def ssh(command: str, stdin: str | None = None, timeout: int = 300) -> str:
    target = os.environ.get("LABAPE_LAB_SSH")
    if not target:
        raise SystemExit("sbom_snapshot: set LABAPE_LAB_SSH to user@host of the lab host (see Live-Testing.local.md)")
    args = ["ssh", "-o", "BatchMode=yes"]
    if os.environ.get("LABAPE_LAB_KEY"):
        args += ["-i", os.environ["LABAPE_LAB_KEY"]]
    r = subprocess.run(args + [target, command], input=stdin, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit(f"sbom_snapshot: `{command[:80]}` failed on the lab host: {r.stderr.strip()[-300:]}")
    return r.stdout


def in_worker(command: str, stdin: str | None = None) -> str:
    return ssh(f"docker exec -i {FIND_WORKER} {command}", stdin)


# --- parsing (kept apart from the ssh calls so tests can feed them sample output) -----------

def parse_tools(text: str) -> dict[str, str]:
    """`tofu version`, `packer version`, `step version` first lines."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"OpenTofu v(\S+)", line)
        if m:
            out["OpenTofu"] = m.group(1)
        m = re.match(r"Packer v(\S+)", line)
        if m:
            out["Packer"] = m.group(1)
        m = re.match(r"Smallstep CLI/(\S+)", line)
        if m:
            out["step CLI"] = m.group(1)
    return out


def parse_collections(text: str) -> dict[str, str]:
    """`ansible-galaxy collection list --format json`: {path: {name: {version}}}."""
    data = json.loads(text)
    return {name: info["version"] for collections in data.values() for name, info in sorted(collections.items())}


def parse_providers(paths: str) -> dict[str, str]:
    """OpenTofu's provider mirror: .../registry.opentofu.org/<namespace>/<name>/terraform-provider-<name>_<version>_linux_amd64.zip"""
    out = {}
    for p in paths.split():
        m = re.search(r"/([^/]+)/([^/]+)/terraform-provider-[^/]+?_(\d[^_/]*)_linux_[a-z0-9]+\.zip$", p)
        if m:
            out[f"{m.group(1)}/{m.group(2)}"] = m.group(3)
    return dict(sorted(out.items()))


def parse_plugins(paths: str) -> dict[str, str]:
    """Packer's plugin folder: .../github.com/<namespace>/<name>/packer-plugin-<name>_v<version>_x5.0_linux_amd64"""
    out = {}
    for p in paths.split():
        m = re.search(r"/github\.com/([^/]+)/([^/]+)/packer-plugin-[^/]+?_v(\d[^_/]*)_[^/]*_linux_[a-z0-9]+$", p)
        if m:
            out[f"{m.group(1)}/{m.group(2)}"] = m.group(3)
    return dict(sorted(out.items()))


def parse_images(text: str) -> dict[str, str]:
    """Lines of `<image> <name@sha256:digest>`: the digest per image the stack runs (images built here have none)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and "@sha256:" in parts[1]:
            out[parts[0]] = parts[1].split("@", 1)[1]
    return dict(sorted(out.items()))


def parse_packages(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.strip()]


def take() -> dict:
    probe = (HERE / "sbom_probe.py").read_text(encoding="utf-8")
    snap = json.loads(in_worker("/opt/labape/venv/bin/python -", probe))
    snap["tools"] = parse_tools(in_worker("sh -c 'tofu version | head -1; packer version | head -1; step version | head -1'"))
    snap["collections"] = parse_collections(in_worker("ansible-galaxy collection list -p /usr/share/ansible/collections --format json"))
    snap["providers"] = parse_providers(in_worker("find /opt/labape/tofu-mirror -name '*.zip'"))
    snap["plugins"] = parse_plugins(in_worker("find /opt/labape/packer-plugins -name 'packer-plugin-*_linux_*' ! -name '*SHA256SUM'"))
    snap["images"] = parse_images(ssh(
        "docker ps --format '{{.Image}}' | sort -u | while read i; do printf '%s %s\\n' \"$i\" "
        "\"$(docker image inspect --format '{{index .RepoDigests 0}}' \"$i\" 2>/dev/null)\"; done"))
    syslog = ssh("docker run --rm --entrypoint sh labape-syslog:local -c "
                 "\"dpkg-query -W -f '\\${Package}\\t\\${Version}\\n' rsyslog rsyslog-pgsql rsyslog-openssl ca-certificates procps; "
                 "echo TOTAL:\\$(dpkg-query -W | wc -l)\"")
    lines = parse_packages(syslog)
    total = int(lines[-1].split(":")[1])
    snap["syslog_image"] = {"packages": lines[:-1], "dpkg_total": total}
    version = in_worker("printenv LABAPE_VERSION").strip()
    snap["image_version"] = version
    snap["captured"] = dt.date.today().isoformat()
    snap["captured_from"] = f"the lab stack's worker container (labape:local, built as {version}) and the images it runs"
    for key in ("tools", "collections", "providers", "plugins", "images"):
        if not snap[key]:
            raise SystemExit(f"sbom_snapshot: found no {key} in the lab stack; is it running?")
    return snap


def main() -> int:
    args = sys.argv[1:]
    if "--print-version" in args:
        print(in_worker("printenv LABAPE_VERSION").strip())
        return 0
    out = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "sbom" / "image-snapshot.json"
    snap = take()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"sbom_snapshot: {len(snap['python'])} Python packages, {len(snap['images'])} images, image {snap['image_version']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
