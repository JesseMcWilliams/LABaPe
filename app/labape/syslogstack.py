"""The stack's syslog collector (design §29.1, questions 121-139).

`labape-syslog` (rsyslog) receives on UDP/TCP 514 and TLS 6514 and writes
every message to `labape-syslog-db`, a PostgreSQL that listens only on a
Unix socket shared through a volume. LABaPe's side, run by the worker every
minute (`tick`):

- the table and its grants (LABaPe's role owns the database; rsyslog's role
  may only insert);
- the certificates, from the platform CA: the collector's server certificate
  and LABaPe's client certificate (the audit trail and TLS forwards present
  it), renewed when 10 days are left;
- the rsyslog settings LABaPe owns, rendered into a volume the collector
  watches: forwarding targets (e.g. a SIEM) and whether TLS clients must
  present a certificate.

The API reads messages for the Syslog tab (`query`); the worker purges rows
older than the retention (`purge`).
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import appsettings, ca
from .appsettings import FACILITY_NAMES, SEVERITY_NAMES, SyslogCollectorSettings, SyslogForward
from .config import get_settings
from .models import Setting

log = logging.getLogger("labape.syslog")

COLLECTOR = "labape-syslog"                   # its name on the stack network
SOCKET = Path("/run/syslog-db/.s.PGSQL.5432")
RENEW_DAYS = 10
CERT_HOURS = 2160                             # 90 days, step-ca's maximum here
FACILITY_NUM = {name: i for i, name in enumerate(FACILITY_NAMES)}
SEVERITY_NUM = {name: i for i, name in enumerate(SEVERITY_NAMES)}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS syslog_messages (
         id bigserial PRIMARY KEY,
         received_at timestamptz NOT NULL,
         reported_at timestamptz,
         from_ip text NOT NULL DEFAULT '',
         host text NOT NULL DEFAULT '',
         facility smallint NOT NULL,
         severity smallint NOT NULL,
         app_name text NOT NULL DEFAULT '',
         procid text NOT NULL DEFAULT '',
         msgid text NOT NULL DEFAULT '',
         structured_data text NOT NULL DEFAULT '',
         message text NOT NULL DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS syslog_messages_received ON syslog_messages (received_at)",
    "CREATE INDEX IF NOT EXISTS syslog_messages_host ON syslog_messages (host, id)",
    "CREATE INDEX IF NOT EXISTS syslog_messages_severity ON syslog_messages (severity, id)",
    "GRANT INSERT ON syslog_messages TO rsyslog_writer",
    "GRANT USAGE ON SEQUENCE syslog_messages_id_seq TO rsyslog_writer",
]

STATUS_KEY = "syslog_collector.status"        # the worker's last tick, for the API
status: dict = {"schema": False, "certs": "", "rendered": "", "error": ""}
_engine: Engine | None = None


def present() -> bool:
    """The collector's database is running here: its socket exists (the volume is mounted even when
    the installer left the collector out, design §29.2)."""
    return SOCKET.exists()


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().syslog_db_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _engine


def ensure_schema(eng: Engine | None = None) -> None:
    with (eng or engine()).begin() as c:
        for stmt in SCHEMA:
            c.execute(text(stmt))
    status["schema"] = True


# --- the rsyslog settings LABaPe owns --------------------------------------------------------

def _q(v: str) -> str:
    """A RainerScript string value (the settings' patterns already keep quotes out)."""
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def forward_action(i: int, f: SyslogForward) -> str:
    params = [f'type="omfwd"', f'name={_q(f"forward_{i}")}', f"target={_q(f.host)}", f'port="{f.port}"',
              f'protocol="{"udp" if f.transport == "udp" else "tcp"}"',
              f'template="{"RSYSLOG_SyslogProtocol23Format" if f.format == "rfc5424" else "RSYSLOG_ForwardFormat"}"']
    if f.transport != "udp" and f.format == "rfc5424":
        params.append('TCP_Framing="octet-counted"')
    if f.transport == "tls":
        params += ['StreamDriver="ossl"', 'StreamDriverMode="1"', 'StreamDriver.CAFile="/run/labape-syslog/ca-bundle.pem"']
        if not f.verify_tls:
            params.append('StreamDriverAuthMode="anon"')
        else:
            try:
                ipaddress.ip_address(f.host)
                params.append('StreamDriverAuthMode="x509/certvalid"')     # an address: the chain only
            except ValueError:
                params += ['StreamDriverAuthMode="x509/name"', f"StreamDriverPermittedPeers={_q(f.host)}"]
        if f.client_cert:
            params += ['StreamDriver.CertFile="/tls/client.crt"', 'StreamDriver.KeyFile="/tls/client.key"']
    # Its own disk-assisted queue: a SIEM that's down doesn't hold up the database or the others.
    params += [f'queue.type="LinkedList"', f'queue.filename="forward_{i}"', 'queue.maxDiskSpace="512m"',
               'queue.saveOnShutdown="on"', 'action.resumeRetryCount="-1"', 'action.resumeInterval="30"']
    action = "action(" + " ".join(params) + ")"
    cond = []
    if f.facilities:
        cond.append(f'prifilt("{",".join(f.facilities)}.*")')
    if f.min_severity != "debug":
        cond.append(f"$syslogseverity <= {SEVERITY_NUM[f.min_severity]}")
    return f"if {' and '.join(cond)} then {{ {action} }}" if cond else action


def render_files(cfg: SyslogCollectorSettings) -> dict[str, str | None]:
    """File name -> content (None: the file shouldn't exist)."""
    lines = [f"# {f.name}\n{forward_action(i, f)}" for i, f in enumerate(cfg.forwards) if f.enabled]
    return {
        "forward.conf": ("# Written by LABaPe (Admin → Settings → Syslog collector); don't edit.\n"
                         + "\n".join(lines) + "\n") if lines else None,
        "client-certs-optional": None if cfg.require_client_cert else "TLS clients needn't present a certificate\n",
    }


def render(cfg: SyslogCollectorSettings, conf_dir: Path | None = None) -> bool:
    """Write the files that differ; True when anything changed (the collector restarts)."""
    d = conf_dir or get_settings().syslog_conf_dir
    if not d.is_dir():
        return False
    changed = False
    for name, content in render_files(cfg).items():
        path = d / name
        if content is None:
            if path.exists():
                path.unlink()
                changed = True
        elif not path.is_file() or path.read_text(encoding="utf-8") != content:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
            changed = True
    if changed:
        status["rendered"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return changed


# --- certificates ----------------------------------------------------------------------------

def collector_address(cfg: SyslogCollectorSettings) -> str:
    return cfg.address or urlsplit(get_settings().public_url).hostname or ""


def _expires(path: Path) -> dt.datetime | None:
    from cryptography import x509
    try:
        return x509.load_pem_x509_certificate(path.read_bytes()).not_valid_after_utc
    except (OSError, ValueError):
        return None


def _names(path: Path) -> set[str]:
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return {*san.get_values_for_type(x509.DNSName), *(str(i) for i in san.get_values_for_type(x509.IPAddress))}
    except (OSError, ValueError, x509.ExtensionNotFound):
        return set()


def make_csr(cn: str, names: list[str]) -> tuple[bytes, str]:
    """(PEM private key, PEM CSR) for an EC P-256 key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for n in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            sans.append(x509.DNSName(n))
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
           .add_extension(x509.SubjectAlternativeName(sans), critical=False)
           .sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return pem_key, csr.public_bytes(serialization.Encoding.PEM).decode()


def _issue(d: Path, stem: str, cn: str, names: list[str]) -> None:
    key, csr = make_csr(cn, names)
    chain = ca.sign(csr, hours=CERT_HOURS)
    for suffix, data, mode in ((".key", key, 0o600), (".crt", chain.encode(), 0o644)):
        tmp = d / f"{stem}{suffix}.tmp"
        tmp.write_bytes(data)
        tmp.chmod(mode)
        tmp.replace(d / f"{stem}{suffix}")


def ensure_certs(cfg: SyslogCollectorSettings, tls_dir: Path | None = None) -> list[str]:
    """Issue or renew the collector's and LABaPe's certificates; returns what was issued."""
    d = tls_dir or get_settings().syslog_tls_dir
    if not d.is_dir() or not ca.configured():
        return []
    root = ca.root_pem()
    if not (d / "root.crt").is_file() or (d / "root.crt").read_text(encoding="ascii") != root:
        (d / "root.crt").write_text(root, encoding="ascii")
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=RENEW_DAYS)
    server_names = sorted({COLLECTOR, "localhost", *(n for n in [collector_address(cfg)] if n)})
    issued = []
    for stem, cn, names in (("server", COLLECTOR, server_names), ("client", "labape", ["labape"])):
        exp = _expires(d / f"{stem}.crt")
        if exp is None or exp < soon or (stem == "server" and _names(d / "server.crt") != set(server_names)):
            _issue(d, stem, cn, names)
            issued.append(stem)
    if issued:
        log.info("syslog collector: issued %s certificate(s)", ", ".join(issued))
    status["certs"] = ", ".join(f"{s} until {(_expires(d / f'{s}.crt') or '').__str__()[:10]}" for s in ("server", "client"))
    return issued


# --- the worker's part -----------------------------------------------------------------------

def tick(db: Session) -> None:
    """Every minute in the worker: schema, certificates, rendered settings. Errors are kept
    for the Syslog tab's status line, never raised."""
    if not present():
        return
    cfg = appsettings.get(db, "syslog_collector")
    adopt_audit_trail(db)
    try:
        if not status["schema"]:
            ensure_schema()
        ensure_certs(cfg)
        render(cfg)
        status["error"] = ""
    except Exception as exc:  # noqa: BLE001 — the collector is best effort for the rest of LABaPe
        status["error"] = str(exc)[:500]
        log.warning("syslog collector: %s", exc)
    row = db.get(Setting, STATUS_KEY)
    value = {**status, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if row is None:
        db.add(Setting(key=STATUS_KEY, value=value))
    else:
        row.value = value
    db.commit()


def adopt_audit_trail(db: Session) -> bool:
    """An install whose audit syslog was never configured sends it to its own collector
    (question 121: the audit trail goes there from the first start). Saved settings, even
    "off", are left alone. True when it was set now."""
    if db.get(Setting, "app.syslog") is not None:
        return False
    appsettings.put(db, "syslog", appsettings.SyslogSettings(enabled=True, host=COLLECTOR, port=6514, protocol="tls",
                                                              client_cert=True))
    log.info("audit trail: sending to the stack's syslog collector (%s:6514, TLS)", COLLECTOR)
    return True


def last_status(db: Session) -> dict:
    row = db.get(Setting, STATUS_KEY)
    return dict(row.value) if row else {}


def purge(db: Session) -> int:
    if not present():
        return 0
    days = appsettings.get(db, "syslog_collector").retention_days
    with engine().begin() as c:
        r = c.execute(text("DELETE FROM syslog_messages WHERE received_at < now() - make_interval(days => :d)"),
                      {"d": days})
    return r.rowcount or 0


# --- the Syslog tab --------------------------------------------------------------------------

def query(eng: Engine | None = None, *, since: dt.datetime | None = None, until: dt.datetime | None = None,
          host: str = "", from_ip: str = "", facility: str = "", min_severity: str = "", app: str = "",
          q: str = "", before_id: int | None = None, after_id: int | None = None, limit: int = 200) -> list[dict]:
    where, args = [], {"limit": max(1, min(limit, 1000))}
    if since:
        where.append("received_at >= :since"); args["since"] = since
    if until:
        where.append("received_at <= :until"); args["until"] = until
    if host:
        where.append("host ILIKE :host"); args["host"] = f"%{host}%"
    if from_ip:
        where.append("from_ip LIKE :ip"); args["ip"] = f"{from_ip}%"
    if facility:
        if facility not in FACILITY_NUM:
            raise ValueError(f"unknown facility {facility}")
        where.append("facility = :fac"); args["fac"] = FACILITY_NUM[facility]
    if min_severity:
        if min_severity not in SEVERITY_NUM:
            raise ValueError(f"unknown severity {min_severity}")
        where.append("severity <= :sev"); args["sev"] = SEVERITY_NUM[min_severity]
    if app:
        where.append("app_name ILIKE :app"); args["app"] = f"%{app}%"
    if q:
        where.append("message ILIKE :q"); args["q"] = f"%{q}%"
    if before_id:
        where.append("id < :before"); args["before"] = before_id
    if after_id:
        where.append("id > :after"); args["after"] = after_id
    sql = ("SELECT id, received_at, reported_at, from_ip, host, facility, severity, app_name, procid, msgid, "
           "structured_data, message FROM syslog_messages"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT :limit")
    with (eng or engine()).connect() as c:
        rows = c.execute(text(sql), args).mappings().all()
    return [{**r, "facility": FACILITY_NAMES[r["facility"]] if r["facility"] < len(FACILITY_NAMES) else str(r["facility"]),
             "severity": SEVERITY_NAMES[r["severity"]] if r["severity"] < len(SEVERITY_NAMES) else str(r["severity"])}
            for r in rows]


def summary(eng: Engine | None = None) -> dict:
    """About how many rows (PostgreSQL's estimate: counting a large table is slow), the oldest and newest."""
    with (eng or engine()).connect() as c:
        r = c.execute(text(
            "SELECT (SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE relname = 'syslog_messages') AS rows,"
            " (SELECT received_at FROM syslog_messages ORDER BY received_at LIMIT 1) AS oldest,"
            " (SELECT received_at FROM syslog_messages ORDER BY id DESC LIMIT 1) AS newest")).mappings().one()
    return dict(r)
