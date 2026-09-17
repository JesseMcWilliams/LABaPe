#!/usr/bin/env bash
# docs/validate-setup.md §1 — confirms the base tools and required
# Ansible collections are actually installed and importable, not just
# "the command exists."
set -uo pipefail

fail=0

echo "== OpenTofu ==" >&2
if command -v tofu >/dev/null 2>&1; then
  ver="$(tofu version | head -1)"
  echo "OK: $ver" >&2
else
  echo "FAIL: tofu not found on PATH (docs/install-opentofu.md)" >&2
  fail=1
fi

echo "== Ansible ==" >&2
if command -v ansible >/dev/null 2>&1; then
  # Catches the pyo3/cryptography ABI mismatch a pip-installed
  # ansible-core can hit against a stale system `cryptography` package
  # — `ansible --version` genuinely crashes in that state, it isn't
  # just a formality check.
  if ansible --version >/tmp/labape-ansible-version.$$ 2>&1; then
    echo "OK: $(head -1 /tmp/labape-ansible-version.$$)" >&2
  else
    echo "FAIL: 'ansible --version' errored — see below (often a cryptography/cffi ABI mismatch; try: pip install --user --force-reinstall cffi cryptography)" >&2
    cat /tmp/labape-ansible-version.$$ >&2
    fail=1
  fi
  rm -f /tmp/labape-ansible-version.$$
else
  echo "FAIL: ansible not found on PATH (docs/install-ansible.md)" >&2
  fail=1
fi

echo "== Python / PyYAML ==" >&2
if python3 -c "import yaml" 2>/dev/null; then
  echo "OK: PyYAML importable ($(python3 -c 'import yaml; print(yaml.__version__)'))" >&2
else
  echo "FAIL: PyYAML not importable — every scripts/lib/*.py helper needs it (pip install --user pyyaml)" >&2
  fail=1
fi

echo "== Ansible collections ==" >&2
if command -v ansible-galaxy >/dev/null 2>&1; then
  installed="$(ansible-galaxy collection list 2>/dev/null)"
  for coll in ansible.windows microsoft.ad community.general chocolatey.chocolatey; do
    if echo "$installed" | grep -qi "^$coll "; then
      echo "OK: $coll installed" >&2
    else
      echo "FAIL: $coll not installed (docs/install-ansible.md §4: ansible-galaxy collection install $coll)" >&2
      fail=1
    fi
  done
else
  echo "FAIL: ansible-galaxy not found (comes with ansible-core)" >&2
  fail=1
fi

exit $fail
