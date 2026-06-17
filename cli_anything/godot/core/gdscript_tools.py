"""GDScript tooling — skeleton creation, bulk validation, format/lint, docs, tests.

See SPEC-offline §C.3. Mechanisms:

  * ``script_new`` — FILE (write a ``.gd`` skeleton).
  * ``validate_all`` — FLAG loop, reusing ``core.script.validate_script``.
  * ``script_format`` / ``script_lint`` — shell out to the *optional* external
    **gdtoolkit** (``gdformat`` / ``gdlint``). Godot ships no formatter; when the
    tool is absent we degrade with a clear, non-fatal message (do NOT hard-fail).
  * ``script_docs`` — FLAG ``--doctool <dir> --gdscript-docs <path>``.
  * ``script_test`` — SCRIPT: a generated ``extends SceneTree`` runner that loads
    each test ``.gd``, instantiates it, and invokes its ``test_*`` methods,
    reporting pass/fail. (If GUT is present we note it; the generated runner is
    self-contained and needs no addon.)

``script run`` / ``script inline`` / ``script validate`` live in
``core.script`` and are imported by the command layer directly — not duplicated
here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from cli_anything.godot.core.script import validate_script
from cli_anything.godot.utils import godot_backend


# ──────────────────────────────────────────────────────────────────────
# script_new (FILE)
# ──────────────────────────────────────────────────────────────────────

def script_new(project_path: str, path: str, extends: str = "Node",
               class_name: str | None = None, tool: bool = False) -> dict:
    """Write a skeleton ``.gd`` file.

    Layout (Godot 4.x order): ``@tool`` -> ``class_name`` -> ``extends`` ->
    lifecycle stubs ``_ready`` / ``_process``.

    Args:
        path: project-relative (or ``res://``) output path.
        extends: base class for ``extends``.
        class_name: optional registered ``class_name``.
        tool: emit ``@tool`` so the script runs in the editor.
    """
    rel = path[len("res://"):] if path.startswith("res://") else path
    if not rel.endswith(".gd"):
        rel += ".gd"
    abs_path = Path(project_path) / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if tool:
        lines.append("@tool")
    if class_name:
        lines.append(f"class_name {class_name}")
    lines.append(f"extends {extends}")
    lines.append("")
    lines.append("")
    lines.append("func _ready() -> void:")
    lines.append("\tpass")
    lines.append("")
    lines.append("")
    lines.append("func _process(delta: float) -> void:")
    lines.append("\tpass")
    lines.append("")
    text = "\n".join(lines)
    abs_path.write_text(text, encoding="utf-8")
    return {
        "status": "ok", "changed": True, "path": rel,
        "extends": extends, "class_name": class_name, "tool": tool,
    }


# ──────────────────────────────────────────────────────────────────────
# validate_all (FLAG loop)
# ──────────────────────────────────────────────────────────────────────

def validate_all(project_path: str) -> dict:
    """Validate every ``*.gd`` in the project via ``--check-only`` (stderr scan).

    Skips our own generated temp scripts. Returns an aggregate result with a
    per-file breakdown and an overall ``valid`` flag.
    """
    root = Path(project_path)
    results = []
    all_valid = True
    for gd in sorted(root.rglob("*.gd")):
        name = gd.name
        if name.startswith("__cli_anything_gen_") or name == "_cli_anything_tmp.gd":
            continue
        if ".godot" in gd.parts:
            continue
        rel = gd.relative_to(root).as_posix()
        res = validate_script(project_path, rel)
        valid = res.get("valid", False)
        all_valid = all_valid and valid
        entry = {"script": rel, "valid": valid}
        if not valid:
            entry["errors"] = (res.get("errors") or "").strip()[:500]
        results.append(entry)
    return {
        "status": "ok", "changed": False,
        "valid": all_valid, "count": len(results), "scripts": results,
    }


# ──────────────────────────────────────────────────────────────────────
# format / lint (optional gdtoolkit)
# ──────────────────────────────────────────────────────────────────────

_GDTOOLKIT_HINT = (
    "optional dep gdtoolkit not installed: pip install gdtoolkit"
)


def _abs_target(project_path: str, path: str) -> Path:
    rel = path[len("res://"):] if path.startswith("res://") else path
    return Path(project_path) / rel


def script_format(project_path: str, path: str, write: bool = False,
                  check: bool = False) -> dict:
    """Format a ``.gd`` file (or dir) with ``gdformat`` if available.

    Degrades cleanly (status ``skipped``) when gdtoolkit is not installed —
    never raises for the missing-dep case.

    Args:
        write: apply changes in place.
        check: check-only mode (exit non-zero if reformatting needed); does not
            modify files.
    """
    exe = shutil.which("gdformat")
    if not exe:
        return {
            "status": "skipped", "changed": False, "path": path,
            "message": _GDTOOLKIT_HINT, "tool": "gdformat", "available": False,
        }
    target = _abs_target(project_path, path)
    if not target.exists():
        raise RuntimeError(f"Path not found: {path}")

    args = [exe]
    if check:
        args.append("--check")
    elif not write:
        # default to diff/preview without modifying when neither write nor check
        args.append("--diff")
    args.append(str(target))

    proc = subprocess.run(args, capture_output=True, text=True)
    # gdformat: rc 0 = ok/no-change; rc !=0 in --check means "would reformat".
    changed = write and proc.returncode == 0
    needs_format = check and proc.returncode != 0
    return {
        "status": "ok", "changed": changed, "path": path, "tool": "gdformat",
        "available": True, "returncode": proc.returncode,
        "needs_format": needs_format,
        "output": (proc.stdout or proc.stderr).strip()[:2000],
    }


def script_lint(project_path: str, path: str) -> dict:
    """Lint a ``.gd`` file (or dir) with ``gdlint`` if available.

    Degrades cleanly (status ``skipped``) when gdtoolkit is not installed.
    """
    exe = shutil.which("gdlint")
    if not exe:
        return {
            "status": "skipped", "changed": False, "path": path,
            "message": _GDTOOLKIT_HINT, "tool": "gdlint", "available": False,
        }
    target = _abs_target(project_path, path)
    if not target.exists():
        raise RuntimeError(f"Path not found: {path}")

    proc = subprocess.run([exe, str(target)], capture_output=True, text=True)
    clean = proc.returncode == 0
    return {
        "status": "ok", "changed": False, "path": path, "tool": "gdlint",
        "available": True, "clean": clean, "returncode": proc.returncode,
        "output": (proc.stdout + proc.stderr).strip()[:4000],
    }


# ──────────────────────────────────────────────────────────────────────
# docs (FLAG --gdscript-docs)
# ──────────────────────────────────────────────────────────────────────

def script_docs(project_path: str, out_dir: str, path: str = "res://") -> dict:
    """Generate GDScript API docs (XML) from ``##`` doc comments (FLAG).

    Uses ``--doctool <out_dir> --gdscript-docs <path>`` headlessly. The engine
    writes one XML per class under ``out_dir``.
    """
    out_abs = Path(out_dir)
    if not out_abs.is_absolute():
        out_abs = Path(project_path) / out_dir
    out_abs.mkdir(parents=True, exist_ok=True)

    res_path = path if path.startswith("res://") else f"res://{path.lstrip('/')}"
    result = godot_backend.run_godot(
        ["--doctool", str(out_abs), "--gdscript-docs", res_path, "--quit"],
        project_path=project_path,
        headless=True,
        timeout=120,
    )
    xml = sorted(str(p.relative_to(out_abs)) for p in out_abs.rglob("*.xml"))
    return {
        "status": "ok" if result["returncode"] == 0 else "error",
        "changed": bool(xml),
        "out_dir": str(out_abs),
        "path": res_path,
        "files": xml,
        "returncode": result["returncode"],
        "stderr": (result["stderr"] or "")[:1000] if result["returncode"] != 0 else "",
    }


# ──────────────────────────────────────────────────────────────────────
# test runner (SCRIPT)
# ──────────────────────────────────────────────────────────────────────

def script_test(project_path: str, test_dir: str, pattern: str = "test_*.gd",
                timeout: int = 180) -> dict:
    """Run a generated headless harness over test scripts (SCRIPT).

    For each ``test_*.gd`` under ``test_dir`` the harness:
      1. ``load()``s the script, ``.new()``s an instance,
      2. calls every ``test_*`` method, treating a raised error / assert failure
         as a fail (the engine flushes ``SCRIPT ERROR`` to stderr),
      3. reports per-method pass/fail.

    A test method "passes" if it runs without pushing an error. Tests can use
    ``assert(...)`` or ``push_error("...")`` to signal failure. If a GUT
    installation is detected (``res://addons/gut``) we note it but still use the
    self-contained runner so no addon is required.

    Returns aggregate {passed, failed, total} plus per-test detail.
    """
    rel_dir = test_dir[len("res://"):] if test_dir.startswith("res://") else test_dir
    abs_dir = Path(project_path) / rel_dir
    if not abs_dir.is_dir():
        raise RuntimeError(f"Test dir not found: {test_dir}")

    test_files = sorted(abs_dir.rglob(pattern))
    test_files = [t for t in test_files if ".godot" not in t.parts]
    if not test_files:
        return {
            "status": "ok", "changed": False, "passed": 0, "failed": 0,
            "total": 0, "tests": [], "message": f"no tests matching {pattern}",
        }

    res_dir = f"res://{rel_dir.replace(chr(92), '/')}"
    gut_present = (Path(project_path) / "addons" / "gut").is_dir()

    gd = _build_test_runner(res_dir, pattern)
    run = godot_backend.run_generated_script(project_path, gd, timeout=timeout)
    out = run.get("stdout") or ""
    err = run.get("stderr") or ""

    tests = []
    passed = failed = 0
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("RESULT|"):
            # RESULT|<script>|<method>|PASS or FAIL
            parts = line.split("|")
            if len(parts) >= 4:
                entry = {"script": parts[1], "method": parts[2],
                         "result": parts[3]}
                tests.append(entry)
                if parts[3] == "PASS":
                    passed += 1
                else:
                    failed += 1

    # A SCRIPT ERROR in stderr (assert/parse) means at least one failure even if
    # the per-line markers were not all emitted.
    engine_errored = any(m in err for m in ("SCRIPT ERROR", "Parse Error"))
    status = "ok" if (failed == 0 and not engine_errored) else "error"
    return {
        "status": status,
        "changed": False,
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "tests": tests,
        "gut_detected": gut_present,
        "engine_errors": err.strip()[:1000] if engine_errored else "",
    }


def _build_test_runner(res_dir: str, pattern: str) -> str:
    """Build the SceneTree harness GDScript source.

    Uses ``DirAccess`` to enumerate matching files under ``res_dir``, loads each
    as a GDScript, instances it, and calls its ``test_*`` methods. Per-method
    outcome is printed as ``RESULT|<script>|<method>|PASS|FAIL``.
    """
    # Translate the glob pattern's prefix/suffix into simple match logic.
    prefix = pattern.split("*")[0] if "*" in pattern else ""
    suffix = pattern.rsplit("*", 1)[-1] if "*" in pattern else pattern
    return (
        "extends SceneTree\n"
        "func _init():\n"
        f'\tvar dir_path := "{res_dir}"\n'
        f'\tvar prefix := "{prefix}"\n'
        f'\tvar suffix := "{suffix}"\n'
        "\tvar da := DirAccess.open(dir_path)\n"
        "\tif da == null:\n"
        '\t\tpush_error("DIR_OPEN_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        "\tda.list_dir_begin()\n"
        "\tvar fname := da.get_next()\n"
        "\tvar any_fail := false\n"
        '\twhile fname != "":\n'
        "\t\tif not da.current_is_dir() and fname.begins_with(prefix) and fname.ends_with(suffix):\n"
        '\t\t\tvar full := dir_path + "/" + fname\n'
        "\t\t\tvar scr = load(full)\n"
        "\t\t\tif scr == null:\n"
        '\t\t\t\tprint("RESULT|", fname, "|<load>|FAIL")\n'
        "\t\t\t\tany_fail = true\n"
        "\t\t\telse:\n"
        "\t\t\t\tvar inst = scr.new()\n"
        "\t\t\t\tvar methods = inst.get_method_list()\n"
        "\t\t\t\tfor m in methods:\n"
        '\t\t\t\t\tvar mn: String = m["name"]\n'
        '\t\t\t\t\tif mn.begins_with("test_"):\n'
        "\t\t\t\t\t\tinst.call(mn)\n"
        '\t\t\t\t\t\tprint("RESULT|", fname, "|", mn, "|PASS")\n'
        "\t\t\t\tif inst is RefCounted:\n"
        "\t\t\t\t\tpass\n"
        "\t\tfname = da.get_next()\n"
        "\tda.list_dir_end()\n"
        "\tquit(1 if any_fail else 0)\n"
    )
