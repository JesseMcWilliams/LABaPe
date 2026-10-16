"""An environment's time zone (design §52): IANA names as browsers give them, and the Windows name for each.

The table is `tofu/timezones.json`, shared with OpenTofu (the Windows answer files take the Windows name). A browser
whose zone isn't listed gets UTC; the form offers the listed ones.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import get_settings

DEFAULT = "UTC"


@lru_cache(maxsize=1)
def table() -> dict[str, str]:
    for base in (get_settings().engine_dir, Path(__file__).resolve().parents[2]):
        path = Path(base) / "tofu" / "timezones.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise RuntimeError("tofu/timezones.json is missing")


def names() -> list[str]:
    """The zones the form offers: UTC first, then the rest by name."""
    return [DEFAULT] + sorted(n for n in table() if n not in (DEFAULT, "Etc/UTC"))


def is_valid(name: str) -> bool:
    return name in table()


def windows_name(name: str) -> str:
    return table().get(name or DEFAULT, table()[DEFAULT])
