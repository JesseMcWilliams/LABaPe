#!/usr/bin/env python3
"""Write a template's metadata file, <template_dir>/<name>.json (design §21.1).

Usage: write_template_meta.py <template_dir> <name> [--os KEY] [--edition core|desktop]
                              [--sku standard|datacenter]
                              [--from SOURCE_NAME] [--note TEXT]

--from copies the source template's metadata first (a refresh keeps its
description and notes); --note appends a dated line to the notes. Minimums
default by OS family. The web interface reads and edits the same file.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

MINIMUMS = {"windows_11": (2, 4096), "windows_10": (2, 2048)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("template_dir")
    ap.add_argument("name")
    ap.add_argument("--os", default="")
    ap.add_argument("--edition", default="")
    ap.add_argument("--sku", default="", choices=("", "standard", "datacenter"))
    ap.add_argument("--from", dest="source", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    lib = Path(a.template_dir)
    meta = {"os_key": "", "edition": "", "sku": "", "min_cpu": 1, "min_memory_mb": 1024, "description": "", "notes": ""}
    if a.source:
        try:
            meta.update(json.loads((lib / f"{a.source}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if a.os:
        meta["os_key"] = a.os
        if not a.source:
            cpu, mem = MINIMUMS.get(a.os, (2, 2048) if a.os.startswith("windows") else (1, 1024))
            meta["min_cpu"], meta["min_memory_mb"] = cpu, mem
    if a.edition:
        meta["edition"] = a.edition
    if a.sku:
        meta["sku"] = a.sku
    if a.note:
        stamp = dt.date.today().isoformat()
        meta["notes"] = (meta.get("notes", "").rstrip() + f"\n{stamp}: {a.note}").strip()
    out = lib / f"{a.name}.json"
    out.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    out.chmod(0o644)
    print(f"labape: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
