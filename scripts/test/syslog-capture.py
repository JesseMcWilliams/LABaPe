#!/usr/bin/env python3
"""Ad hoc diagnostic tool, not part of the deploy pipeline. Listens for
kickstart's `logging --host=...` / rsyslog forwarding (README's Known
Gaps, M2) and appends every line to a file — a much more direct
troubleshooting channel than inferring install progress from VHD growth
or a Hyper-V uptime-counter reset.

Usage: syslog-capture.py [port] [output-file]
Defaults: port 1514 (not the standard 514 — that needs root/admin to
bind; kickstart's `logging --port=1514` and the rsyslog forwarding line
both need to agree with whatever's passed here), ./syslog-capture.log
"""
import socket
import sys
import time

port = int(sys.argv[1]) if len(sys.argv) > 1 else 1514
out_path = sys.argv[2] if len(sys.argv) > 2 else "syslog-capture.log"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", port))
print(f"labape: listening for syslog on UDP {port}, writing to {out_path}", file=sys.stderr)

with open(out_path, "a", buffering=1) as f:
    while True:
        data, addr = sock.recvfrom(65535)
        line = data.decode("utf-8", errors="replace").rstrip("\n")
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{addr[0]}] {line}\n")
