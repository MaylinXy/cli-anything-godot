"""Godot Engine backend — subprocess wrapper for the Godot binary.

Godot runs as a local binary (godot / godot.exe / Godot_v4*).
All engine operations go through command-line flags:
  --headless      No GPU / display required
  --path <dir>    Set project directory
  --script <gd>   Run a GDScript (must extend SceneTree or MainLoop)
  --export-release/--export-debug <preset> <path>  Export one preset
  --import        Re-import project resources (warm .godot cache)
  --quit          Quit after completing the command

Note: there is NO --export-all flag in Godot 4.2-4.4; callers must loop over
presets (see core/export.py).
"""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any


# ---------- binary discovery ----------

_COMMON_NAMES = [
    "godot",
    "godot4",
    "godot.exe",
    "Godot_v4.4-stable_win64.exe",
    "Godot_v4.4-stable_linux.x86_64",
    "Godot_v4.3-stable_win64.exe",
    "Godot_v4.3-stable_linux.x86_64",
]


def find_godot_binary() -> str | None:
    """Search PATH and common locations for a Godot 4 binary.

    Returns:
        Absolute path to the binary, or None if not found.
    """
    # 1. Environment variable override
    env = os.environ.get("GODOT_BIN")
    if env and shutil.which(env):
        return shutil.which(env)

    # 2. Search PATH for common names
    for name in _COMMON_NAMES:
        path = shutil.which(name)
        if path:
            return path

    return None


def require_godot() -> str:
    """Return the Godot binary path or raise."""
    binary = find_godot_binary()
    if binary is None:
        raise RuntimeError(
            "Godot binary not found. Install Godot 4 and ensure it is on PATH, "
            "or set the GODOT_BIN environment variable."
        )
    return binary


# ---------- low-level runner ----------

def run_godot(
    args: list[str],
    project_path: str | None = None,
    headless: bool = True,
    timeout: int = 120,
    capture: bool = True,
) -> dict[str, Any]:
    """Execute the Godot binary with the given arguments.

    Args:
        args: Extra CLI flags (e.g. ['--script', 'res://tool.gd']).
        project_path: If set, adds --path <project_path>.
        headless: If True, adds --headless flag.
        timeout: Subprocess timeout in seconds.
        capture: If True, capture stdout/stderr.

    Returns:
        Dict with 'returncode', 'stdout', 'stderr' keys.

    Raises:
        RuntimeError: On binary-not-found or subprocess timeout.
    """
    binary = require_godot()
    cmd = [binary]
    if headless:
        cmd.append("--headless")
    if project_path:
        cmd.extend(["--path", str(project_path)])
    cmd.extend(args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            # Godot emits UTF-8; force it so non-Latin Windows locales (e.g. GBK)
            # don't crash decoding engine output. errors='replace' keeps it robust.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=project_path,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout if capture else "",
            "stderr": result.stderr if capture else "",
        }
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Godot command timed out after {timeout}s: {' '.join(cmd)}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Godot binary not found at {binary}"
        ) from e


# ---------- convenience helpers ----------

def get_version() -> dict:
    """Return Godot version info."""
    result = run_godot(["--version", "--quit"], headless=True, timeout=15)
    version_str = result["stdout"].strip().split("\n")[0] if result["stdout"] else "unknown"
    return {"version": version_str, "returncode": result["returncode"]}


def is_available() -> bool:
    """Check if Godot binary is reachable."""
    return find_godot_binary() is not None


def validate_project(project_path: str) -> bool:
    """Check if a directory is a valid Godot project (has project.godot)."""
    return Path(project_path, "project.godot").is_file()


# ---------- import / generated-script helpers ----------

def import_project(project_path: str, timeout: int = 180) -> dict:
    """Warm the `.godot/` cache via `--headless --import`.

    Generates `.import` sidecars and UIDs and builds `.godot/uid_cache.bin`.
    Per SPEC-offline §A, this is the robust prelude to a headless export and to
    any operation that needs resolved resource references.

    Returns the run dict ({returncode, stdout, stderr}). `--import` implies
    `--editor`/`--quit`, so no extra flags are needed.
    """
    if not validate_project(project_path):
        raise RuntimeError(f"Not a Godot project (no project.godot): {project_path}")
    return run_godot(
        ["--import"],
        project_path=project_path,
        headless=True,
        timeout=timeout,
    )


def run_generated_script(
    project_path: str,
    gd_source: str,
    *,
    editor: bool = False,
    timeout: int = 120,
    quit_after: int | None = None,
    user_args: list[str] | None = None,
) -> dict:
    """Write `gd_source` to a temp `.gd` INSIDE the project, run it, delete it.

    The temp file is created under the project root so `res://` resolves to it.

    editor=False:
        Runs `--headless --script res://<tmp>.gd --quit`. The supplied source
        must `extends SceneTree` (or `MainLoop`).
    editor=True:
        Runs `--editor --headless --script res://<tmp>.gd` (+ `--quit-after N`
        when `quit_after` is set, else `--quit`) so `EditorInterface` is
        reachable. The source should be a `@tool extends SceneTree` script.

    Extra `user_args` are appended after `--` so the script can read them via
    `OS.get_cmdline_user_args()`.

    Returns {returncode, stdout, stderr}. Raises RuntimeError on
    engine-not-found / timeout. The temp file is always removed (even on error).
    """
    if not validate_project(project_path):
        raise RuntimeError(f"Not a Godot project (no project.godot): {project_path}")

    require_godot()  # fail fast with a clear message if the binary is missing

    fname = f"__cli_anything_gen_{uuid.uuid4().hex[:12]}.gd"
    abs_path = Path(project_path) / fname
    res_path = f"res://{fname}"

    abs_path.write_text(gd_source, encoding="utf-8")
    try:
        args: list[str] = []
        if editor:
            args.append("--editor")
        args.extend(["--script", res_path])
        if editor and quit_after is not None:
            args.extend(["--quit-after", str(quit_after)])
        else:
            args.append("--quit")
        if user_args:
            args.append("--")
            args.extend(user_args)

        return run_godot(
            args,
            project_path=project_path,
            headless=True,
            timeout=timeout,
        )
    finally:
        try:
            abs_path.unlink()
        except OSError:
            pass
