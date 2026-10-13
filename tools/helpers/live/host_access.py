"""KVM host administration through the API (design §28.4).

Usage: host_access.py register NAME ADDRESS [SERVICE_USER]   (a disabled host record for ADDRESS)
       host_access.py setup HOST_ID ADDRESS ADMIN_USER          (the admin password on stdin; never printed)
       host_access.py show HOST_ID [dashboard|drives|nics|os|accounts|groups]
       host_access.py refresh HOST_ID
       host_access.py check HOST_ID                              (libvirt Check, then the SSH access check)
       host_access.py manage HOST_ID OP NAME [key=value ...]   (OP: user.add, group.member_add, ...)
       host_access.py install HOST_ID                           (the answers JSON on stdin; waits for the job)
       host_access.py reinstall HOST_ID                         (Prepare again with its last successful install's answers)
"""
import json
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
what, args = sys.argv[1], sys.argv[2:]
if what == "register":
    name, address = args[0], args[1]
    svc = args[2] if len(args) > 2 else "labape"
    r = c.post(f"{LABAPE}/api/hosts", json={"name": name, "libvirt_uri": f"qemu+ssh://{svc}@{address}/system",
                                            "enabled": False, "ssh_address": address})
    print(r.status_code, r.json().get("id"), r.text[:200] if r.status_code >= 300 else "")
elif what == "setup":
    pw = sys.stdin.read()
    r = c.post(f"{LABAPE}/api/hosts/{args[0]}/access",
               json={"address": args[1], "admin_user": args[2], "admin_password": pw})
    print("setup:", r.status_code, {k: r.json().get(k) for k in ("access_status", "ssh_user", "access_error")}
          if r.status_code == 200 else r.text[:400])
elif what == "check":
    print("libvirt check:", c.post(f"{LABAPE}/api/hosts/{args[0]}/check").json())
    print("ssh check:", {k: c.post(f"{LABAPE}/api/hosts/{args[0]}/access/check").json().get(k) for k in ("access_status", "access_error")})
elif what == "refresh":
    print(c.post(f"{LABAPE}/api/hosts/{args[0]}/admin/refresh").text[:300])
elif what == "show":
    v = c.get(f"{LABAPE}/api/hosts/{args[0]}/admin").json()
    sec = args[1] if len(args) > 1 else "dashboard"
    f = v["facts"] or {}
    print("collected", v["collected_at"], "smart", v["smart_at"], "error", v["error"] or "-")
    if sec == "dashboard":
        print(" os:", {k: f.get("os", {}).get(k) for k in ("name", "kernel", "cpus", "memory_mb", "updates", "reboot_required")})
        print(" warnings:", [w["message"] for w in v["warnings"]])
        print(" latest:", {k: (v["latest"] or {}).get(k) for k in ("load", "fs_used_pct", "temps")})
        print(" series points:", len(v["series"]["t"]))
    elif sec == "drives":
        print(" disks:", [(d["name"], d["type"], d.get("size")) for d in f.get("disks", [])])
        print(" fs:", [(x["mount"], x["type"], round(x["used"] * 100 / x["size"]) if x["size"] else 0) for x in f.get("filesystems", [])])
        print(" smart:", v["smart"].get("available"), [(d["device"], d.get("passed")) for d in v["smart"].get("disks", [])])
        print(" lvm vgs:", [g.get("vg_name") for g in (f.get("lvm") or {}).get("vgs", [])])
    elif sec == "nics":
        print(" nics:", [(n["name"], n["kind"], n["state"], n.get("speed_mbps"), n["addresses"]) for n in f.get("nics", [])])
    elif sec == "os":
        print(" os:", json.dumps(f.get("os", {}))[:600])
    elif sec in ("accounts", "groups"):
        rows = f.get(sec, [])
        print(f" {sec}:", [(x["name"], x.get("groups", x.get("members"))) for x in rows])
elif what == "manage":
    host, op, name = args[0], args[1], args[2]
    extra = dict(a.split("=", 1) for a in args[3:])
    if "groups" in extra:
        extra["groups"] = extra["groups"].split(",")
    r = c.post(f"{LABAPE}/api/hosts/{host}/accounts", json={"op": op, "name": name, **extra})
    print(op, name, r.status_code, r.text[:300] if r.status_code != 200 else "ok")
elif what in ("install", "reinstall"):
    if what == "install":
        answers = json.loads(sys.stdin.read())
    else:
        done = [j for j in c.get(f"{LABAPE}/api/jobs?type=host.install&state=succeeded&limit=200").json()
                if j.get("kvm_host_id") == int(args[0])]
        if not done:
            raise SystemExit(f"host {args[0]} has no successful install to reuse")
        answers = c.get(f"{LABAPE}/api/jobs/{done[0]['id']}").json()["params"]["answers"]
        print(f"reusing the answers of job {done[0]['id']}")
    r = c.post(f"{LABAPE}/api/hosts/{args[0]}/install", json=answers)
    if r.status_code != 200:
        raise SystemExit(f"refused: {r.status_code} {r.text[:400]}")
    job, t0 = r.json()["job_id"], time.time()
    while (j := c.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
        time.sleep(10)
    print(f"job {job} {j['state']} in {time.time() - t0:.0f}s")
    lines = job_log(c, job).splitlines()
    for line in [x for x in lines if x.startswith(("labape:", "==", "[", "FAIL", "ERROR"))][-40:]:
        print("  ", line[:200])
