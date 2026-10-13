"""KVM host administration over SSH (design §28.4, questions 113-115, 120).

LABaPe reaches each KVM host as a dedicated access account (default
`localadmin01`), separate from the service user that owns the stack, with
its own key (`~/.ssh/labape_hostadmin` in the worker home). An admin sets the
account up once by typing an existing admin login: the password is used for
that one SSH session (sudo), never stored or logged. The account is
key-only (its password is locked) and has passwordless sudo, because LABaPe
uses it to install and manage the host as well as to read it.

From then on the worker reads, over that account:
- facts hourly (and on demand): disks (lsblk), filesystems, LVM, ZFS,
  mdraid, NICs and bridges, sensors, OS, updates, failed units, accounts and
  groups;
- SMART daily (smartctl, when installed);
- metrics every 5 minutes, kept 7 days: CPU, memory, filesystems,
  temperatures, NIC counters.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import threading
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import HostFacts, HostMetric, KvmHost, utcnow
from . import hostio

KEY_NAME = "labape_hostadmin"
ACCOUNT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+( [\w@.:-]+)?$")
METRICS_EVERY = dt.timedelta(minutes=5)
FACTS_EVERY = dt.timedelta(hours=1)
SMART_EVERY = dt.timedelta(hours=24)
KEEP_METRICS = dt.timedelta(days=7)
PROTECTED = {"root", "localadmin01"}           # never deleted or locked from LABaPe


class AdminError(RuntimeError):
    pass


def key_path() -> Path:
    return hostio.home() / ".ssh" / KEY_NAME


def ensure_key() -> str:
    """The host-admin public key, making the pair once (separate from the libvirt key)."""
    key = key_path()
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(key.parent, 0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "labape-hostadmin", "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
    return key.with_name(KEY_NAME + ".pub").read_text(encoding="utf-8").strip()


class Ssh:
    """Commands on a host as its LABaPe access account."""

    def __init__(self, address: str, port: int, user: str):
        if not address:
            raise AdminError("the host has no SSH address")
        self.address, self.port, self.user = address, port or 22, user

    def base(self) -> list[str]:
        return ["ssh", "-i", str(key_path()), *hostio.SSH_OPTS, "-p", str(self.port), f"{self.user}@{self.address}"]

    def run(self, command: str, input: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run([*self.base(), command], input=input, capture_output=True, timeout=timeout,
                              env=hostio.tool_env(), check=False)

    def python(self, script: str, args: dict, sudo: bool = True, timeout: float = 120) -> dict:
        payload = json.dumps({"script": script, "args": args}).encode()
        boot = "import json,sys;d=json.load(sys.stdin);exec(d['script'],{'ARGS':d['args']})"
        cmd = f"{'sudo -n ' if sudo else ''}python3 -c {shlex.quote(boot)}"
        r = self.run(cmd, input=payload, timeout=timeout)
        if r.returncode != 0:
            raise AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-500:] or f"exit {r.returncode}")
        return json.loads(r.stdout.decode())


def for_host(h: KvmHost) -> Ssh:
    if h.access_status != "ready":
        raise AdminError("set up SSH access to this host first")
    return Ssh(h.ssh_address, h.ssh_port, h.ssh_user)


# --- one-time setup with typed-in credentials (question 114) ----------------------------------

SETUP = r'''
set -euo pipefail
ACCOUNT=%(account)s
KEY=%(key)s
if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "LABaPe host access" "$ACCOUNT"
fi
passwd -l "$ACCOUNT" >/dev/null 2>&1 || usermod -L "$ACCOUNT"
HOME_DIR="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
install -d -m 0700 -o "$ACCOUNT" -g "$(id -gn "$ACCOUNT")" "$HOME_DIR/.ssh"
AK="$HOME_DIR/.ssh/authorized_keys"
touch "$AK"
grep -qxF "$KEY" "$AK" || printf '%%s\n' "$KEY" >> "$AK"
chown "$ACCOUNT":"$(id -gn "$ACCOUNT")" "$AK"; chmod 0600 "$AK"
SUDOERS=/etc/sudoers.d/labape-$ACCOUNT
printf '# LABaPe host access account (design 28.4): key-only login, used to read and manage this host.\n%%s ALL=(ALL) NOPASSWD: ALL\n' "$ACCOUNT" > "$SUDOERS.tmp"
chmod 0440 "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp" >/dev/null
mv "$SUDOERS.tmp" "$SUDOERS"
getent group libvirt >/dev/null && usermod -aG libvirt "$ACCOUNT" || true
# sshd may only let some groups in (e.g. Raspberry Pi OS images: AllowGroups AllowSSH): join the first
# one, or the account couldn't sign in. An AllowUsers list is the admin's to change, so it's reported.
SSHD="$(sshd -T 2>/dev/null || true)"
ALLOWED="$(printf '%%s\n' "$SSHD" | awk '$1=="allowgroups"{for(i=2;i<=NF;i++) print $i}')"
if [ -n "$ALLOWED" ]; then
  MEMBER=no
  for g in $ALLOWED; do id -nG "$ACCOUNT" | tr ' ' '\n' | grep -qx "$g" && MEMBER=yes; done
  G="$(printf '%%s\n' $ALLOWED | head -1)"
  if [ "$MEMBER" = no ] && getent group "$G" >/dev/null; then
    usermod -aG "$G" "$ACCOUNT"
    echo "labape-setup: added $ACCOUNT to $G (sshd AllowGroups)"
  fi
fi
USERS="$(printf '%%s\n' "$SSHD" | awk '$1=="allowusers"{for(i=2;i<=NF;i++) print $i}')"
if [ -n "$USERS" ] && ! printf '%%s\n' $USERS | grep -qx "$ACCOUNT"; then
  echo "labape-setup: warning: sshd's AllowUsers doesn't list $ACCOUNT; add it there, or it can't sign in" >&2
fi
# Disk health (SMART) for the Drives section; best effort, the account works without it.
if ! command -v smartctl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq smartmontools >/dev/null 2>&1 || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq smartmontools >/dev/null 2>&1; } || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q smartmontools >/dev/null 2>&1 || true
  fi
  command -v smartctl >/dev/null 2>&1 && echo "labape-setup: installed smartmontools" || echo "labape-setup: couldn't install smartmontools (install it for disk health)"
fi
echo "labape-setup: $ACCOUNT ready"
'''


def setup_access(address: str, port: int, admin_user: str, admin_password: str, account: str,
                 runner=subprocess.run) -> str:
    """Create or refresh the access account with an admin's password (one SSH session, sudo).
    The password goes to ssh through SSH_ASKPASS and to sudo on stdin; it's never on a command
    line, in a file that outlives the call, or in a log. Returns the setup output."""
    if not ACCOUNT_RE.match(account):
        raise AdminError("the access account must be a valid Linux user name, e.g. localadmin01")
    if not ACCOUNT_RE.match(admin_user):
        raise AdminError("the admin user must be a valid Linux user name")
    pub = ensure_key()
    if not PUBKEY_RE.match(pub):
        raise AdminError("the host-admin public key looks wrong")
    script = SETUP % {"account": shlex.quote(account), "key": shlex.quote(pub)}
    with tempfile.TemporaryDirectory() as tmp:
        askpass = Path(tmp) / "askpass"
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$LABAPE_ASKPASS"\n', encoding="utf-8")
        askpass.chmod(stat.S_IRWXU)
        env = {**hostio.tool_env(), "SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "force",
               "DISPLAY": "labape:0", "LABAPE_ASKPASS": admin_password}
        argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
                "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "NumberOfPasswordPrompts=1", "-p", str(port or 22), f"{admin_user}@{address}"]
        if admin_user == "root":
            argv.append("bash -s")
            stdin = script
        else:
            # sudo reads the password line from stdin, bash -s the script after it.
            argv.append("sudo -S -p '' bash -s")
            stdin = admin_password + "\n" + script
        r = runner(argv, input=stdin.encode(), capture_output=True, timeout=120, env=env, check=False)
    out = (r.stdout or b"").decode(errors="replace")
    err = (r.stderr or b"").decode(errors="replace").replace(admin_password, "***")
    if r.returncode != 0:
        if "Permission denied" in err:
            raise AdminError(f"{admin_user}@{address} refused the password (wrong password, or the host's sshd "
                             "allows only keys: PasswordAuthentication no)")
        if "incorrect password" in err or "not in the sudoers" in err:
            raise AdminError(f"{admin_user} can't use sudo with that password on {address}")
        raise AdminError(err.strip()[-400:] or f"setup failed (exit {r.returncode})")
    return out.strip()


def verify(h: KvmHost) -> None:
    r = Ssh(h.ssh_address, h.ssh_port, h.ssh_user).run("sudo -n true && echo ok", timeout=30)
    if r.returncode != 0 or b"ok" not in r.stdout:
        raise AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or "sudo -n failed")


# --- what's collected (run on the host with its python3, as root through sudo -n) ---------------

FACTS = r'''
import glob, json, os, pwd, grp, re, shutil, subprocess

def sh(*cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""

def js(*cmd):
    out = sh(*cmd)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None

def read(p, default=""):
    try:
        return open(p, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return default

out = {}
osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', read("/etc/os-release"), re.M))
up = float(read("/proc/uptime", "0").split()[0] or 0)
out["os"] = {"name": osr.get("PRETTY_NAME", ""), "id": osr.get("ID", ""), "version": osr.get("VERSION_ID", ""),
             "kernel": os.uname().release, "hostname": os.uname().nodename, "uptime_seconds": int(up),
             "load": read("/proc/loadavg").split()[:3], "cpus": os.cpu_count(),
             "cpu_model": next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines() if l.startswith("model name")), ""),
             "memory_mb": int(re.search(r"MemTotal:\s+(\d+)", read("/proc/meminfo")).group(1)) // 1024,
             "reboot_required": os.path.exists("/var/run/reboot-required"),
             "timesync": dict(l.split("=", 1) for l in sh("timedatectl", "show").splitlines() if "=" in l),
             "failed_units": [l.split()[0] for l in sh("systemctl", "--failed", "--plain", "--no-legend").splitlines() if l.strip()]}
if shutil.which("apt"):
    out["os"]["updates"] = len([l for l in sh("apt", "list", "--upgradable", timeout=60).splitlines() if "/" in l])
elif shutil.which("dnf"):
    out["os"]["updates"] = len([l for l in sh("dnf", "-q", "check-update", timeout=120).splitlines() if l.strip() and not l.startswith(" ")])
    if shutil.which("needs-restarting"):              # RHEL family: exit 1 means a reboot is needed
        out["os"]["reboot_required"] = subprocess.run(["needs-restarting", "-r"], capture_output=True, timeout=120).returncode == 1
out["disks"] = (js("lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,ROTA,TRAN,FSTYPE,MOUNTPOINTS,LABEL,UUID") or {}).get("blockdevices", [])
out["filesystems"] = []
for l in sh("df", "-PT", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay", "-x", "squashfs").splitlines()[1:]:
    p = l.split()
    if len(p) >= 7:
        out["filesystems"].append({"device": p[0], "type": p[1], "size": int(p[2]), "used": int(p[3]),
                                   "avail": int(p[4]), "mount": p[6]})
lvm = {}
for kind in ("pvs", "vgs", "lvs"):
    if shutil.which(kind):
        r = js(kind, "--reportformat", "json", "--units", "b", "--nosuffix")
        lvm[kind] = ((r or {}).get("report") or [{}])[0].get(kind[:-1], []) if r else []
out["lvm"] = lvm
out["zfs"] = [dict(zip(("name", "size", "alloc", "free", "health"), l.split("\t")))
              for l in sh("zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health").splitlines()] if shutil.which("zpool") else []
out["mdraid"] = read("/proc/mdstat")
nics = []
for a in js("ip", "-j", "-s", "addr") or []:
    name = a.get("ifname", "")
    if name == "lo" or name.startswith(("veth", "vnet", "docker", "br-", "virbr")):
        continue
    base = f"/sys/class/net/{name}"
    speed = read(f"{base}/speed", "")
    uevent = read(f"{base}/uevent")
    kind = ("bridge" if os.path.isdir(f"{base}/bridge") else "bond" if os.path.isdir(f"{base}/bonding")
            else "vlan" if "DEVTYPE=vlan" in uevent else "wireless" if os.path.isdir(f"{base}/wireless")
            else "nic" if os.path.exists(f"{base}/device") else "virtual")
    nics.append({"name": name, "state": a.get("operstate"), "mac": a.get("address"), "mtu": a.get("mtu"),
                 "addresses": [f"{x['local']}/{x['prefixlen']}" for x in a.get("addr_info", [])],
                 "speed_mbps": int(speed) if speed.isdigit() else None,
                 "kind": kind,
                 "members": sorted(os.listdir(f"{base}/brif")) if os.path.isdir(f"{base}/brif") else read(f"{base}/bonding/slaves").split(),
                 "master": os.path.basename(os.path.realpath(f"{base}/master")) if os.path.exists(f"{base}/master") else "",
                 "driver": os.path.basename(os.path.realpath(f"{base}/device/driver")) if os.path.exists(f"{base}/device/driver") else ""})
out["nics"] = nics
out["sensors"] = js("sensors", "-j") or {}
if not out["sensors"]:
    therm = {}
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        t = read(f"{z}/temp")
        if t.lstrip("-").isdigit():
            therm[read(f"{z}/type") or os.path.basename(z)] = int(t) / 1000
    out["sensors"] = {"thermal": therm} if therm else {}
admins = set()
for g in ("sudo", "wheel", "admin"):
    try:
        admins |= set(grp.getgrnam(g).gr_mem)
    except KeyError:
        pass
users = []
for u in pwd.getpwall():
    if u.pw_uid == 0 or u.pw_uid >= 1000 and u.pw_uid < 60000:
        shadow = sh("passwd", "-S", u.pw_name).split()
        users.append({"name": u.pw_name, "uid": u.pw_uid, "home": u.pw_dir, "shell": u.pw_shell, "comment": u.pw_gecos,
                      "groups": sorted(g.gr_name for g in grp.getgrall() if u.pw_name in g.gr_mem),
                      "admin": u.pw_uid == 0 or u.pw_name in admins or os.path.exists(f"/etc/sudoers.d/labape-{u.pw_name}"),
                      "locked": len(shadow) > 1 and shadow[1] in ("L", "LK")})
out["accounts"] = users
out["groups"] = [{"name": g.gr_name, "gid": g.gr_gid, "members": sorted(g.gr_mem)} for g in grp.getgrall()
                 if g.gr_gid >= 1000 and g.gr_gid < 60000 or g.gr_name in ("sudo", "wheel", "libvirt", "kvm", "docker", "adm")]
out["tools"] = {t: bool(shutil.which(t)) for t in ("smartctl", "sensors", "ethtool", "virsh")}
print(json.dumps(out))
'''

SMART = r'''
import json, shutil, subprocess
out = {"available": bool(shutil.which("smartctl")), "disks": []}
if out["available"]:
    scan = json.loads(subprocess.run(["smartctl", "--scan-open", "-j"], capture_output=True, text=True, timeout=60).stdout or "{}")
    for d in scan.get("devices", []):
        r = subprocess.run(["smartctl", "-j", "-a", d["name"], "-d", d.get("type", "auto")], capture_output=True, text=True, timeout=60)
        try:
            j = json.loads(r.stdout)
        except ValueError:
            continue
        attrs = {a["name"]: a.get("raw", {}).get("value") for a in j.get("ata_smart_attributes", {}).get("table", [])}
        nv = j.get("nvme_smart_health_information_log", {})
        out["disks"].append({"device": d["name"], "model": j.get("model_name", ""), "serial": j.get("serial_number", ""),
                             "passed": j.get("smart_status", {}).get("passed"),
                             "temperature": j.get("temperature", {}).get("current"),
                             "power_on_hours": j.get("power_on_time", {}).get("hours"),
                             "reallocated": attrs.get("Reallocated_Sector_Ct"), "pending": attrs.get("Current_Pending_Sector"),
                             "wear_pct": nv.get("percentage_used"), "media_errors": nv.get("media_errors")})
print(json.dumps(out))
'''

METRICS = r'''
import json, glob, os, re, subprocess
def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""
cpu = [int(x) for x in read("/proc/stat").splitlines()[0].split()[1:]]
mem = dict((k, int(v)) for k, v in re.findall(r"^(\w+):\s+(\d+)", read("/proc/meminfo"), re.M))
fs = {}
for l in subprocess.run(["df", "-P", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay", "-x", "squashfs"],
                        capture_output=True, text=True, timeout=30).stdout.splitlines()[1:]:
    p = l.split()
    if len(p) >= 6 and int(p[1]) > 0:
        fs[p[5]] = round(int(p[2]) * 100 / int(p[1]), 1)
temps = {}
try:
    s = json.loads(subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=20).stdout or "{}")
    for chip, feats in s.items():
        for feat, vals in (feats.items() if isinstance(feats, dict) else []):
            for k, v in (vals.items() if isinstance(vals, dict) else []):
                if k.endswith("_input") and k.startswith("temp"):
                    temps[f"{chip}/{feat}"] = v
except (OSError, ValueError, subprocess.TimeoutExpired):
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        t = read(f"{z}/temp").strip()
        if t.lstrip("-").isdigit():
            temps[read(f"{z}/type").strip() or os.path.basename(z)] = int(t) / 1000
nics = {}
for d in glob.glob("/sys/class/net/*"):
    n = os.path.basename(d)
    if n == "lo" or n.startswith(("veth", "vnet", "docker", "br-", "virbr")):
        continue
    nics[n] = [int(read(f"{d}/statistics/rx_bytes") or 0), int(read(f"{d}/statistics/tx_bytes") or 0)]
print(json.dumps({"cpu": cpu, "mem_total_kb": mem.get("MemTotal", 0), "mem_available_kb": mem.get("MemAvailable", 0),
                  "load": read("/proc/loadavg").split()[:3], "fs_used_pct": fs, "temps": temps, "nic_bytes": nics}))
'''


def collect_facts(db: Session, h: KvmHost, smart: bool = False) -> HostFacts:
    row = db.get(HostFacts, h.id) or HostFacts(kvm_host_id=h.id, facts={}, smart={})
    db.add(row)
    try:
        ssh = for_host(h)
        row.facts = ssh.python(FACTS, {}, timeout=240)
        row.collected_at, row.error = utcnow(), ""
        if smart or row.smart_at is None or utcnow() - _aware(row.smart_at) > SMART_EVERY:
            row.smart, row.smart_at = ssh.python(SMART, {}, timeout=300), utcnow()
    except (AdminError, subprocess.TimeoutExpired, ValueError) as exc:
        row.error = str(exc)[:1000]
    db.commit()
    return row


def collect_metrics(db: Session, h: KvmHost) -> dict | None:
    try:
        data = for_host(h).python(METRICS, {}, timeout=60)
    except (AdminError, subprocess.TimeoutExpired, ValueError):
        return None
    db.add(HostMetric(kvm_host_id=h.id, at=utcnow(), data=data))
    db.commit()
    return data


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


_lock = threading.Lock()


def collect_due(db: Session) -> None:
    """Called every minute by the worker loop: metrics every 5 minutes, facts hourly, SMART
    daily, metrics older than 7 days purged. Runs in a thread so slow hosts don't hold the loop."""
    if not _lock.acquire(blocking=False):
        return

    def go():
        from ..db import session_factory
        try:
            with session_factory()() as s:
                now = utcnow()
                # Enabled or not: "enabled" is about deploys, and a host being prepared is watched too.
                for h in s.scalars(select(KvmHost).where(KvmHost.access_status == "ready")):
                    last = s.scalar(select(HostMetric.at).where(HostMetric.kvm_host_id == h.id)
                                    .order_by(HostMetric.at.desc()).limit(1))
                    if last is None or now - _aware(last) >= METRICS_EVERY - dt.timedelta(seconds=20):
                        collect_metrics(s, h)
                    f = s.get(HostFacts, h.id)
                    if f is None or f.collected_at is None or now - _aware(f.collected_at) >= FACTS_EVERY:
                        collect_facts(s, h)
                    if h.log_forward and (h.log_forward_until is None
                                          or _aware(h.log_forward_until) - now < LOG_FORWARD_RENEW):
                        set_log_forwarding(s, h, True)     # renew the client certificate
                s.execute(delete(HostMetric).where(HostMetric.at < now - KEEP_METRICS))
                s.commit()
        finally:
            _lock.release()

    threading.Thread(target=go, name="hostadmin", daemon=True).start()


def series(db: Session, h: KvmHost, hours: int = 24) -> dict:
    """Metric series for the host's Dashboard: CPU %, memory %, the fullest filesystem %, the
    hottest temperature, and NIC throughput (bytes/s, summed)."""
    rows = list(db.scalars(select(HostMetric).where(HostMetric.kvm_host_id == h.id,
                                                    HostMetric.at >= utcnow() - dt.timedelta(hours=hours))
                           .order_by(HostMetric.at)))
    out = {"t": [], "cpu_pct": [], "memory_used_pct": [], "fs_max_pct": [], "temp_max_c": [], "rx_bps": [],
           "tx_bps": []}
    prev = None
    for r in rows:
        d = r.data or {}
        out["t"].append(r.at.isoformat())
        cpu = d.get("cpu") or []
        if prev and cpu and prev[1].get("cpu"):
            pc = prev[1]["cpu"]
            busy = (sum(cpu) - cpu[3] - cpu[4]) - (sum(pc) - pc[3] - pc[4]) if len(cpu) > 4 and len(pc) > 4 else 0
            total = sum(cpu) - sum(pc)
            out["cpu_pct"].append(round(busy * 100 / total, 1) if total > 0 else None)
        else:
            out["cpu_pct"].append(None)
        tot, avail = d.get("mem_total_kb") or 0, d.get("mem_available_kb") or 0
        out["memory_used_pct"].append(round((tot - avail) * 100 / tot, 1) if tot else None)
        out["fs_max_pct"].append(max((d.get("fs_used_pct") or {}).values(), default=None))
        out["temp_max_c"].append(max((d.get("temps") or {}).values(), default=None))
        nics = d.get("nic_bytes") or {}
        if prev and prev[1].get("nic_bytes"):
            secs = max((r.at - prev[0]).total_seconds(), 1)
            rx = sum(max(v[0] - prev[1]["nic_bytes"].get(k, v)[0], 0) for k, v in nics.items())
            tx = sum(max(v[1] - prev[1]["nic_bytes"].get(k, v)[1], 0) for k, v in nics.items())
            out["rx_bps"].append(round(rx / secs))
            out["tx_bps"].append(round(tx / secs))
        else:
            out["rx_bps"].append(None)
            out["tx_bps"].append(None)
        prev = (r.at, d)
    return out


def warnings(facts: dict, smart: dict, latest: dict | None) -> list[dict]:
    """What the Dashboard section flags: SMART failures and wear, heat, full filesystems,
    failed units, a pending reboot, many updates."""
    w = []
    for d in (smart or {}).get("disks", []):
        if d.get("passed") is False:
            w.append({"level": "error", "message": f"{d['device']} ({d.get('model')}): SMART overall health FAILED"})
        if (d.get("reallocated") or 0) > 0 or (d.get("pending") or 0) > 0:
            w.append({"level": "warning", "message": f"{d['device']}: {d.get('reallocated') or 0} reallocated, "
                                                     f"{d.get('pending') or 0} pending sectors"})
        if (d.get("wear_pct") or 0) >= 80:
            w.append({"level": "warning", "message": f"{d['device']}: {d['wear_pct']}% of its rated wear used"})
    for name, t in ((latest or {}).get("temps") or {}).items():
        if isinstance(t, (int, float)) and t >= 85:
            w.append({"level": "warning", "message": f"{name} at {t:.0f} °C"})
    for mount, pct in ((latest or {}).get("fs_used_pct") or {}).items():
        if pct >= 90:
            w.append({"level": "warning" if pct < 97 else "error", "message": f"{mount} is {pct}% full"})
    os_ = (facts or {}).get("os", {})
    if os_.get("failed_units"):
        w.append({"level": "warning", "message": f"Failed services: {', '.join(os_['failed_units'][:6])}"})
    if os_.get("reboot_required"):
        w.append({"level": "info", "message": "A reboot is required (updates installed)"})
    if (os_.get("updates") or 0) >= 20:
        w.append({"level": "info", "message": f"{os_['updates']} updates available"})
    if facts and not (facts.get("tools") or {}).get("smartctl"):
        w.append({"level": "info", "message": "smartctl isn't installed, so disk health isn't read (install smartmontools)"})
    return w


# --- accounts and groups (question 120: admins, on KVM hosts) ---------------------------------

NAME_RE = ACCOUNT_RE


def manage(h: KvmHost, op: str, name: str, **kw) -> str:
    """One account or group change, as root through the access account. Returns its output."""
    return manage_over(for_host(h), PROTECTED | {h.ssh_user}, op, name, **kw)


def manage_over(ssh, protected: set[str], op: str, name: str, **kw) -> str:
    """The same changes over any SSH session with passwordless sudo (a KVM host's access
    account, or a Linux VM's admin user; design §28.6)."""
    if not NAME_RE.match(name):
        raise AdminError(f"{name!r} isn't a valid user or group name")
    q = shlex.quote
    if op in ("user.delete", "user.lock") and name in protected:
        raise AdminError(f"{name} is protected: LABaPe needs it")
    groups = [g for g in (kw.get("groups") or []) if NAME_RE.match(g)]
    commands = {
        "user.add": f"useradd -m -s /bin/bash -c {q(kw.get('comment') or '')} "
                    f"{'-G ' + q(','.join(groups)) + ' ' if groups else ''}{q(name)}",
        "user.delete": f"userdel -r {q(name)}",
        "user.lock": f"usermod -L {q(name)}",
        "user.unlock": f"usermod -U {q(name)}",
        "user.groups": f"usermod -G {q(','.join(groups))} {q(name)}",
        "group.add": f"groupadd {q(name)}",
        "group.delete": f"groupdel {q(name)}",
        "group.member_add": f"gpasswd -a {q(kw.get('member') or '')} {q(name)}",
        "group.member_remove": f"gpasswd -d {q(kw.get('member') or '')} {q(name)}",
    }
    stdin = None
    if op == "user.password":
        # chpasswd reads "name:password" on stdin: never on a command line.
        if not kw.get("password"):
            raise AdminError("give the new password")
        commands[op] = "chpasswd"
        stdin = f"{name}:{kw['password']}\n".encode()
    if op == "user.add" and kw.get("ssh_key"):
        key = str(kw["ssh_key"]).strip()
        if not re.match(r"^(ssh-ed25519|ecdsa-sha2-nistp\d+|ssh-rsa|sk-ssh-ed25519@openssh.com) [A-Za-z0-9+/=]+( .*)?$", key):
            raise AdminError("that doesn't look like an SSH public key")
        home = f"$(getent passwd {q(name)} | cut -d: -f6)"
        commands[op] += (f" && install -d -m 0700 -o {q(name)} {home}/.ssh && printf '%s\\n' {q(key)} > {home}/.ssh/authorized_keys"
                         f" && chown {q(name)} {home}/.ssh/authorized_keys && chmod 0600 {home}/.ssh/authorized_keys")
    if op not in commands:
        raise AdminError(f"unknown operation {op}")
    if op.startswith("group.member") and not NAME_RE.match(kw.get("member") or ""):
        raise AdminError("give a valid member name")
    cmd = commands[op] if op != "user.add" or not kw.get("ssh_key") else f"bash -c {q(commands[op])}"
    r = ssh.run(f"sudo -n {cmd}", input=stdin, timeout=60)
    if r.returncode != 0:
        raise AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or f"{op} failed")
    return (r.stdout or b"").decode(errors="replace").strip()


# --- the host's own logs to the stack's syslog collector (design §29.1, question 124) ----------

LOG_FORWARD_RENEW = dt.timedelta(days=10)
FORWARD = r'''
import grp, json, os, shutil, subprocess
A = ARGS
# Under /etc/rsyslog.d: Ubuntu's AppArmor profile lets rsyslogd read only there, and it runs as
# the syslog user (group-readable files); Debian runs it as root. Not *.conf, so not included.
d, conf, legacy = "/etc/rsyslog.d/labape-tls", "/etc/rsyslog.d/90-labape-forward.conf", "/etc/labape-syslog"
def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)
if not A["enable"]:
    for f in (conf,):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(legacy, ignore_errors=True)
    if shutil.which("rsyslogd"):
        sh("systemctl", "restart", "rsyslog")
    print(json.dumps({"ok": True, "note": "forwarding removed"}))
    raise SystemExit(0)
# rsyslog with its OpenSSL driver (Debian: rsyslog-openssl; RHEL family: rsyslog-openssl).
have_ossl = any(os.path.exists(p) for p in ("/usr/lib/x86_64-linux-gnu/rsyslog/lmnsd_ossl.so",
                                            "/usr/lib64/rsyslog/lmnsd_ossl.so", "/usr/lib/rsyslog/lmnsd_ossl.so"))
if not shutil.which("rsyslogd") or not have_ossl:
    if shutil.which("apt-get"):
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        r = subprocess.run(["apt-get", "install", "-y", "-qq", "rsyslog", "rsyslog-openssl"], capture_output=True, text=True, env=env)
        if r.returncode != 0:
            subprocess.run(["apt-get", "update", "-qq"], capture_output=True, env=env)
            r = subprocess.run(["apt-get", "install", "-y", "-qq", "rsyslog", "rsyslog-openssl"], capture_output=True, text=True, env=env)
    elif shutil.which("dnf"):
        r = subprocess.run(["dnf", "install", "-y", "-q", "rsyslog", "rsyslog-openssl"], capture_output=True, text=True)
    else:
        raise SystemExit("no apt-get or dnf to install rsyslog")
    if r.returncode != 0:
        raise SystemExit("installing rsyslog failed: " + (r.stderr or r.stdout)[-300:])
try:
    gid = grp.getgrnam("syslog").gr_gid
except KeyError:
    gid = 0
shutil.rmtree(legacy, ignore_errors=True)
os.makedirs(d, exist_ok=True)
os.chown(d, 0, gid)
os.chmod(d, 0o750)
for name, text, mode in (("root.crt", A["root"], 0o644), ("client.crt", A["cert"], 0o644), ("client.key", A["key"], 0o640)):
    path = os.path.join(d, name)
    with open(path + ".tmp", "w") as fh:
        fh.write(text)
    os.chown(path + ".tmp", 0, gid)
    os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)
with open(conf + ".tmp", "w") as fh:
    fh.write(A["conf"])
os.replace(conf + ".tmp", conf)
r = sh("rsyslogd", "-N1")
if r.returncode != 0:
    os.remove(conf)
    raise SystemExit("rsyslog rejected the forwarding settings: " + (r.stderr or r.stdout)[-400:])
sh("systemctl", "enable", "rsyslog")
r = sh("systemctl", "restart", "rsyslog")
if r.returncode != 0:
    raise SystemExit("restarting rsyslog failed: " + (r.stderr or r.stdout)[-300:])
print(json.dumps({"ok": True}))
'''


def forward_conf(address: str, host_name: str) -> str:
    """rsyslog settings that send everything to the stack collector over TLS with a client certificate."""
    import ipaddress
    try:
        ipaddress.ip_address(address)
        auth = 'StreamDriverAuthMode="x509/certvalid"'
    except ValueError:
        auth = f'StreamDriverAuthMode="x509/name" StreamDriverPermittedPeers="{address}"'
    return (f"# LABaPe (design §29.1): send {host_name}'s logs to the stack's syslog collector. Managed by LABaPe.\n"
            f'action(type="omfwd" target="{address}" port="6514" protocol="tcp" '
            'template="RSYSLOG_SyslogProtocol23Format" TCP_Framing="octet-counted" '
            f'StreamDriver="ossl" StreamDriverMode="1" {auth} '
            'StreamDriver.CAFile="/etc/rsyslog.d/labape-tls/root.crt" StreamDriver.CertFile="/etc/rsyslog.d/labape-tls/client.crt" '
            'StreamDriver.KeyFile="/etc/rsyslog.d/labape-tls/client.key" '
            'queue.type="LinkedList" queue.filename="labape_forward" queue.maxDiskSpace="256m" '
            'queue.saveOnShutdown="on" action.resumeRetryCount="-1" action.resumeInterval="30")\n')


def set_log_forwarding(db: Session, h: KvmHost, enable: bool) -> None:
    """Turn the host's log forwarding on (install rsyslog if needed, a client certificate from the
    platform CA, the forwarding file) or off. Errors are kept on the host and raised."""
    from .. import appsettings, ca, syslogstack
    try:
        ssh = for_host(h)
        args: dict = {"enable": enable}
        if enable:
            cfg = appsettings.get(db, "syslog_collector")
            address = syslogstack.collector_address(cfg)
            if not address:
                raise AdminError("the collector has no address: set one in Settings → Syslog collector")
            names = [h.name] + ([h.ssh_address] if h.ssh_address and h.ssh_address != h.name else [])
            key, csr = syslogstack.make_csr(h.name, names)
            chain = ca.sign(csr, hours=syslogstack.CERT_HOURS)
            args.update(root=ca.root_pem(), cert=chain, key=key.decode(), conf=forward_conf(address, h.name))
        ssh.python(FORWARD, args, timeout=300)
    except (AdminError, ca.CaError, subprocess.SubprocessError, OSError) as exc:
        h.log_forward_error = str(exc)[:1000]
        db.commit()
        raise AdminError(str(exc)) from exc
    h.log_forward, h.log_forward_error = enable, ""
    h.log_forward_until = utcnow() + dt.timedelta(hours=syslogstack.CERT_HOURS) if enable else None
    db.commit()


# --- preparing a new KVM host (design §28.4, question 114) -----------------------------------

# What the kvm profile reads on the host; nothing else is copied. Jobs bring their own
# workspace (tofu, ansible, packer, scripts) to ~/.labape/jobs/<id>, so the host needs only the
# installer and the examples its Config and Smoke steps start from.
INSTALL_FILES = ("deploy", "secrets.vault.example.yml", "tofu/environment.example.yml",
                 "ansible/software-manifest.example.yml", "ansible/directory-manifest.example.yml", "scripts/lib")
INSTALL_EXCLUDE = ("__pycache__", "deploy/state", "deploy/answers.*.json", "deploy/update-agent",
                   "deploy/labape-rollback.sh", "deploy/allowed_signers")
# What a re-run keeps from an earlier install: the installer's progress and the configuration it
# generated. Everything else is removed first (the copy brings back what's needed), so files from
# older, fuller copies don't linger.
INSTALL_KEEP = ("deploy/state", "deploy/logs", "deploy/answers.web.json", "secrets.vault.yml", "tofu/environment.yml",
                "ansible/software-manifest.yml", "ansible/directory-manifest.yml")
PRUNE = r'''
import os, shutil, sys
root, keep = sys.argv[1], set(sys.argv[2:])
def walk(rel):
    for name in sorted(os.listdir(os.path.join(root, rel) if rel else root)):
        path = f"{rel}/{name}" if rel else name
        if path in keep:
            continue
        if any(k.startswith(path + "/") for k in keep) and os.path.isdir(os.path.join(root, path)):
            walk(path)
            continue
        full = os.path.join(root, path)
        shutil.rmtree(full) if os.path.isdir(full) and not os.path.islink(full) else os.remove(full)
        print("removed", path)
if os.path.isdir(root):
    walk("")
'''
# One stable place on the host: the installer keeps its progress (deploy/state) and the host's
# vault and environment.yml there, and a re-run updates the code in place.
INSTALL_ROOT = "/opt/labape-kvm"


def service_user(h: KvmHost) -> str:
    """The user in a qemu+ssh libvirt URI: the one jobs and file operations run as."""
    from urllib.parse import urlsplit
    return urlsplit(h.libvirt_uri).username or ""


def ensure_file_roots(db: Session, h: KvmHost) -> list[str]:
    """The Files page's locations on a remote host, owned by its service user, so the stack can
    keep files there when this host is the primary (design §29.5). Through the access account
    (sudo); existing folders keep their contents. Returns the folders handled."""
    from ..files import roots
    user = service_user(h)
    if not user or h.access_status != "ready":
        return []
    q = shlex.quote
    paths = sorted(set(roots(db).values()))
    cmd = " && ".join(f"sudo -n install -d -m 0775 -o {q(user)} -g {q(user)} {q(p)} "
                      f"&& sudo -n chown {q(user)}:{q(user)} {q(p)}" for p in paths)
    r = for_host(h).run(cmd, timeout=120)
    if r.returncode != 0:
        raise AdminError(f"creating the file locations failed: {r.stderr.decode(errors='replace').strip()[-300:]}")
    return paths


def attach_networks(db: Session, h: KvmHost, answers: dict) -> list[str]:
    """After a successful Prepare, the catalog networks chosen in the form (question 127) are
    attached to the host on the bridges the installer made; existing attachments keep their own
    settings. Returns the names attached now."""
    from ..models import HostNetwork
    ids = answers.get("_NetworkIds") or {}
    bridges = {n["Name"]: n["Bridge"] for n in answers.get("Networks") or []}
    have = {hn.network_id for hn in db.scalars(select(HostNetwork).where(HostNetwork.kvm_host_id == h.id))}
    done = []
    for name, net_id in ids.items():
        if net_id in have or name not in bridges:
            continue
        db.add(HostNetwork(kvm_host_id=h.id, network_id=net_id, bridge=bridges[name],
                           is_default=name == answers.get("DefaultNetwork") and not have))
        done.append(name)
    db.commit()
    return done


def run_install(db: Session, job, popen=subprocess.Popen) -> int:
    """The `host.install` job: copy the installer to the host and run
    `install-labape.py --profile kvm` there as root, unattended, from the answers in the job's
    params (no secrets in them). The worker's libvirt key is authorized for the service user,
    so the host can then be used as a qemu+ssh KVM host."""
    from ..config import get_settings
    from .runner import _append
    h = db.get(KvmHost, job.kvm_host_id)
    log = lambda m: _append(job, m)                                         # noqa: E731
    if h is None:
        log("labape: no such KVM host\n")
        return 1
    try:
        ssh = for_host(h)
    except AdminError as exc:
        log(f"labape: {exc}\n")
        return 1
    answers = {**(job.params or {}).get("answers", {}), "Profile": "kvm", "InstallWebUi": False,
               "WorkerPublicKey": hostio.ensure_key(), "TemplatesToBuild": []}     # templates: web jobs
    root = INSTALL_ROOT
    engine = get_settings().engine_dir
    # The copy needs tar on the host; minimal RHEL-family images don't have it.
    tar_ok = ssh.run("command -v tar >/dev/null || sudo -n dnf install -y -q tar >/dev/null 2>&1 "
                     "|| sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tar >/dev/null 2>&1; "
                     "command -v tar >/dev/null", timeout=300)
    if tar_ok.returncode != 0:
        log("labape: the host has no tar and installing it failed (dnf or apt-get)\n")
        return 1
    pruned = ssh.run(f"sudo -n python3 - {root} {' '.join(INSTALL_KEEP)}", input=PRUNE.encode(), timeout=120)
    if pruned.returncode != 0:
        log(f"labape: tidying {root} failed: {pruned.stderr.decode(errors='replace').strip()[-300:]}\n")
        return 1
    # Only what this copy doesn't bring back is worth reporting.
    gone = [g.removeprefix("removed ") for g in pruned.stdout.decode(errors="replace").split("\n") if g]
    gone = [g for g in gone if not any(g == f or g.startswith(f + "/") or f.startswith(g + "/") for f in INSTALL_FILES)]
    if gone:
        log(f"labape: removed what older installs left in {root}: {', '.join(gone)}\n")
    log(f"labape: copying the installer to {h.ssh_user}@{h.ssh_address}:{root}\n")
    tar = popen(["tar", "-C", str(engine), *[f"--exclude={x}" for x in INSTALL_EXCLUDE], "-czf", "-",
                 *[d for d in INSTALL_FILES if (engine / d).exists()]], stdout=subprocess.PIPE)
    r = subprocess.run([*ssh.base(), f"sudo -n mkdir -p {root} && sudo -n tar -xzf - -C {root}"],
                       stdin=tar.stdout, capture_output=True, timeout=900, env=hostio.tool_env(), check=False)
    tar.stdout.close()
    tar.wait()
    if r.returncode != 0 or tar.returncode != 0:
        log(f"labape: copying failed: {r.stderr.decode(errors='replace').strip()[-400:]}\n")
        return 1
    put = ssh.run(f"sudo -n tee {root}/deploy/answers.web.json >/dev/null && sudo -n chmod 0600 {root}/deploy/answers.web.json",
                  input=json.dumps({k: v for k, v in answers.items() if k != "ForwardHostLogs"}, indent=2).encode())
    # The service user owns the repository copy, as on any LABaPe host: the installer runs
    # ansible-vault and the CLI checks as that user. Created here if it doesn't exist yet
    # (the same as the installer's Prereq.User).
    svc = shlex.quote(answers.get("ServiceUser") or "labape")
    own = ssh.run(f"(id -u {svc} >/dev/null 2>&1 || sudo -n useradd --create-home --shell /bin/bash {svc}) "
                  f"&& sudo -n chown -R {svc}:{svc} {root}")
    if own.returncode != 0:
        log(f"labape: handing {root} to the service user failed: {own.stderr.decode(errors='replace').strip()[-300:]}\n")
        return 1
    if put.returncode != 0:
        log("labape: writing the answer file failed\n")
        return 1
    cmd = (f"cd {root} && sudo -n env DEBIAN_FRONTEND=noninteractive python3 deploy/install-labape.py --profile kvm "
           f"--instance web --config-file deploy/answers.web.json --non-interactive")
    log(f"labape: running the installer (kvm profile) on {h.name}; this takes a while\n")
    from .runner import beat
    import time
    with open(job.log_path, "ab", buffering=0) as logf:
        p = popen([*ssh.base(), cmd], stdout=logf, stderr=subprocess.STDOUT, env=hostio.tool_env())
        while (rc := p.poll()) is None:
            if beat(job.id):               # heartbeat; True means Cancel was asked for
                p.terminate()
                log("labape: cancelled; the installer may still finish its current step on the host\n")
                return 1
            time.sleep(15)
    if rc != 0:
        log(f"labape: the installer failed (exit {rc}); fix the cause and run Prepare again (finished steps are skipped)\n")
        return rc
    svc = answers.get("ServiceUser") or "labape"
    h.libvirt_uri = f"qemu+ssh://{svc}@{h.ssh_address}{'' if (h.ssh_port or 22) == 22 else ':' + str(h.ssh_port)}/system"
    db.commit()
    attached = attach_networks(db, h, answers)
    if attached:
        log(f"labape: {h.name} now carries {', '.join(attached)} (Admin → Networks)\n")
    try:
        made = ensure_file_roots(db, h)
        if made:
            log(f"labape: file locations ready for {service_user(h)}: {', '.join(made)}\n")
    except AdminError as exc:
        log(f"labape: warning: {exc}\n")
    if answers.get("ForwardHostLogs", True):             # question 124: optional, default yes
        try:
            set_log_forwarding(db, h, True)
            log("labape: the host's logs now go to the stack's syslog collector (TLS, client certificate)\n")
        except AdminError as exc:
            log(f"labape: warning: forwarding the host's logs failed (turn it on later on the host's SSH access "
                f"section): {exc}\n")
    log(f"labape: done; libvirt URI set to {h.libvirt_uri}. Run Check on the host's Settings to confirm.\n")
    return 0
