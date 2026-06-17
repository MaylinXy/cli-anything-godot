"""Test bootstrap: ensure the agent-harness root is importable and GODOT_BIN set.

The package uses namespace packaging; tests must import
``cli_anything.godot.*``. We insert the agent-harness dir (4 levels up from this
file: tests -> godot -> cli_anything -> agent-harness) onto sys.path.

GODOT_BIN is a User-scope env var that new shells inherit, but a test process
spawned from a shell that did not inherit it would fail engine tests. We
back-fill it from the Windows User scope if missing.
"""

import os
import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[3]
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))


def _ensure_godot_bin():
    if os.environ.get("GODOT_BIN"):
        return
    # Best-effort: read the User-scope env var on Windows.
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, "GODOT_BIN")
                if val:
                    os.environ["GODOT_BIN"] = val
        except OSError:
            pass


_ensure_godot_bin()
