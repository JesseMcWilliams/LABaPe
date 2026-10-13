"""The stack's syslog collector (design §29.1): status, messages, test senders, settings.

Usage: syslog_stack.py status
       syslog_stack.py messages [filter=value ...]       host, from_ip, facility, min_severity, app, q, since, limit
       syslog_stack.py send ADDRESS                      one tagged message each over UDP 514, TCP 514, and TLS 6514
                                                         without a client certificate; prints the tag
       syslog_stack.py settings [retention=N] [certs=required|optional] [address=A]
                                [forward=NAME,HOST,PORT,udp|tcp|tls[,rfc3164]] [noforward]
Signs in as labape-admin. Check what arrived with `messages q=<tag>`.
"""
import datetime as dt
import socket
import ssl
import sys
import uuid

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd, args = sys.argv[1], sys.argv[2:]


def msg(tag: str, how: str) -> bytes:
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return f"<134>1 {ts} live-test labape-live - - - {tag} via {how}".encode()    # local0.info


if cmd == "status":
    print(c.get(f"{LABAPE}/api/syslog/status").json())
elif cmd == "messages":
    q = dict(a.split("=", 1) for a in args)
    r = c.get(f"{LABAPE}/api/syslog/messages", params={"limit": 20, **q})
    if r.status_code != 200:
        raise SystemExit(f"{r.status_code} {r.text[:300]}")
    for m in r.json():
        print(f"  #{m['id']} {m['received_at'][:19]} {m['from_ip']:15} {m['host'][:20]:20} {m['facility']}.{m['severity']:7} "
              f"{m['app_name'][:16]:16} {m['message'][:110]}")
elif cmd == "send":
    addr, tag = args[0], f"lt-{uuid.uuid4().hex[:8]}"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(msg(tag, "udp"), (addr, 514))
    with socket.create_connection((addr, 514), timeout=10) as s:
        d = msg(tag, "tcp")
        s.sendall(f"{len(d)} ".encode() + d)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with socket.create_connection((addr, 6514), timeout=10) as raw, ctx.wrap_socket(raw) as s:
            d = msg(tag, "tls-no-client-cert")
            s.sendall(f"{len(d)} ".encode() + d)
            s.settimeout(3)
            try:
                s.recv(1)                     # a server that refuses the client closes here
            except (TimeoutError, socket.timeout):
                pass
        print("tls without a client certificate: sent (the server didn't refuse the handshake)")
    except (ssl.SSLError, OSError) as exc:
        print(f"tls without a client certificate: refused ({type(exc).__name__}: {str(exc)[:100]})")
    print("tag:", tag)
elif cmd == "forwardtest":
    # A listener here (host network) on ADDRESS:5514, a forward to it, one tagged message to the
    # collector, then wait for the forwarded copy and remove the forward again.
    import threading
    import time
    addr, mode = args[0], args[1]
    port, got, tag = 5514, [], f"fw-{uuid.uuid4().hex[:8]}"
    ready = threading.Event()

    def listen():
        if mode == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("0.0.0.0", port)); s.settimeout(1); ready.set()
            end = time.time() + 90
            while time.time() < end and not got:
                try:
                    data = s.recv(65536)
                    if tag.encode() in data:
                        got.append(data.decode(errors="replace"))
                except (TimeoutError, socket.timeout):
                    pass
            return
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port)); srv.listen(5); srv.settimeout(1)
        ctx = None
        if mode == "tls":
            import tempfile
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "live-test")])
            now = dt.datetime.now(dt.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(hours=1)).sign(key, hashes.SHA256()))
            d = tempfile.mkdtemp()
            open(f"{d}/c.pem", "wb").write(cert.public_bytes(serialization.Encoding.PEM))
            open(f"{d}/k.pem", "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                             serialization.NoEncryption()))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
        ready.set()
        end, buf = time.time() + 90, b""
        while time.time() < end and not got:
            try:
                conn, _ = srv.accept()
            except (TimeoutError, socket.timeout):
                continue
            try:
                if ctx:
                    conn = ctx.wrap_socket(conn, server_side=True)
                conn.settimeout(2)
                while time.time() < end and not got:
                    try:
                        data = conn.recv(65536)
                    except (TimeoutError, socket.timeout):
                        continue
                    if not data:
                        break
                    buf += data
                    if tag.encode() in buf:
                        got.append(buf.decode(errors="replace")[-300:])
            except (ssl.SSLError, OSError) as exc:
                print("listener:", exc)

    t = threading.Thread(target=listen, daemon=True); t.start(); ready.wait(10)
    cur = c.get(f"{LABAPE}/api/admin/settings").json()["syslog_collector"]
    fwd = {"name": "live-test", "host": addr, "port": port, "transport": mode, "verify_tls": False,
           "app_name": "", "facilities": ["local0"], "min_severity": "info"}
    r = c.put(f"{LABAPE}/api/admin/settings/syslog_collector", json={**cur, "forwards": [*cur["forwards"], fwd]})
    print("forward saved:", r.status_code, "" if r.status_code == 200 else r.text[:300])
    time.sleep(25)                                    # the collector notices and restarts within ~10 s
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(msg(tag, "udp, to be forwarded"), (addr, 514))
    t.join(70)
    print(f"forwarded over {mode}:", "yes: " + got[0].strip()[-160:] if got else "NOT received")
    c.put(f"{LABAPE}/api/admin/settings/syslog_collector",
          json={**cur, "forwards": [f for f in cur["forwards"] if f["name"] != "live-test"]})
elif cmd == "settings":
    cur = c.get(f"{LABAPE}/api/admin/settings").json()["syslog_collector"]
    for a in args:
        if a == "noforward":
            cur["forwards"] = []
        else:
            k, v = a.split("=", 1)
            if k == "retention":
                cur["retention_days"] = int(v)
            elif k == "certs":
                cur["require_client_cert"] = v == "required"
            elif k == "address":
                cur["address"] = v
            elif k == "forward":
                p = v.split(",")
                cur["forwards"].append({"name": p[0], "host": p[1], "port": int(p[2]), "transport": p[3],
                                        "format": p[4] if len(p) > 4 else "rfc5424", "verify_tls": True})
    r = c.put(f"{LABAPE}/api/admin/settings/syslog_collector", json=cur)
    print(r.status_code, r.json() if r.status_code == 200 else r.text[:300])
