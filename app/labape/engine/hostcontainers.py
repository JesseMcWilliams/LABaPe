"""The Containers page (plan C1, Planning_Container-Management.md): what a host's Docker or Podman holds.

A script run over the SSH access account (as root, like the host facts) reads containers, images and volumes
in one shape for both runtimes; `build` turns the stored values into the page. Logs and live stats are read on
request and never stored. Nothing here changes a container: actions are batch C2.
"""
from __future__ import annotations

import datetime as dt
import re
import shlex
import subprocess

from sqlalchemy.orm import Session

from sqlalchemy import delete, select

from ..models import ContainerSample, HostContainers, KvmHost, utcnow
from . import hostadmin

COLLECT_EVERY = dt.timedelta(minutes=5)
SAMPLES_KEEP = dt.timedelta(days=7)                 # plan A1: how long CPU and memory readings are kept
VOLUME_SIZES_EVERY = dt.timedelta(hours=1)          # `system df -v` walks every volume: hourly only
RESTART_WINDOW = dt.timedelta(minutes=30)
# The Stack's own compose projects: shown, never actionable (plan §0.3).
STACK_PROJECTS = ("labape", "authentik-test")
LOG_MAX_LINES, LOG_MAX_BYTES = 2000, 1_000_000
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CLEAN_EXIT = (0, 137, 143)                           # finished, or stopped by `stop`/SIGTERM/SIGKILL

SCRIPT = r'''
import json, shutil, subprocess
P = []                      # a prefix that runs the command as another user (rootless Podman)
def sh(*a, timeout=120):
    try:
        r = subprocess.run([*P, *a], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""
def lines(text):
    out = []
    text = text.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except ValueError:
            return []
    for ln in text.splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out
def gather(rt, owner):
    out = {"version": "", "containers": [], "images": [], "volumes": []}
    out["version"] = sh(rt, "version", "--format", "{{.Server.Version}}" if rt == "docker" else "{{.Version}}").strip()
    names = sh(rt, "ps", "-a", "--format", "{{.Names}}").split()
    stats = {}
    for s in lines(sh(rt, "stats", "--no-stream", "--format", "{{json .}}", timeout=60)):
        stats[s.get("Name") or s.get("name") or ""] = {"cpu": s.get("CPUPerc") or s.get("cpu_percent") or "",
                                                       "memory": s.get("MemUsage") or s.get("mem_usage") or "",
                                                       "memory_pct": s.get("MemPerc") or s.get("mem_percent") or ""}
    for c in (json.loads(sh(rt, "inspect", *names) or "[]") if names else []):
        st = c.get("State") or {}
        cfg = c.get("Config") or {}
        host = c.get("HostConfig") or {}
        labels = cfg.get("Labels") or {}
        ports = []
        for cport, binds in ((c.get("NetworkSettings") or {}).get("Ports") or {}).items():
            for b in binds or [None]:
                ports.append(f"{b.get('HostIp') or '0.0.0.0'}:{b.get('HostPort')}->{cport}" if b else cport)
        name = c.get("Name", "").lstrip("/")
        out["containers"].append({
            "name": name, "id": (c.get("Id") or "")[:12], "image": cfg.get("Image", ""), "image_id": (c.get("Image") or "")[:19],
            "project": labels.get("com.docker.compose.project", ""), "service": labels.get("com.docker.compose.service", ""),
            "state": st.get("Status", ""), "health": (st.get("Health") or {}).get("Status", ""),
            "exit_code": st.get("ExitCode"), "oom": bool(st.get("OOMKilled")), "error": (st.get("Error") or "")[:300],
            "owner": owner, "started_at": st.get("StartedAt", ""), "finished_at": st.get("FinishedAt", ""), "created": c.get("Created", ""),
            "restarts": c.get("RestartCount", 0), "restart_policy": (host.get("RestartPolicy") or {}).get("Name", ""),
            "ports": sorted(set(ports)),
            "mounts": [{"type": m.get("Type", ""), "source": m.get("Name") or m.get("Source", ""),
                        "target": m.get("Destination", ""), "rw": bool(m.get("RW", True))} for m in c.get("Mounts") or []],
            "networks": sorted((c.get("NetworkSettings") or {}).get("Networks") or {}),
            "quadlet": bool(labels.get("PODMAN_SYSTEMD_UNIT")), "unit": labels.get("PODMAN_SYSTEMD_UNIT", ""),
            "compose_dir": labels.get("com.docker.compose.project.working_dir", ""),
            "compose_files": labels.get("com.docker.compose.project.config_files", ""), "stats": stats.get(name)})
    for i in lines(sh(rt, "images", "-a", "--format", "{{json .}}")):
        out["images"].append({"id": (i.get("ID") or i.get("Id") or "")[:12],
                              "repository": i.get("Repository") or "", "tag": i.get("Tag") or "",
                              "owner": owner, "size": i.get("Size") or "", "created": i.get("CreatedSince") or i.get("CreatedAt") or ""})
    dangling = set(sh(rt, "images", "-q", "-f", "dangling=true").split())
    for i in out["images"]:
        i["dangling"] = i["id"] in dangling or (i["repository"] in ("<none>", "") and i["tag"] in ("<none>", ""))
    sizes = {}
    if ARGS.get("volume_sizes") and rt == "docker":
        for ln in sh(rt, "system", "df", "-v", "--format", "{{json .}}", timeout=600).splitlines():
            try:
                for v in json.loads(ln).get("Volumes") or []:
                    sizes[v.get("Name", "")] = v.get("Size", "")
            except ValueError:
                pass
    for v in lines(sh(rt, "volume", "ls", "--format", "{{json .}}")):
        lab = v.get("Labels") or ""
        proj = next((p.split("=", 1)[1] for p in (lab.split(",") if isinstance(lab, str) else []) if p.startswith("com.docker.compose.project=")), "")
        out["volumes"].append({"name": v.get("Name", ""), "driver": v.get("Driver", ""), "owner": owner, "size": sizes.get(v.get("Name", "")), "project": proj})
    return out

rt = "docker" if shutil.which("docker") else ("podman" if shutil.which("podman") else "")
out = {"runtime": rt, "version": "", "containers": [], "images": [], "volumes": [], "warnings": []}
if rt:
    out.update(gather(rt, ""))
user = ARGS.get("rootless_user")
if user and shutil.which("podman"):
    import pwd
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        pw = None
    if pw is not None and pw.pw_uid != 0:
        P[:] = ["runuser", "-u", user, "--", "env", "XDG_RUNTIME_DIR=/run/user/%d" % pw.pw_uid, "HOME=" + pw.pw_dir]
        g = gather("podman", user)
        if g["containers"] or g["images"] or g["volumes"]:
            out["rootless"] = {"user": user, "uid": pw.pw_uid, "version": g["version"]}
            for k in ("containers", "images", "volumes"):
                out[k] += g[k]
            out["runtime"] = out["runtime"] or "podman"
        P[:] = []
print(json.dumps(out))
'''


def rootless_user(h: KvmHost) -> str:
    """The account whose own (rootless) Podman is read as well (plan A2): the access account, when it isn't root."""
    u = (h.ssh_user or "").strip()
    return u if u and u != "root" and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", u) else ""


def run_as(row: HostContainers | None, c: dict) -> list[str]:
    """The prefix that runs a runtime command as the container's owner: nothing for root's, `runuser` for a rootless one."""
    if not c.get("owner"):
        return []
    info = ((row.data if row else {}) or {}).get("rootless") or {}
    if info.get("user") != c["owner"] or not isinstance(info.get("uid"), int):
        raise hostadmin.AdminError("this rootless container's owner is unknown (refresh the page)")
    return ["runuser", "-u", c["owner"], "--", "env", f"XDG_RUNTIME_DIR=/run/user/{info['uid']}"]


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t.replace(tzinfo=dt.timezone.utc) if t is not None and t.tzinfo is None else t


def collect(db: Session, h: KvmHost, now: dt.datetime | None = None) -> HostContainers:
    """Read the host's containers now. A host without Docker or Podman is stored as runtime "" (no section)."""
    now = now or utcnow()
    row = db.get(HostContainers, h.id) or HostContainers(kvm_host_id=h.id, data={})
    old = dict(row.data or {})
    try:
        sizes = not old.get("volume_sizes_at") or now - dt.datetime.fromisoformat(old["volume_sizes_at"]) >= VOLUME_SIZES_EVERY
        got = hostadmin.for_host(h).python(SCRIPT, {"volume_sizes": sizes, "rootless_user": rootless_user(h)}, timeout=900)
    except (hostadmin.AdminError, subprocess.TimeoutExpired, ValueError) as exc:
        row.error = str(exc)[:1000]
        db.merge(row)
        db.commit()
        return row
    before = {c["name"]: c for c in old.get("containers") or []}
    seen = dict(old.get("restart_seen") or {})
    for c in got.get("containers", []):
        prev = before.get(c["name"])
        if prev is not None and c["restarts"] > prev.get("restarts", 0):
            seen[c["name"]] = now.isoformat()
    if not sizes:                                              # keep the last sizes between the hourly reads
        known = {v["name"]: v.get("size") for v in old.get("volumes") or []}
        for v in got.get("volumes", []):
            v["size"] = known.get(v["name"])
    got.update(restart_seen=seen, volume_sizes_at=now.isoformat() if sizes else old.get("volume_sizes_at"), updates=old.get("updates"))
    row.data, row.collected_at, row.error = got, now, ""
    db.merge(row)
    db.commit()
    record_samples(db, h.id, got, now)
    return row


def parse_percent(text) -> float | None:
    """'12.34%' -> 12.34; None when it can't be read (a stopped container has no stats)."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*%?\s*", str(text or ""))
    return float(m.group(1)) if m else None


def record_samples(db: Session, host_id: int, got: dict, now: dt.datetime) -> int:
    """Keep each container's CPU and memory from this read, and drop what is older than a week."""
    n = 0
    for c in got.get("containers") or []:
        st = c.get("stats")
        if not st:
            continue
        cpu = parse_percent(st.get("cpu"))
        mem = parse_size(str(st.get("memory") or "").split("/")[0]) or None        # "512MiB / 2GiB": the used part
        if cpu is None and mem is None:
            continue
        db.add(ContainerSample(kvm_host_id=host_id, name=c["name"], at=now, cpu_pct=cpu, mem_bytes=mem))
        n += 1
    db.execute(delete(ContainerSample).where(ContainerSample.kvm_host_id == host_id, ContainerSample.at < now - SAMPLES_KEEP))
    db.commit()
    return n


def history(db: Session, host_id: int, name: str, hours: int, now: dt.datetime | None = None, cap: int = 700) -> list[dict]:
    """The readings of one container over the last `hours`, oldest first, thinned to at most `cap` points."""
    now = now or utcnow()
    rows = db.scalars(select(ContainerSample).where(
        ContainerSample.kvm_host_id == host_id, ContainerSample.name == name,
        ContainerSample.at >= now - dt.timedelta(hours=hours)).order_by(ContainerSample.at)).all()
    step = max(1, -(-len(rows) // cap))
    return [{"t": int(_aware(r.at).timestamp() * 1000), "cpu": r.cpu_pct, "memory": r.mem_bytes} for r in rows[::step]]


def collect_due(db: Session, h: KvmHost, now: dt.datetime | None = None) -> None:
    now = now or utcnow()
    row = db.get(HostContainers, h.id)
    if row is None or row.collected_at is None or now - _aware(row.collected_at) >= COLLECT_EVERY - dt.timedelta(seconds=20):
        collect(db, h, now)


def attention(c: dict) -> str:
    """Why a container needs a look, else "". A container that was stopped on purpose (exit 0, or the
    exit code `stop` leaves) is not a problem; one that is restarting, dead, unhealthy or was killed is."""
    if c.get("state") in ("restarting", "dead"):
        return f"is {c['state']}"
    if c.get("health") == "unhealthy" and c.get("state") == "running":      # a stopped container keeps its last health
        return "is unhealthy"
    if c.get("state") == "exited" and (c.get("oom") or c.get("exit_code") not in CLEAN_EXIT):
        return "ran out of memory and was killed" if c.get("oom") else f"exited with code {c.get('exit_code')}"
    return ""


def is_stack_own(c: dict) -> bool:
    return c.get("project") in STACK_PROJECTS


def summary(row: HostContainers | None) -> dict:
    """What the host's page needs to know before opening the section: is there a runtime, and how many need a look."""
    data = (row.data if row else {}) or {}
    cs = data.get("containers") or []
    return {"runtime": data.get("runtime", ""), "total": len(cs), "attention": sum(bool(attention(c)) for c in cs)}


# --- the guarded actions (plan C2) -----------------------------------------------------------------

LEVELS = ("off", "known", "new", "all")
OPS = ("start", "stop", "restart", "recreate")
SAFE_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/@+-]{0,255}$")
SAFE_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}\.service$")


def _created(c: dict) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(c.get("created", ""))[:19]).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def actionable(h: KvmHost | None, c: dict) -> tuple[bool, str]:
    """May the page act on this container, and if not, why. The Stack's own are never actionable."""
    if is_stack_own(c):
        return False, "part of this LABaPe stack"
    if c.get("owner"):
        return False, f"a rootless container of {c['owner']}: shown here, not changed from here"
    level = h.container_level if h is not None else "off"
    if level == "all":
        return True, ""
    if level == "off" or h is None:
        return False, "actions are off for this host"
    if c["name"] in (h.container_known or []):
        return True, ""
    if level == "new":
        made, since = _created(c), _aware(h.container_level_at)
        if made is not None and since is not None and made > since:
            return True, ""
        return False, "not marked known, and it existed before the level was set"
    return False, "not marked known"


def ops_for(runtime: str, c: dict) -> list[str]:
    """The actions that make sense for the container as it is now."""
    state = c.get("state")
    ops = []
    if state in ("exited", "created"):
        ops.append("start")
    if state in ("running", "restarting"):
        ops.append("stop")
    if state in ("running", "restarting", "exited", "created"):
        ops.append("restart")
    if runtime == "docker" and c.get("project") and c.get("service") and c.get("compose_dir") and not c.get("quadlet"):
        ops.append("recreate")
    return ops


def action_script(runtime: str, c: dict, op: str) -> str:
    """The shell script for one action on one container; every word in it was checked here."""
    name = c["name"]
    if op not in OPS:
        raise hostadmin.AdminError("not an action")
    if not SAFE_WORD.match(name):
        raise hostadmin.AdminError("not a container name")
    if c.get("quadlet") and c.get("unit"):
        # A Quadlet container belongs to a systemd unit: acting on the container directly, systemd would undo it.
        if op == "recreate":
            raise hostadmin.AdminError("a Quadlet container is recreated by restarting its unit")
        if not SAFE_UNIT.match(c["unit"]):
            raise hostadmin.AdminError("the container's systemd unit has an unexpected name")
        return f"systemctl {op} {shlex.quote(c['unit'])}"
    if op != "recreate":
        return f"{runtime} {op} {shlex.quote(name)}"
    if runtime != "docker":
        raise hostadmin.AdminError("recreate needs docker compose")
    project, service, folder = c.get("project", ""), c.get("service", ""), c.get("compose_dir", "")
    files = [f for f in (c.get("compose_files") or "").split(",") if f]
    if not (SAFE_WORD.match(project) and SAFE_WORD.match(service) and SAFE_PATH.match(folder) and all(SAFE_PATH.match(f) for f in files)):
        raise hostadmin.AdminError("this container's compose labels can't be used safely")
    base = f"docker compose -p {shlex.quote(project)} " + "".join(f"-f {shlex.quote(f)} " for f in files)
    return f"set -e; cd {shlex.quote(folder)}; {base}pull {shlex.quote(service)}; {base}up -d --no-deps {shlex.quote(service)}"


def run_action(ssh, h: KvmHost, row: HostContainers | None, name: str, op: str) -> str:
    """Do `op` on the container `name` of host `h`, after every check; returns the tail of the output."""
    c = _known(row, name)
    ok, why = actionable(h, c)
    if not ok:
        raise hostadmin.AdminError(f"{name} can't be changed from here: {why}")
    if op not in ops_for(c["runtime"], c):
        raise hostadmin.AdminError(f"{op} doesn't apply to {name} as it is now ({c.get('state')})")
    r = ssh.run(f"sudo -n bash -c {shlex.quote(action_script(c['runtime'], c, op))}", timeout=900 if op == "recreate" else 120)
    text = (r.stdout + r.stderr).decode(errors="replace").strip()
    if r.returncode != 0:
        raise hostadmin.AdminError(text[-400:] or f"exit {r.returncode}")
    return text[-400:]


def build(row: HostContainers | None, h: KvmHost | None = None) -> dict:
    """The page: containers with their flags, images and volumes (with what uses them), and a summary."""
    data = (row.data if row else {}) or {}
    containers = []
    used_vols: dict[str, list[str]] = {}
    for c in data.get("containers") or []:
        for m in c.get("mounts") or []:
            if m.get("type") == "volume":
                used_vols.setdefault((c.get("owner", ""), m["source"]), []).append(c["name"])
        ok, why = actionable(h, c)
        containers.append({**c, "attention": attention(c), "stack_own": is_stack_own(c), "actionable": ok, "why_not": why,
                           "ops": ops_for(data.get("runtime", ""), c) if ok else [],
                           "image_update": ((data.get("updates") or {}).get("images") or {}).get(c.get("image"), "")})
    images = [{**i, "in_use": any(i["id"] and c["image_id"].removeprefix("sha256:").startswith(i["id"]) and c.get("owner", "") == i.get("owner", "")
                                  for c in containers)}
              for i in data.get("images") or []]
    volumes = [{**v, "used_by": sorted(used_vols.get((v.get("owner", ""), v["name"]), []))} for v in data.get("volumes") or []]
    return {"runtime": data.get("runtime", ""), "version": data.get("version", ""),
            "collected_at": row.collected_at if row else None, "error": row.error if row else "",
            "level": h.container_level if h is not None else "off", "known": list(h.container_known or []) if h is not None else [],
            "updates": {"enabled": bool(h.container_updates) if h is not None else False, "at": (data.get("updates") or {}).get("at"),
                        "errors": (data.get("updates") or {}).get("errors") or {}},
            "rootless": (data.get("rootless") or {}).get("user", ""),
            "containers": containers, "images": images, "volumes": volumes,
            "summary": {"total": len(containers), "running": sum(c["state"] == "running" for c in containers),
                        "attention": sum(bool(c["attention"]) for c in containers),
                        "newer_images": sum(c["image_update"] == "newer" for c in containers)}}


def alerts(h: KvmHost, row: HostContainers | None, now: dt.datetime, stack: bool, reclaim_gb: int = 0) -> list[tuple[str, str, str]]:
    """(level, message, kind) for the host's containers that are wrong now. The Stack's own containers
    are watched by stackmon, so a stack host's compose projects are skipped here."""
    if row is None or not h.container_alerts or not (row.data or {}).get("runtime"):
        return []
    out = []
    seen = (row.data or {}).get("restart_seen") or {}
    for c in row.data.get("containers") or []:
        if stack and is_stack_own(c):
            continue
        why = attention(c)
        if why:
            out.append(("warning" if c.get("state") == "exited" or c.get("health") == "unhealthy" else "error",
                        f"container {c['name']} {why}", "container"))
        t = seen.get(c["name"])
        if t and now - _aware(dt.datetime.fromisoformat(t)) < RESTART_WINDOW:
            out.append(("warning", f"container {c['name']} restarted (restart count {c.get('restarts')})", "restart"))
    if reclaim_gb:
        # Images nothing uses (sizes are per image and layers are shared, so what is freed is less than this).
        cand = cleanup_candidates(build(row, h))["totals"]
        gb = (cand["dangling_images"]["bytes"] + cand["unused_images"]["bytes"]) / 1e9
        if gb >= reclaim_gb:
            out.append(("warning", f"about {gb:.0f} GB of container images nothing uses can be cleaned up (Containers → Clean up…)", "reclaim"))
    return out


# --- clean-up (plan C3): a preview first, then only what was picked ---------------------------------

UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}
IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$")
IMAGE_ID = re.compile(r"^[0-9a-f]{12}$")


def parse_size(text) -> int:
    """'190MB', '2.338kB', '1.2GiB' -> bytes; 0 when it can't be read."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]*)\s*", str(text or ""))
    return int(float(m.group(1)) * UNITS.get(m.group(2).upper() or "B", 0)) if m else 0


def cleanup_candidates(page: dict) -> dict:
    """What could be removed from a built page: stopped containers the level allows, images nothing uses (untagged
    ones apart from tagged ones), and volumes no container refers to, never the Stack's own. Docker itself also
    refuses to remove anything in use, since nothing here forces it."""
    out = {"containers": [], "dangling_images": [], "unused_images": [], "volumes": []}
    for c in page["containers"]:
        if c["state"] in ("exited", "created", "dead") and c["actionable"]:
            out["containers"].append({"key": c["name"], "label": c["name"], "bytes": 0,
                                      "detail": f"{c['state']}{'' if c.get('exit_code') in (None, 0) else f', exit code {c['exit_code']}'}, {c['image']}"})
    for i in page["images"]:
        if i["in_use"] or i.get("owner"):                    # a rootless user's images are theirs: shown, never offered
            continue
        size = parse_size(i["size"])
        if i["dangling"]:
            out["dangling_images"].append({"key": i["id"], "label": i["id"], "bytes": size, "detail": f"untagged, {i['size']}, {i['created']}"})
        else:
            ref = f"{i['repository']}:{i['tag']}"
            out["unused_images"].append({"key": ref, "label": ref, "bytes": size, "detail": f"{i['size']}, {i['created']}"})
    for v in page["volumes"]:
        if not v["used_by"] and v.get("project") not in STACK_PROJECTS and not v.get("owner"):
            out["volumes"].append({"key": v["name"], "label": v["name"], "bytes": parse_size(v["size"]),
                                   "detail": f"{v['size'] or 'size not read yet'}, no container uses it"})
    out["totals"] = {k: {"count": len(out[k]), "bytes": sum(x["bytes"] for x in out[k])} for k in ("containers", "dangling_images", "unused_images", "volumes")}
    return out


def cleanup_script(runtime: str, kind: str, keys: list[str]) -> str:
    """One removal per item, each reported; no force flag, so the runtime refuses anything still in use."""
    if kind == "containers":
        verb, ok = f"{runtime} rm", lambda k: bool(SAFE_WORD.match(k))
    elif kind in ("dangling_images", "unused_images"):
        verb, ok = f"{runtime} rmi", lambda k: bool(IMAGE_ID.match(k) if kind == "dangling_images" else IMAGE_REF.match(k))
    elif kind == "volumes":
        verb, ok = f"{runtime} volume rm", lambda k: bool(SAFE_WORD.match(k))
    else:
        raise hostadmin.AdminError("not a kind of clean-up")
    if not keys or not all(ok(k) for k in keys):
        raise hostadmin.AdminError("nothing to remove, or a name that can't be used safely")
    return "\n".join(f"{verb} {shlex.quote(k)} >/dev/null 2>&1 && echo {shlex.quote('removed ' + k)} || echo {shlex.quote('kept ' + k)}"
                     for k in keys)


def run_cleanup(ssh, h: KvmHost, row: HostContainers | None, kind: str, keys: list[str], confirm: str = "") -> dict:
    if h.container_level == "off":
        raise hostadmin.AdminError("changes are off for this host: choose what the page may change first")
    page = build(row, h)
    if not page["runtime"]:
        raise hostadmin.AdminError("this host has no container runtime")
    wanted = {x["key"] for x in cleanup_candidates(page).get(kind, [])} if kind in ("containers", "dangling_images", "unused_images", "volumes") else set()
    if not set(keys) <= wanted:
        raise hostadmin.AdminError("something picked isn't a candidate any more (refresh the page)")
    if kind == "volumes" and confirm != "delete":
        raise hostadmin.AdminError("removing volumes deletes their data: type delete to confirm")
    r = ssh.run(f"sudo -n bash -c {shlex.quote(cleanup_script(page['runtime'], kind, keys))}", timeout=600)
    lines = r.stdout.decode(errors="replace").splitlines()
    if r.returncode != 0 and not lines:
        raise hostadmin.AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or f"exit {r.returncode}")
    return {"removed": [x[8:] for x in lines if x.startswith("removed ")], "kept": [x[5:] for x in lines if x.startswith("kept ")]}


# --- on request: logs and live stats (never stored) ------------------------------------------------

SECRET_KEY = re.compile(r"(?i)\b([A-Z0-9_.-]*(?:PASSWORD|PASSWD|TOKEN|SECRET|KEY)[A-Z0-9_.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")


def mask_secrets(text: str) -> str:
    """Hide the value of `NAME_PASSWORD=...`, `token: ...` and the like (plan question 206)."""
    return SECRET_KEY.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def _known(row: HostContainers | None, name: str, runtime: bool = True) -> dict:
    if not NAME.match(name or ""):
        raise hostadmin.AdminError("not a container name")
    data = (row.data if row else {}) or {}
    c = next((x for x in data.get("containers") or [] if x["name"] == name), None)
    if c is None or not data.get("runtime"):
        raise hostadmin.AdminError("no such container on this host (refresh the page)")
    return {"runtime": "podman" if c.get("owner") else data["runtime"], **c}        # a user's rootless containers are Podman


def logs(ssh, row: HostContainers | None, name: str, tail: int = 200, since: str = "", needle: str = "") -> dict:
    """The container's recent log lines, capped and with secrets masked; `since` is like "10m" or "2h"."""
    c = _known(row, name)
    tail = max(1, min(int(tail), LOG_MAX_LINES))
    if since and not re.fullmatch(r"[0-9]{1,4}[smhd]", since):
        raise hostadmin.AdminError("since is a number and s, m, h or d (10m, 2h)")
    if since.endswith("d"):
        since = f"{int(since[:-1]) * 24}h"                  # docker and podman take no "d"
    cmd = [*run_as(row, c), c["runtime"], "logs", "--timestamps", "--tail", str(tail)] + (["--since", since] if since else []) + [name]
    r = ssh.run("sudo -n " + " ".join(shlex.quote(x) for x in cmd) + " 2>&1", timeout=60)
    text = r.stdout.decode(errors="replace")
    if r.returncode != 0:
        raise hostadmin.AdminError(text.strip()[-300:] or f"exit {r.returncode}")
    lines = text.splitlines()
    if needle:
        lines = [ln for ln in lines if needle.lower() in ln.lower()]
    out = "\n".join(lines)
    truncated = len(out.encode()) > LOG_MAX_BYTES
    if truncated:
        out = out.encode()[-LOG_MAX_BYTES:].decode(errors="replace")
    return {"text": mask_secrets(out), "lines": len(lines), "truncated": truncated}


def live_stats(ssh, row: HostContainers | None, name: str) -> dict:
    """One fresh CPU and memory reading for one container."""
    c = _known(row, name)
    cmd = ["sudo", "-n", *run_as(row, c), c["runtime"], "stats", "--no-stream", "--format", "{{json .}}", name]
    r = ssh.run(" ".join(shlex.quote(x) for x in cmd), timeout=60)
    if r.returncode != 0:
        raise hostadmin.AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or f"exit {r.returncode}")
    import json
    try:
        s = json.loads(r.stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise hostadmin.AdminError("the runtime returned no reading") from exc
    return {"cpu": s.get("CPUPerc") or s.get("cpu_percent") or "", "memory": s.get("MemUsage") or s.get("mem_usage") or "",
            "memory_pct": s.get("MemPerc") or s.get("mem_percent") or "", "pids": s.get("PIDs") or s.get("pids") or "",
            "net": s.get("NetIO") or s.get("net_io") or "", "block": s.get("BlockIO") or s.get("block_io") or ""}
