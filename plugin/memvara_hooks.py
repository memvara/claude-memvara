"""Import modules from the vendored hooks' `lib` package by its location.

`statusline.py`, `setup.py` and `project_scope.py` use the hooks' own modules, so the
switches, the counts and the project are read by the same code the hooks run. That package
is called `lib`, a common name. Putting `hooks/` on `sys.path` and writing `from lib import
x` would pick up any other `lib` already imported in the process, so this loads the package
from its file and replaces a `lib` that lives anywhere else.
"""

import importlib
import importlib.util
import os
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")
_LIB = os.path.join(HOOKS, "lib")


def hooks_lib(*names: str) -> tuple:
    """The modules `lib.<name>` of the vendored hooks, in the order named."""
    package = sys.modules.get("lib")
    where = getattr(package, "__file__", None) or ""
    if package is None or os.path.realpath(os.path.dirname(where)) != os.path.realpath(_LIB):
        for key in [key for key in sys.modules if key == "lib" or key.startswith("lib.")]:
            del sys.modules[key]
        spec = importlib.util.spec_from_file_location(
            "lib", os.path.join(_LIB, "__init__.py"), submodule_search_locations=[_LIB])
        package = importlib.util.module_from_spec(spec)
        sys.modules["lib"] = package
        spec.loader.exec_module(package)
    return tuple(importlib.import_module(f"lib.{name}") for name in names)
