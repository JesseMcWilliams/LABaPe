"""Console API and MCP (design §59): a round trip with a throwaway console token.

Usage: console.py <environment name> <linux vm> [<windows vm>]
Signs in as labape-admin, makes a 1-day console token limited to that environment, then: lists what it
can reach, runs `id; hostname`, opens a shell session and types into it, does the same through /mcp,
checks another environment is refused, and revokes the token. Only read-only commands are run. The
token's secret is never printed.
"""
import json
import sys
import time

import httpx

from oidc_login import LABAPE, login

env_name, vm = sys.argv[1], sys.argv[2]
win = sys.argv[3] if len(sys.argv) > 3 else ""
c = login("labape-admin")
envs = c.get(f"{LABAPE}/api/environments").json()
env = next(e for e in envs if e["name"] == env_name)
other = next((e for e in envs if e["id"] != env["id"]), None)
t = c.post(f"{LABAPE}/api/tokens", json={"name": "console-roundtrip", "roles": ["console"], "days": 1,
                                         "environments": [env["id"]], "require_account": False}).json()
h = {"Authorization": f"Bearer {t['secret']}"}
raw = httpx.Client(verify=False, timeout=130, headers=h)
base = f"{LABAPE}/api/environments/{env['id']}/vms/{vm}"
try:
    r = raw.get(f"{LABAPE}/api/console/environments").json()
    print("reach:", [(e["name"], [v["name"] for v in e["vms"]]) for e in r["environments"]])
    print("a browser-style call without the role (hosts):", raw.get(f"{LABAPE}/api/hosts").status_code)

    r = raw.post(f"{base}/exec", json={"command": "id; hostname"})
    print("exec:", r.status_code, r.json() if r.status_code != 200 else {k: v for k, v in r.json().items() if k != "session"})
    r = raw.post(f"{base}/exec", json={"command": "sleep 20", "timeout": 2}).json()
    print("timeout:", r.get("timed_out"), r.get("exit_code"))
    r = raw.post(f"{base}/exec", json={"command": "yes | head -c 2000000"}).json()
    print("output cap:", r.get("truncated"), len(r.get("stdout", "")))

    s = raw.post(f"{LABAPE}/api/console/sessions", json={"environment_id": env["id"], "vm": vm})
    print("session:", s.status_code)
    if s.status_code == 200:
        sid, pos, text = s.json()["id"], 0, ""
        raw.post(f"{LABAPE}/api/console/sessions/{sid}/input", json={"data": "echo from-session; hostname; exit\n"})
        for _ in range(10):
            o = raw.get(f"{LABAPE}/api/console/sessions/{sid}/output", params={"after": pos, "wait": 3}).json()
            pos, text = o["next"], text + o["data"]
            if not o["open"]:
                break
        print("session output has the echo:", "from-session" in text, "| closed:", not o["open"], "|", o["reason"])
        raw.delete(f"{LABAPE}/api/console/sessions/{sid}")

    r = raw.post(f"{LABAPE}/api/console/sessions", json={"environment_id": env["id"], "vm": vm, "kind": "serial"})
    print("serial console:", r.status_code, r.json().get("detail", "") if r.status_code != 200 else "opened")
    if r.status_code == 200:
        raw.delete(f"{LABAPE}/api/console/sessions/{r.json()['id']}")
    if win:
        r = raw.post(f"{LABAPE}/api/environments/{env['id']}/vms/{win}/exec", json={"command": "$env:COMPUTERNAME; whoami", "timeout": 60})
        print("windows exec:", r.status_code, r.json().get("stdout", r.json().get("detail", "")) if r.status_code else "")
        r = raw.post(f"{LABAPE}/api/console/sessions", json={"environment_id": env["id"], "vm": win})
        print("windows session (expect 400):", r.status_code)

    def mcp(method, params=None, i=1):
        return raw.post(f"{LABAPE}/mcp", json={"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}).json()
    print("mcp tools:", sorted(x["name"] for x in mcp("tools/list")["result"]["tools"]))
    r = mcp("tools/call", {"name": "run_command", "arguments": {"environment_id": env["id"], "vm": vm, "command": "hostname"}})
    print("mcp run_command:", r["result"].get("isError", False), json.loads(r["result"]["content"][0]["text"])["stdout"].strip())
    if other:
        r = raw.post(f"{LABAPE}/api/environments/{other['id']}/vms/x/exec", json={"command": "id"})
        print("another environment:", r.status_code)
finally:
    print("revoke:", c.delete(f"{LABAPE}/api/tokens/{t['id']}").json()["state"])
    time.sleep(0.2)
    print("after revoking:", raw.get(f"{LABAPE}/api/console/environments").status_code)
