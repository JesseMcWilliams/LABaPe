"""Small string-munging helpers for docs/directory-objects.md (M5) that
are awkward to express in pure Jinja2 — kept as a filter plugin rather
than growing a tangle of chained Jinja2 filters in the role tasks
themselves.
"""


def ou_dns(organizational_units, ou_refs):
    """Every OU DN that needs to exist, shallowest first: the explicit
    organizational_units list (each {name, path} -> "OU=<name>,<path>"),
    plus every ancestor of each OU DN referenced by a domain_groups[].ou
    or domain_users[].ou field (docs/directory-objects.md §3's OU
    auto-creation) — walking the DN from the top down is what makes an
    explicit organizational_units entry optional scaffolding rather than
    a strict prerequisite.
    """
    dns = []
    seen = set()

    def add(dn):
        if dn not in seen:
            seen.add(dn)
            dns.append(dn)

    for ou in organizational_units or []:
        add("OU=" + ou["name"] + "," + ou["path"])

    for ref in ou_refs or []:
        if not ref:
            continue
        parts = [p.strip() for p in ref.split(",")]
        ou_parts = [p for p in parts if p.upper().startswith("OU=")]
        base_parts = [p for p in parts if not p.upper().startswith("OU=")]
        base = ",".join(base_parts)
        for depth in range(1, len(ou_parts) + 1):
            add(",".join(ou_parts[-depth:]) + "," + base)

    # Shallowest first: fewer comma-separated components == closer to the
    # domain root, so parents are always created before their children.
    return sorted(dns, key=lambda dn: len(dn.split(",")))


def for_host_group(items, group_names):
    """Filter a list of directory-manifest entries (each with a `hosts:`
    list of role names, docs/directory-objects.md §4/§5/§7) down to the
    ones that apply to this host — i.e. its `hosts:` list intersects the
    current host's group_names. No built-in Jinja2 test does a plain list
    intersection, hence a filter rather than `selectattr`.
    """
    names = set(group_names or [])
    return [i for i in (items or []) if names & set(i.get("hosts", []))]


class FilterModule:
    def filters(self):
        return {
            "ou_dns": ou_dns,
            "for_host_group": for_host_group,
        }
