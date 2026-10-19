#!/usr/bin/env python3
"""Build the LABaPe MCP bundle for Claude Desktop (an .mcpb file: a zip with a manifest).

    python tools/helpers/build_mcpb.py [output.mcpb]        (default: dist/labape-mcp-<version>.mcpb)

The bundle runs `app/labape/mcp.py` over stdio with the system Python (3.9 or newer, standard library only,
so nothing is installed), through a small bootstrap that also works with the Microsoft Store version of
Claude Desktop (see BOOTSTRAP). Desktop asks for the stack's address and the API token when it is imported; the
token is kept in the operating system's secret store. The tool list in the manifest is read from the code.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

# Claude Desktop from the Microsoft Store runs with a redirected profile: it unpacks an extension under
# %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\Claude Extensions\<name>, but tells the server the
# ordinary %APPDATA% path, which does not exist for other processes (Python: "can't open file"). So the
# server is started through this bootstrap: it uses the path it is given, else looks in the Store package.
BOOTSTRAP = (
    "import os,sys,glob,runpy;d=sys.argv[1];m=os.path.join('server','main.py');"
    "n=os.path.basename(d.replace(chr(92),'/').rstrip('/'));"
    "c=[d]+glob.glob(os.path.join(os.environ.get('LOCALAPPDATA',''),'Packages','Claude_*','LocalCache','Roaming',"
    "'Claude','Claude Extensions',n));"
    "r=next((x for x in c if os.path.isfile(os.path.join(x,m))),None);"
    "r or sys.exit('labape: cannot find the extension files in '+d);"
    "runpy.run_path(os.path.join(r,m),run_name='__main__')"
)

LAUNCHER = '''import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labape_mcp import main  # noqa: E402

raise SystemExit(main([]))
'''

README = """LABaPe for Claude Desktop

Lets Claude run commands and open text sessions on the VMs of your LABaPe lab, and manage environments, with
the permissions of an API token you make in LABaPe (user menu -> API tokens). Needs Python 3.9 or newer on
this computer. Every call is audited in LABaPe under the token's owner.
"""


def version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "app" / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def manifest() -> dict:
    from labape import mcp
    return {
        "manifest_version": "0.2",
        "name": "labape",
        "display_name": "LABaPe",
        "version": version(),
        "description": "Run commands and open text sessions on LABaPe lab VMs, and manage lab environments.",
        "long_description": ("Connects Claude to a LABaPe stack with an API token. The tools offered depend on the "
                             "token's roles: a console token gets commands and text sessions; tokens with the "
                             "deployer or read roles also get environment, host and job tools."),
        "author": {"name": "LABaPe"},
        "license": "AGPL-3.0-only",
        "server": {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": "python",
                "args": ["-c", BOOTSTRAP, "${__dirname}"],
                "env": {
                    "LABAPE_URL": "${user_config.labape_url}",
                    "LABAPE_TOKEN": "${user_config.api_token}",
                    "LABAPE_CA_FILE": "${user_config.ca_file}",
                    "LABAPE_INSECURE": "${user_config.skip_tls_verification}",
                },
            },
        },
        "tools": [{"name": t.name, "description": t.description.split(". ")[0].rstrip(".") + "."} for t in mcp.TOOLS],
        "tools_generated": False,
        "user_config": {
            "labape_url": {
                "type": "string", "title": "LABaPe address", "required": True,
                "description": "The web interface's address, for example https://oz.jlm.abq"},
            "api_token": {
                "type": "string", "title": "API token", "required": True, "sensitive": True,
                "description": "A token from LABaPe (user menu, API tokens). Give it the console role to run commands."},
            "ca_file": {
                "type": "string", "title": "Certificate authority file (optional)", "required": False, "default": "",
                "description": "Path to the stack's platform CA certificate (.crt), if this computer doesn't trust it"},
            "skip_tls_verification": {
                "type": "boolean", "title": "Don't verify the certificate", "required": False, "default": False,
                "description": "Only for a lab whose certificate you can't install. Prefer the CA file."},
        },
        "compatibility": {"platforms": ["win32", "darwin", "linux"], "runtimes": {"python": ">=3.9"}},
        "keywords": ["labape", "lab", "console", "ssh", "environments"],
    }


def build(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(), indent=2) + "\n")
        z.writestr("server/main.py", LAUNCHER)
        z.write(ROOT / "app" / "labape" / "mcp.py", "server/labape_mcp.py")
        z.writestr("README.txt", README)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / f"labape-mcp-{version()}.mcpb"
    print(build(target))
