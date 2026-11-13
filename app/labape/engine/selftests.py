"""SMART self-test schedules (plan: Planning_SMART-Self-Test-Schedule.md, design §76).

smartd runs the tests; LABaPe sets the schedule through the installer's `Prereq.SmartTests` and reads what a host
really has from its `smartd.conf` (the collector's `smartd` report). A stack default (the `selftests` settings
section) applies to every host unless the host has an override: Setting `selftests.host<id>`:
`{"mode": "default" | "custom" | "off", <schedule fields>, "skip": [devices], "warn": true | false | null}`.
Hours are the host's local time (smartd's own clock); `smartd["now"]` is the host's local time at collection.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from ..models import Setting

KEY = "selftests.host{}"
STALE_HOURS = 14 * 24          # no self-test in two weeks of running is worth a look
SCHEDULE_FIELDS = ("short_on", "short_weekday", "short_hour", "long_on", "long_day", "long_hour")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_SHORT = re.compile(r"S/\.\./\.\./([1-7])/(\d\d)")
_LONG = re.compile(r"L/\.\./(\d\d)/\./(\d\d)")


def override(db: Session, host_id: int) -> dict:
    row = db.get(Setting, KEY.format(host_id))
    return dict(row.value) if row else {}


def save_override(db: Session, host_id: int, value: dict) -> None:
    row = db.get(Setting, KEY.format(host_id))
    if row is None:
        db.add(Setting(key=KEY.format(host_id), value=value))
    else:
        row.value = value
    db.commit()


def effective(db: Session, host_id: int | None) -> dict:
    """What applies to the host: the stack default, with the host's override on top."""
    from .. import appsettings
    eff = appsettings.get(db, "selftests").model_dump()
    ov = override(db, host_id) if host_id is not None else {}
    mode = ov.get("mode") or "default"
    if mode == "custom":
        eff.update({k: ov[k] for k in SCHEDULE_FIELDS if k in ov})
    elif mode == "off":
        eff["run"] = False
    if ov.get("warn") is not None:
        eff["warn"] = bool(ov["warn"])
    return {**eff, "mode": mode, "skip": list(ov.get("skip") or [])}


def expression(eff: dict) -> str:
    """smartd's -s regular expression for the policy ('' when no tests are wanted)."""
    if not eff.get("run", True):
        return ""
    parts = []
    if eff.get("short_on", True):
        parts.append(f"S/../../{int(eff['short_weekday'])}/{int(eff['short_hour']):02d}")
    if eff.get("long_on", True):
        parts.append(f"L/../{int(eff['long_day']):02d}/./{int(eff['long_hour']):02d}")
    return "|".join(parts)


def parse_expression(expr: str | None) -> dict:
    """The short and long entries of an -s expression written in LABaPe's forms; `other` is true when the
    expression has anything else in it (a schedule written by hand), which is then shown as it is."""
    out: dict = {"short": None, "long": None, "other": False, "raw": expr or ""}
    for part in (expr or "").strip("()").split("|"):
        if not part:
            continue
        m, n = _SHORT.fullmatch(part), _LONG.fullmatch(part)
        if m:
            out["short"] = {"weekday": int(m.group(1)), "hour": int(m.group(2))}
        elif n:
            out["long"] = {"day": int(n.group(1)), "hour": int(n.group(2))}
        else:
            out["other"] = True
    return out


def describe(expr: str | None) -> str:
    p = parse_expression(expr)
    if not expr:
        return "no scheduled self-tests"
    bits = []
    if p["short"]:
        bits.append(f"short: every {WEEKDAYS[p['short']['weekday'] - 1]} at {p['short']['hour']:02d}:00")
    if p["long"]:
        bits.append(f"long: the {p['long']['day']}{_ordinal(p['long']['day'])} of the month at {p['long']['hour']:02d}:00")
    if p["other"]:
        bits.append(f"custom: {p['raw']}")
    return ", ".join(bits)


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def next_run(expr: str | None, now: dt.datetime) -> dict:
    """The next short and long run after `now` (naive, host-local); None where the expression has none."""
    p = parse_expression(expr)
    out: dict = {"short": None, "long": None}
    for i in range(0, 70):
        day = (now + dt.timedelta(days=i)).date()
        if p["short"] and out["short"] is None and day.isoweekday() == p["short"]["weekday"]:
            t = dt.datetime.combine(day, dt.time(p["short"]["hour"]))
            if t > now:
                out["short"] = t
        if p["long"] and out["long"] is None and day.day == p["long"]["day"]:
            t = dt.datetime.combine(day, dt.time(p["long"]["hour"]))
            if t > now:
                out["long"] = t
    return out


def _first(runs: dict) -> dt.datetime | None:
    return min((t for t in runs.values() if t), default=None)


def _when(t: dt.datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M") if t else "?"


def _local(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def drive_schedule(smartd: dict | None, dev: str) -> tuple[str | None, bool]:
    """(the -s expression smartd applies to the drive, whether the host's file leaves the drive out). An explicit
    line for the device wins; otherwise DEVICESCAN's."""
    smartd = smartd or {}
    devs = smartd.get("devices") or {}
    if dev in devs:
        return devs[dev], devs[dev] is None
    return smartd.get("scan"), False


def disk_warnings(d: dict, smartd: dict | None = None, policy: dict | None = None) -> list[dict]:
    """A drive whose last self-test failed (always), or that was never tested or not lately. `smartd` is the
    collector's report of the host's smartd.conf (None for a reading from before it existed); `policy` the
    host's effective settings ({"warn": false} says nothing about missing tests). Only ATA drives report
    `tests`; a reading from before they existed has none and says nothing."""
    tests, poh, dev = d.get("tests"), d.get("power_on_hours"), d.get("device")
    if tests is None or poh is None:
        return []
    out = []
    for kind, t in tests.items():
        if t and t.get("passed") is False:
            out.append({"level": "error", "disk": dev,
                        "message": f"{dev}: the last {kind} SMART self-test failed ({t.get('status') or 'no detail'})"})
    policy = policy or {}
    expr, left_out = drive_schedule(smartd, dev)
    if policy.get("warn") is False or dev in (policy.get("skip") or []) or left_out:
        return out
    done = [t["hours"] for t in tests.values() if t and t.get("hours") is not None]
    if smartd is not None and not smartd.get("active", True) and expr:
        out.append({"level": "warning", "disk": dev,
                    "message": f"{dev}: self-tests are scheduled ({describe(expr)}) but smartd isn't running, so they don't happen"})
    elif done:
        if poh - max(done) > STALE_HOURS:
            out.append({"level": "warning", "disk": dev,
                        "message": f"{dev}: no SMART self-test in {(poh - max(done)) // 24} days of running (the last at hour {max(done)})"
                                   + (f"; scheduled: {describe(expr)}" if expr else "; none is scheduled (see the Self-tests page)")})
    elif smartd is None:
        out.append({"level": "warning", "disk": dev,
                    "message": f"{dev}: no SMART self-test has ever run (see the Self-tests page)"})
    elif not expr:
        out.append({"level": "warning", "disk": dev,
                    "message": f"{dev}: no SMART self-test has ever run and none is scheduled (see the Self-tests page)"})
    else:
        now, since = _local(smartd.get("now")), _local(smartd.get("conf_mtime"))
        first = _first(next_run(expr, since)) if since else None
        if now and first and now > first + dt.timedelta(days=1):
            out.append({"level": "warning", "disk": dev,
                        "message": f"{dev}: scheduled ({describe(expr)}) but no self-test has run: smartd may be unable to "
                                   "test this drive (a USB bridge needs '-d sat'); try 'smartctl -t short' by hand"})
        else:
            nxt = _first(next_run(expr, now)) if now else None
            out.append({"level": "info", "disk": dev,
                        "message": f"{dev}: no self-test has run yet; the first is scheduled ({describe(expr)}"
                                   + (f"; next {_when(nxt)} host time" if nxt else "") + ")"})
    return out
