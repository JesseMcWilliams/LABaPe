"""LABaPe's own license: AGPL-3.0-only, stated the same everywhere (LICENSE, the Python and web packages, the UI)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPDX = "AGPL-3.0-only"


def test_the_license_text_is_the_agpl():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE") and "Version 3, 19 November 2007" in text
    assert "13. Remote Network Interaction; Use with the GNU General Public License." in text
    assert 30000 < len(text) < 40000                                    # the whole text, not a stub


def test_the_packages_say_the_same():
    assert re.search(rf'^license = "{SPDX}"', (ROOT / "app" / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    pkg = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    assert pkg["license"] == SPDX and lock["packages"][""]["license"] == SPDX


def test_the_ui_offers_the_source():
    """AGPL section 13: users of a modified copy over the network are offered its source."""
    app = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "SOURCE_URL" in app and "Source code" in app and "GNU Affero General Public License" in app
