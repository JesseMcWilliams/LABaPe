"""Open a browser-style remote session through the API and the tunnel (design §28.5).

Usage: remote_session.py vm ENV_ID VM_NAME ssh|rdp|serial|vnc [--as ACCOUNT] [--seconds N]
       remote_session.py host HOST_ID ssh [--seconds N]
       remote_session.py record on|off
       remote_session.py sessions
Signs in as labape-admin, asks for a session, connects to /api/remote/tunnel the way
guacamole-common-js does (session cookie, LABaPe's origin), and reports what guacd
sent: opcodes and counts, or the error. Sends no keystrokes; disconnects after N seconds.
--as signs in as one of the environment's accounts (E2): its secret's name, e.g. account/jesse.
"""
import asyncio
import collections
import ssl
import sys
from urllib.parse import urlsplit

import websockets

from oidc_login import LABAPE, login
from labape import remote as guac

c = login("labape-admin")
args = sys.argv[1:]
if args[0] == "record":
    cur = c.get(f"{LABAPE}/api/admin/settings").json()["remote"]
    print(c.put(f"{LABAPE}/api/admin/settings/remote", json={**cur, "record_sessions": args[1] == "on"}).json())
    sys.exit(0)
if args[0] == "sessions":
    for s in c.get(f"{LABAPE}/api/remote/sessions?limit=10").json():
        print(f"  #{s['id']} {s['username']} {s['target']} {s['protocol']} {s['state']} recorded={s['recorded']} "
              f"file={s['has_recording']} {s['error'][:80]}")
    sys.exit(0)
seconds = int(args[args.index("--seconds") + 1]) if "--seconds" in args else 8
if args[0] == "vm":
    body = {"target_type": "vm", "environment_id": int(args[1]), "vm": args[2], "protocol": args[3]}
    if "--as" in args:
        body["account"] = args[args.index("--as") + 1]
else:
    body = {"target_type": "host", "kvm_host_id": int(args[1]), "protocol": args[2]}
r = c.post(f"{LABAPE}/api/remote/sessions", json=body)
if r.status_code != 200:
    raise SystemExit(f"refused: {r.status_code} {r.text[:300]}")
sess = r.json()
print(f"session #{sess['id']} to {sess['target']} ({sess['protocol']}), recorded={sess['recorded']}")
u = urlsplit(LABAPE)
cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())


async def go():
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    url = f"wss://{u.netloc}/api/remote/tunnel?token={sess['token']}&width=1024&height=768&dpi=96"
    seen, errors, buf = collections.Counter(), [], ""
    async with websockets.connect(url, ssl=ctx, subprotocols=["guacamole"], max_size=None,
                                  additional_headers={"Cookie": cookie, "Origin": f"{u.scheme}://{u.netloc}"}) as ws:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), max(end - loop.time(), 0.1))
            except asyncio.TimeoutError:
                break
            buf += msg if isinstance(msg, str) else msg.decode()
            instrs, buf = guac.parse(buf)
            for i in instrs:
                seen[i[0] or "(internal)"] += 1
                if i[0] == "error":
                    errors.append(i[1:])
                if i[0] == "sync":                          # acknowledge frames, as the browser does
                    await ws.send(guac.encode("sync", i[1]))
        await ws.send(guac.encode("disconnect"))
    print("received:", dict(seen.most_common(12)))
    print("errors:", errors or "none")

asyncio.run(go())
