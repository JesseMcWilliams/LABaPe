"""An environment spread over two KVM hosts (design §45) through the API.

Usage: spread.py create NAME HOME_HOST_ID OTHER_HOST_ID [TEMPLATE]   (one Debian VM on each; waits for the deploy)
       spread.py vms ENV_ID                                         (each VM's host and state)
       spread.py power ENV_ID ACTION                                (start|shutdown|restart|force_off; waits)
       spread.py job ENV_ID deploy|destroy|refresh                   (waits; prints the part lines)
       spread.py move ENV_ID GROUP HOST_ID                          (live-migrates the group, waits for the move and its deploy)
Signs in as labape-admin.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
what, args = sys.argv[1], sys.argv[2:]


def wait(job: int) -> None:
    t0 = time.time()
    while (j := c.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
        time.sleep(10)
    print(f"job {job} {j['state']} in {time.time() - t0:.0f}s")
    for line in job_log(c, job).splitlines():
        if line.startswith(("labape: job", "labape: spread", "labape: part", "labape: running on", "PLAY RECAP",
                            "labape: could", "labape: failed", "Error", "fatal")) or " : ok=" in line:
            print("  ", line[:220])


if what == "create":
    name, home, other = args[0], int(args[1]), int(args[2])
    tpl = args[3] if len(args) > 3 else "debian13-packer-2026.10"
    g = {"os": "debian_latest", "roles": ["linux_server"], "image_source": "packer_template", "template": tpl,
         "cpu_count": 2, "memory_mb": 2048, "disk_gb": 40, "network": "lab-vlan48", "addressing": "static"}
    r = c.post(f"{LABAPE}/api/environments", json={"name": name, "kvm_host_id": home, "host_groups": [
        {**g, "name": "sph"}, {**g, "name": "spo", "kvm_host_id": other}]})
    if r.status_code != 201:
        raise SystemExit(f"refused: {r.status_code} {r.text[:400]}")
    env = r.json()["environment"]
    print("environment", env["id"], env["name"], [(x["name"], x.get("kvm_host_id")) for x in env["spec"]["host_groups"]])
    wait(r.json()["job_id"])
    print("addresses:", c.get(f"{LABAPE}/api/environments/{env['id']}").json()["addresses"])
elif what == "vms":
    for v in c.get(f"{LABAPE}/api/environments/{args[0]}/vms").json()["vms"]:
        print(f"  {v['name']} on {v.get('kvm_host')} ({v.get('kvm_host_id')}): {v['state']}")
    for v in c.get(f"{LABAPE}/api/environments/{args[0]}/vms").json()["vms"]:
        d = c.get(f"{LABAPE}/api/environments/{args[0]}/vms/{v['name']}").json()
        print(f"  details {v['name']}: host {d.get('kvm_host')}, address {d.get('address')}, "
              f"consoles host_id {d['consoles'].get('host_id')}")
elif what == "power":
    r = c.post(f"{LABAPE}/api/environments/{args[0]}/power", json={"action": args[1]})
    if r.status_code != 200:
        raise SystemExit(f"refused: {r.status_code} {r.text[:300]}")
    wait(r.json()["job_id"])
elif what == "move":                         # spread.py move ENV_ID GROUP HOST_ID: the move, then its deploy
    r = c.post(f"{LABAPE}/api/environments/{args[0]}/move", json={"group": args[1], "kvm_host_id": int(args[2])})
    if r.status_code != 200:
        raise SystemExit(f"refused: {r.status_code} {r.text[:300]}")
    job = r.json()["job_id"]
    wait(job)
    print("\n".join(x for x in job_log(c, job).splitlines() if x.startswith("labape:"))[-1500:])
    follow = [j for j in c.get(f"{LABAPE}/api/jobs?type=environment.deploy&limit=20").json()
              if (j.get("params") or {}).get("after_move") == job or j["id"] > job and j.get("environment_id") == int(args[0])]
    if follow:
        wait(follow[-1]["id"])
elif what == "log":                          # spread.py log JOB_ID [LINES]: the end of a job's log
    print("\n".join(job_log(c, args[0]).splitlines()[-int(args[1] if len(args) > 1 else 40):]))
elif what == "job":
    path = {"deploy": "deploy", "destroy": "destroy", "refresh": "refresh-addresses"}[args[1]]
    r = c.post(f"{LABAPE}/api/environments/{args[0]}/{path}")
    if r.status_code != 200:
        raise SystemExit(f"refused: {r.status_code} {r.text[:300]}")
    wait(r.json()["job_id"])
