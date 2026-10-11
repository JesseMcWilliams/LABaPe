"""VM names for a host group: the names the user gave, else <group><n>.

The engine uses the same rule (tofu/backends/libvirt/main.tf: `names`), so
the API, the address allocator and the runner all agree on each VM's name.
"""
from __future__ import annotations

import re

# libvirt allows more, but Windows computer names stop at 15 characters
# and must not be all digits; DNS labels allow letters, digits and hyphens.
HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,14}$")


def instance_names(group: dict) -> list[str]:
    given = [n.strip() for n in (group.get("names") or [])]
    return [given[i] if i < len(given) and given[i] else f"{group['name']}{i + 1}"
            for i in range(group.get("count", 1))]
