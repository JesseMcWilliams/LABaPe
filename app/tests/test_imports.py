"""Every module imports: a syntax error in one the other tests never touch
(like cli.py) would otherwise reach a deploy and crash-loop the stack."""
import importlib
import pkgutil

import labape


def test_every_module_imports():
    names = [m.name for m in pkgutil.walk_packages(labape.__path__, "labape.") if ".migrations" not in m.name]
    assert "labape.cli" in names
    for name in names:
        importlib.import_module(name)
