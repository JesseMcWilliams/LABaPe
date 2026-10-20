"""Which operating systems an installer targets (design §28.2, question 112).

A file's `targets` is a list mixing generic and fine-grained entries:
- "any": every OS;
- a platform: "windows", "linux";
- a family: "windows-server", "windows-client", "rhel-family", "debian-family";
- one exact OS, by its key: "rocky9", "windows_server_2022", ...

An OS matches the list when any entry covers it. An empty list means "not
set" and matches nothing on purpose, so a deploy never silently uses an
installer nobody has classified (the migration and uploads fill it in).
"""
from __future__ import annotations

from pathlib import PurePath

# OS key -> (platform, family, label). The keys are the template/OS keys used everywhere else.
OSES: dict[str, tuple[str, str, str]] = {
    "rocky9": ("linux", "rhel-family", "Rocky Linux 9"),
    "ubuntu_lts": ("linux", "debian-family", "Ubuntu 24.04 LTS"),
    "ubuntu_26": ("linux", "debian-family", "Ubuntu 26.04"),
    "debian_latest": ("linux", "debian-family", "Debian (latest)"),
    "windows_server_2019": ("windows", "windows-server", "Windows Server 2019"),
    "windows_server_2022": ("windows", "windows-server", "Windows Server 2022"),
    "windows_server_2025": ("windows", "windows-server", "Windows Server 2025"),
    "windows_11": ("windows", "windows-client", "Windows 11"),
    "windows_10": ("windows", "windows-client", "Windows 10"),
}
# The smallest disk an OS installs on, in GB; it must match `min_disk_gb` in
# tofu/backends/libvirt/main.tf (a test checks), which refuses smaller ones at plan time.
MIN_DISK_GB: dict[str, int] = {"windows_11": 64}
PLATFORMS = {"windows": "Windows (any)", "linux": "Linux (any)"}
FAMILIES = {"windows-server": "Windows Server", "windows-client": "Windows client",
            "rhel-family": "RHEL family (Rocky, RHEL, Alma)", "debian-family": "Debian family (Debian, Ubuntu)"}


def vocabulary() -> list[dict]:
    """Every allowed target, grouped for the UI's picker."""
    out = [{"value": "any", "label": "Any OS", "group": "Generic"}]
    out += [{"value": k, "label": v, "group": "Generic"} for k, v in PLATFORMS.items()]
    out += [{"value": k, "label": v, "group": "OS family"} for k, v in FAMILIES.items()]
    out += [{"value": k, "label": v[2], "group": "Specific OS"} for k, v in OSES.items()]
    return out


VALID = {t["value"] for t in vocabulary()}


def invalid(targets: list[str]) -> list[str]:
    return [t for t in targets if t not in VALID]


def matches(targets: list[str], os_key: str) -> bool:
    """Whether a file with these targets may be used on os_key."""
    if "any" in targets:
        return True
    info = OSES.get(os_key)
    if info is None:
        return False
    platform, family, _ = info
    return bool({platform, family, os_key} & set(targets))


def covers_platform(targets: list[str], platform: str) -> bool:
    """Whether the targets include at least one OS of this platform (windows or linux)."""
    return any(matches(targets, k) for k, v in OSES.items() if v[0] == platform)


def infer(filename: str) -> list[str]:
    """A starting value from the file's extension: MSI and EXE -> windows, DEB ->
    debian-family, RPM -> rhel-family, anything else -> any."""
    ext = PurePath(filename).suffix.lower()
    if ext in (".msi", ".exe", ".msu", ".msix", ".appx"):
        return ["windows"]
    if ext == ".deb":
        return ["debian-family"]
    if ext == ".rpm":
        return ["rhel-family"]
    return ["any"]


def describe(targets: list[str]) -> str:
    labels = {t["value"]: t["label"] for t in vocabulary()}
    return ", ".join(labels.get(t, t) for t in targets) or "not set"
