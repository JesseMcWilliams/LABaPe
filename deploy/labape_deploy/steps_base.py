"""The install steps. Each takes the run context and returns (status, message),
or raises StepError. Every step checks what's already there first, so
re-running is safe."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import getpass
import os
import pwd
import platform
import secrets
import shutil
import socket
import string
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .answers import Resolver
from .util import Runner, StepError

DONE = "Completed"
NA = "NotApplicable"

APT_PACKAGES = [
    "qemu-system-x86", "qemu-utils", "libvirt-daemon-system", "libvirt-clients", "virtinst", "xorriso",
    "python3-yaml", "python3-venv", "python3-libvirt", "git", "curl", "unzip", "openssl", "iputils-ping",
    "network-manager", "perl", "smartmontools", "lm-sensors", "ethtool",
]
# A web-only install (--profile web): the container image carries the engine's tools.
# Monitoring (question 173): disk health, temperatures and NIC details on every host the stack watches.
WEB_PACKAGES = ["python3-yaml", "python3-venv", "git", "curl", "unzip", "openssl", "iputils-ping", "perl",
                "smartmontools", "lm-sensors", "ethtool"]
# RHEL family (Rocky, Alma; question 128), kvm profile: the same tools under their dnf names, plus
# SELinux's labelling tool and needs-restarting. ansible-core needs Python 3.12 (Rocky 9's own is 3.9).
RPM_PACKAGES = [
    "qemu-kvm", "qemu-img", "libvirt", "libvirt-client", "virt-install", "xorriso", "python3-pyyaml",
    "python3-libvirt", "git", "curl", "unzip", "openssl", "iputils", "NetworkManager", "perl", "smartmontools",
    "lm_sensors", "ethtool", "policycoreutils-python-utils", "dnf-utils",
]


def os_release(path: str = "/etc/os-release") -> dict:
    """/etc/os-release as a dict (platform.freedesktop_os_release needs Python 3.10; RHEL 9 has 3.9)."""
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def os_family(info: dict | None = None) -> str:
    """debian, rhel, or '' (unsupported)."""
    info = os_release() if info is None else info
    ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
    if "debian" in ids:
        return "debian"
    if ids & {"rhel", "centos", "fedora", "rocky", "almalinux"}:
        return "rhel"
    return ""


def cpu_arch() -> str:
    """amd64 or arm64, as release downloads name them (a Raspberry Pi is arm64; design §29.6)."""
    m = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def rpm_packages(info: dict | None = None) -> list[str]:
    info = os_release() if info is None else info
    major = info.get("VERSION_ID", "").split(".")[0]
    return RPM_PACKAGES + (["python3.12", "python3.12-pip"] if major == "9" else [])


def kvm_side(a) -> bool:
    """Profiles that make this machine a KVM host (full, kvm)."""
    return a.need("Profile") in ("full", "kvm")
VENV = Path("/opt/labape/venv")
COLLECTIONS = ["ansible.windows:==3.8.0", "microsoft.ad:==1.12.1", "community.general:==13.4.0",
               "chocolatey.chocolatey:==1.6.0"]
TEMPLATE_POOL = "labape-templates"


@dataclass
class Ctx:
    r: Runner
    a: Resolver
    repo: Path
    deploy_root: Path
    force: bool = False
    # Install from a full-stack backup (design §33): the archive, its key, and a drill's
    # maintenance mode.
    backup: Path | None = None
    backup_key: Path | None = None
    restore_drill: bool = False

    @property
    def user(self) -> str:
        return self.a.need("ServiceUser")

    @property
    def compose(self) -> list[str]:
        return ["docker", "compose"] if self.a.need("ContainerRuntime") == "docker" else ["podman", "compose"]


# Helpers the install groups share (kept here so each group can import them without a cycle).

def _installed(c: Ctx, pkg: str) -> bool:
    if os_family() == "rhel":
        # A missing package prints "package X is not installed" on stdout, so compare the name.
        return c.r.out(["rpm", "-q", "--qf", "%{NAME}", pkg]) == pkg
    # Exact match: "unknown ok not-installed" also ends with "installed".
    return c.r.out(["dpkg-query", "-W", "-f=${Status}", pkg]) == "install ok installed"


def _catalog(c: Ctx) -> tuple[dict, str]:
    nets = {}
    for n in c.a.need("Networks"):
        nets[n["Name"]] = {
            "cidr": n["Cidr"], "gateway": n.get("Gateway", ""), "dns_servers": n.get("DnsServers", []),
            "bridge": n["Bridge"], "addressing": n.get("Addressing", ["static"]),
            "static_pools": n.get("StaticPools", []), "dhcp_ranges": n.get("DhcpRanges", []),
            "reserved": n.get("Reserved", []),
        }
    return nets, c.a.need("DefaultNetwork")
