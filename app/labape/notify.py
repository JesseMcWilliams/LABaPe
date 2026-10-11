"""Outbound notifications (design §21.5): syslog for audit events, email
for job results. Both are best effort: a slow or broken syslog/SMTP server
never delays or fails the action that triggered it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import smtplib
import socket
import ssl
import threading
from email.message import EmailMessage

from urllib.parse import urlsplit

from .appsettings import SMTP_PASSWORD_SECRET, SmtpSettings, SyslogSettings
from .config import get_settings

log = logging.getLogger("labape.notify")

FACILITIES = {"user": 1, "daemon": 3, "auth": 4, **{f"local{i}": 16 + i for i in range(8)}}
SEVERITY = {"ok": 6, "denied": 4, "failed": 3}   # informational, warning, error


def format_rfc5424(cfg: SyslogSettings, event: dict) -> bytes:
    pri = FACILITIES[cfg.facility] * 8 + SEVERITY.get(event.get("outcome", "ok"), 6)
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    # The public host name, not the container's random one.
    host = (urlsplit(get_settings().public_url).hostname or socket.gethostname())[:255]
    msg = json.dumps(event, default=str, separators=(",", ":"))
    return f"<{pri}>1 {ts} {host} {cfg.app_name} - {event.get('action', 'audit')[:32]} - {msg}".encode()


def send_syslog(cfg: SyslogSettings, event: dict, timeout: float = 5.0) -> None:
    data = format_rfc5424(cfg, event)
    if cfg.protocol == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(data, (cfg.host, cfg.port))
        return
    raw = socket.create_connection((cfg.host, cfg.port), timeout=timeout)
    try:
        sock = raw
        if cfg.protocol == "tls":
            ctx = ssl.create_default_context()
            if not cfg.verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(raw, server_hostname=cfg.host)
        # RFC 6587 octet counting framing for stream transports.
        sock.sendall(f"{len(data)} ".encode() + data)
    finally:
        raw.close()


_syslog_q: queue.Queue = queue.Queue(maxsize=10000)
_syslog_thread: threading.Thread | None = None


def _syslog_worker() -> None:
    while True:
        cfg, event = _syslog_q.get()
        try:
            send_syslog(cfg, event)
        except OSError as exc:
            log.warning("syslog to %s:%s failed: %s", cfg.host, cfg.port, exc)


def audit_to_syslog(cfg: SyslogSettings, event: dict) -> None:
    """Queue an audit event; called after the audit row is committed."""
    global _syslog_thread
    if not cfg.enabled or not cfg.host:
        return
    if _syslog_thread is None or not _syslog_thread.is_alive():
        _syslog_thread = threading.Thread(target=_syslog_worker, name="syslog", daemon=True)
        _syslog_thread.start()
    try:
        _syslog_q.put_nowait((cfg, event))
    except queue.Full:
        log.warning("syslog queue full; dropping an audit event")


def send_email(cfg: SmtpSettings, password: str, to: list[str], subject: str, body: str, timeout: float = 20) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.from_address
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if cfg.security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
    try:
        if cfg.security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if cfg.username:
            server.login(cfg.username, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


def email_in_background(cfg: SmtpSettings, password: str, to: list[str], subject: str, body: str) -> None:
    def go():
        try:
            send_email(cfg, password, to, subject, body)
        except (OSError, smtplib.SMTPException) as exc:
            log.warning("email to %s failed: %s", to, exc)
    threading.Thread(target=go, name="email", daemon=True).start()


__all__ = ["SMTP_PASSWORD_SECRET", "audit_to_syslog", "email_in_background", "format_rfc5424", "send_email",
           "send_syslog"]
