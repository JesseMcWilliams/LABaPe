"""Check (and fix) a KVM host's folders for VM disks, templates and files.

A folder LABaPe uses on a host has to work for two users: the service user (it creates files there
through libvirt and the jobs) and the hypervisor's own user (`libvirt-qemu` on Debian and Ubuntu,
`qemu` on Red Hat), which opens the disks directly and so needs to reach them through every folder
above. The script below runs on the host as root, through the access account (sudo). With `fix` off it
only reports what it would change; with `fix` on it makes the changes, then checks again.

What it never does: touch the contents of a folder, change folders above a drive's mount point or in
a person's home, or change AppArmor (it reports denials and the rule to add instead).
"""
from __future__ import annotations


from ..models import KvmHost
from . import hostadmin

CATEGORY_DIRS = ("ISOs", "Installers", "Other")

SCRIPT = r'''
import grp, json, os, pwd, shutil, subprocess
USER = ARGS["user"]
SYSTEM_TOP = {"proc", "sys", "dev", "boot", "bin", "sbin", "lib", "lib64", "run", "tmp", "etc", "usr", "root", "snap"}
NO_FIX_TOP = SYSTEM_TOP | {"home", "var", "opt", "mnt", "media", "srv"}   # never chmod these on the way down

def user(name):
    try:
        p = pwd.getpwnam(name)
    except KeyError:
        return None
    return p.pw_uid, {p.pw_gid} | {g.gr_gid for g in grp.getgrall() if name in g.gr_mem}

def can(name, st, need):
    u = user(name)
    if u is None:
        return False
    uid, gids = u
    if uid == 0:
        return True
    bits = (st.st_mode >> 6) & 7 if st.st_uid == uid else (st.st_mode >> 3) & 7 if st.st_gid in gids else st.st_mode & 7
    return bits & need == need

def qemu_user():
    for n in ("libvirt-qemu", "qemu"):
        if user(n):
            return n
    return ""

def vm_group(qemu):
    try:
        grp.getgrnam("libvirt")
        return "libvirt"
    except KeyError:
        return grp.getgrgid(pwd.getpwnam(qemu).pw_gid).gr_name if qemu else USER

def mounts():
    out = []
    for line in open("/proc/mounts"):
        f = line.split()
        out.append((f[1].replace("\\040", " "), f[2], set(f[3].split(","))))
    return out

def mount_of(p):
    best = ("/", "", set())
    for t, fs, o in mounts():
        if (p == t or p.startswith(t.rstrip("/") + "/")) and len(t) >= len(best[0]):
            best = (t, fs, o)
    return best

def under(p, base):
    return p == base or p.startswith(base.rstrip("/") + "/")

def fstab_unmounted(p):
    """An /etc/fstab entry for this path (or above) that isn't mounted."""
    try:
        lines = open("/etc/fstab").read().splitlines()
    except OSError:
        return ""
    live = {t for t, _, _ in mounts()}
    for line in lines:
        f = line.split()
        if len(f) >= 4 and not f[0].startswith("#") and f[1].startswith("/") and f[1] != "/" and under(p, f[1]) \
                and f[1] not in live and "noauto" not in f[3]:
            return f[1]
    return ""

def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, (r.stderr or r.stdout).strip()[-200:]

def audit(spec):
    p, kind = spec["path"], spec["kind"]
    items, acts = [], []
    def add(level, text, *argvs):
        items.append({"level": level, "text": text})
        acts.extend(argvs)
    parts = [x for x in p.split("/") if x]
    if not parts or parts[0] in SYSTEM_TOP or ".." in parts:
        add("problem", f"{p} isn't a place for VM or LABaPe files")
        return items, acts
    qemu = qemu_user()
    if not qemu:
        add("problem", "the host has no libvirt-qemu or qemu user: is libvirt installed?")
        return items, acts
    group = USER if kind == "files" else vm_group(qemu)
    if user(USER) is None:
        add("problem", f"the service user {USER} doesn't exist on the host")
        return items, acts
    cur, existing, missing, chain = "/", "/", [], ["/"]
    for part in parts:
        cur = os.path.join(cur, part)
        if not missing and os.path.isdir(cur):
            existing = cur
            chain.append(cur)
        elif os.path.lexists(cur) and not missing:
            add("problem", f"{cur} exists but isn't a folder")
            return items, acts
        else:
            missing.append(cur)
    mp, fs, opts = mount_of(existing)
    if "ro" in opts:
        add("problem", f"{mp} is mounted read-only")
        return items, acts
    if mp == "/":
        add("info", "it is on the root filesystem, not a separate drive")
    else:
        add("ok", f"on {mp} ({fs})")
    # Every folder above must let the hypervisor's user through; fix the ones that belong to the drive.
    for d in chain[:-1] if existing == p else chain:
        if can(qemu, os.stat(d), 1):
            continue
        fixable = d != "/" and d.strip("/").split("/")[0] not in NO_FIX_TOP and (mp == "/" or under(d, mp))
        if fixable:
            add("fix", f"let {qemu} through {d} (add o+x)", ["chmod", "o+x", d])
        else:
            add("problem", f"{qemu} can't get through {d}, which this can't change: allow it (chmod o+x) or use another path")
    def want(path, label):
        """The service user writes there, the hypervisor user reads: else (re)own and chmod."""
        if not os.path.isdir(path):
            add("fix", f"create {path} ({USER}:{group}, 2775)", ["install", "-d", "-m", "2775", "-o", USER, "-g", group, path])
            return
        st = os.stat(path)
        if can(USER, st, 7) and can(qemu, st, 5):
            add("ok", f"{label}: {USER} writes there and {qemu} reads")
        else:
            add("fix", f"{label}: set owner {USER}:{group} and mode 2775 on {path}", ["chown", f"{USER}:{group}", path],
                ["chmod", "2775", path])
    for d in missing[:-1]:
        add("fix", f"create {d} (root, 0755)", ["install", "-d", "-m", "0755", d])
    want(p, "folder")
    if kind == "files":
        for sub in ARGS.get("subdirs", []):
            want(f"{p}/{sub}", sub)
    # Where the filesystem has an SELinux policy, VM disks need the virt_image_t label.
    if shutil.which("getenforce") and run(["getenforce"])[1] == "Enforcing" and kind != "files":
        have = run(["ls", "-dZ", p])[1] if os.path.isdir(p) else ""
        if "virt_image_t" not in have:
            add("fix", f"label {p} virt_image_t for SELinux", ["sh", "-c",
                f"semanage fcontext -a -t virt_image_t '{p}(/.*)?' 2>/dev/null || semanage fcontext -m -t virt_image_t '{p}(/.*)?'; "
                f"restorecon -R {p}"])
    try:
        enforcing = open("/sys/module/apparmor/parameters/enabled").read().strip() == "Y"
    except OSError:
        enforcing = False
    if enforcing and kind != "files":
        rc, log = run(["journalctl", "-k", "--no-pager", "-n", "5000", "-o", "cat"])
        hits = [x for x in log.splitlines() if 'apparmor="DENIED"' in x and f'name="{p}' in x] if rc == 0 else []
        if hits:
            add("problem", f"AppArmor denied access under {p}: add the line `{p}/** rwk,` to "
                           "/etc/apparmor.d/local/abstractions/libvirt-qemu, then run `sudo systemctl reload apparmor`")
        else:
            add("info", "AppArmor is enforcing; libvirt allows each VM's disks itself, and no denial under this path is logged")
    free = shutil.disk_usage(existing).free
    add("info", f"{free / 2**30:.0f} GiB free")
    return items, acts

out = []
for spec in ARGS["paths"]:
    items, acts = audit(spec)
    if ARGS["fix"]:
        mnt = fstab_unmounted(spec["path"])
        if mnt:
            rc, err = run(["mount", mnt])
            items = [{"level": "fixed" if rc == 0 else "problem",
                      "text": f"mount {mnt} (listed in /etc/fstab)" if rc == 0 else f"mount {mnt} failed: {err}"}]
            items2, acts = audit(spec)
            items += items2
        if acts:
            done = [i for i in items if i["level"] == "fix"]
            failed = ""
            for argv in acts:
                rc, err = run(argv)
                if rc != 0:
                    failed = f"{' '.join(argv[:2])} failed: {err}"
                    break
            after, _ = audit(spec)
            items = [{"level": "fixed", "text": i["text"]} for i in done] + [i for i in after if i["level"] != "fix"]
            if failed or any(i["level"] == "fix" for i in after):
                items.append({"level": "problem", "text": failed or "some changes didn't take effect"})
    else:
        mnt = fstab_unmounted(spec["path"])
        if mnt:
            items = [{"level": "fix", "text": f"mount {mnt} (listed in /etc/fstab but not mounted); check again after"}]
    out.append({"path": spec["path"], "kind": spec["kind"], "items": items,
                "ok": not any(i["level"] == "problem" for i in items),
                "changes": sum(1 for i in items if i["level"] in ("fix", "fixed"))})
print(json.dumps(out))
'''


def available(h: KvmHost) -> str:
    """"" when the host's folders can be checked over its access account, else why not."""
    if not hostadmin.service_user(h):
        return "this host isn't reached over SSH (qemu+ssh), so LABaPe can't look at its folders"
    if h.access_status != "ready":
        return "set up SSH access to this host first (Access section)"
    return ""


def specs(h: KvmHost, vm: str, templates: str, files: str = "") -> list[dict]:
    out = [{"path": vm.rstrip("/") or "/", "kind": "vm"}]
    if templates.rstrip("/") != vm.rstrip("/"):
        out.append({"path": templates.rstrip("/") or "/", "kind": "templates"})
    if files:
        out.append({"path": files.rstrip("/"), "kind": "files"})
    return out


def check(h: KvmHost, paths: list[dict], fix: bool = False) -> list[dict]:
    """One result per path: its items (level ok, info, fix, fixed, problem), `ok` (no problems) and
    `changes` (what would be changed, or was). Raises hostadmin.AdminError when the host can't be reached."""
    payload = {"user": hostadmin.service_user(h), "paths": paths, "fix": fix, "subdirs": list(CATEGORY_DIRS)}
    return hostadmin.for_host(h).python(SCRIPT, payload, timeout=300)
