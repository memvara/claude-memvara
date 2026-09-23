"""Import modules from the vendored hooks' `lib` package by its location.

`statusline.py`, `setup.py` and `project_scope.py` use the hooks' own modules, so the
switches, the counts and the project are read by the same code the hooks run. That package
is called `lib`, a common name. Putting `hooks/` on `sys.path` and writing `from lib import
x` would pick up any other `lib` already imported in the process, so this loads the package
from its file and replaces a `lib` that lives anywhere else.

Some of those modules import the hooks' other packages in turn: `lib.ipc` reads the
client's record through `core.host`, which finds it in `hosts`, to locate the client's MCP
configuration, and `lib.read_model`, which setup's key check uses, imports `lib.ipc`.
`core` and `hosts` are common names too, so they are loaded the same way, from their files.
"""

import importlib
import importlib.util
import os
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")
_LIB = os.path.join(HOOKS, "lib")


def _vendored(name: str) -> None:
    """Make `sys.modules[name]` the vendored package `hooks/<name>`, replacing any other."""
    where = os.path.join(HOOKS, name)
    package = sys.modules.get(name)
    found = getattr(package, "__file__", None) or ""
    if package is not None and os.path.realpath(os.path.dirname(found)) == os.path.realpath(where):
        return
    for key in [key for key in sys.modules if key == name or key.startswith(f"{name}.")]:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(where, "__init__.py"), submodule_search_locations=[where])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)


def hooks_lib(*names: str) -> tuple:
    """The modules `lib.<name>` of the vendored hooks, in the order named."""
    for package in ("core", "hosts", "lib"):
        _vendored(package)
    return tuple(importlib.import_module(f"lib.{name}") for name in names)
