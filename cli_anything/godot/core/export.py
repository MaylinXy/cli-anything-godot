"""Godot project export — build game binaries for target platforms.

IMPORTANT: there is NO `--export-all` flag in Godot 4.2-4.4 (a common
misconception; only a recent proposal PR #104204). This module enumerates the
runnable presets from `export_presets.cfg` and invokes
`--export-release`/`--export-debug` once per preset. Per SPEC-offline §A, a
`--headless --import` warmup runs first so a clean checkout's `.godot/` cache is
built before exporting (avoids hangs / broken builds, issues #95287, #69511).
"""

from pathlib import Path

from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.utils.godot_backend import (
    import_project,
    run_godot,
    validate_project,
)


def _strip(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw.strip().strip('"')


def _enumerate_presets(project_path: str) -> list[dict]:
    """Parse export_presets.cfg into a list of preset dicts (in file order)."""
    presets_file = Path(project_path) / "export_presets.cfg"
    if not presets_file.exists():
        return []
    cf = ConfigFile.load(str(presets_file))
    presets = []
    for section in cf.sections():
        # preset header sections look like 'preset.0' (NOT 'preset.0.options')
        if not section.startswith("preset.") or section.endswith(".options"):
            continue
        runnable_raw = _strip(cf.get(section, "runnable"))
        presets.append({
            "section": section,
            "name": _strip(cf.get(section, "name")),
            "platform": _strip(cf.get(section, "platform")),
            "runnable": (runnable_raw == "true") if runnable_raw is not None else True,
            "export_path": _strip(cf.get(section, "export_path")),
        })
    return presets


def list_export_presets(project_path: str) -> dict:
    """Parse export_presets.cfg and list available presets."""
    presets = _enumerate_presets(project_path)
    cleaned = [
        {k: p[k] for k in ("name", "platform", "runnable", "export_path")}
        for p in presets
    ]
    return {"status": "ok", "count": len(cleaned), "presets": cleaned}


def _export_one(project_path: str, name: str, output_path: str | None,
                debug: bool, timeout: int) -> dict:
    flag = "--export-debug" if debug else "--export-release"
    args = [flag, name]
    if output_path:
        args.append(output_path)
    args.append("--quit")
    result = run_godot(args, project_path=project_path, headless=True, timeout=timeout)
    return {
        "preset": name,
        "debug": debug,
        "output": output_path,
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "ok": result["returncode"] == 0,
    }


def export_project(
    project_path: str,
    preset: str | None = None,
    output_path: str | None = None,
    debug: bool = False,
    *,
    warmup: bool = True,
    timeout: int = 300,
) -> dict:
    """Export a Godot project.

    Args:
        project_path: Godot project directory.
        preset: Export preset name (from export_presets.cfg). If None, exports
                every RUNNABLE preset (the correct replacement for the bogus
                `--export-all`).
        output_path: Output path; only meaningful for a single named preset
                     (each preset otherwise uses its configured export_path).
        debug: Use `--export-debug` instead of `--export-release`.
        warmup: Run `--headless --import` first to warm the `.godot/` cache.
        timeout: Per-export subprocess timeout (seconds).

    Returns:
        Dict with status and per-preset results.
    """
    if not validate_project(project_path):
        return {"status": "error", "message": f"Not a Godot project: {project_path}"}

    presets = _enumerate_presets(project_path)
    if not presets:
        return {
            "status": "error",
            "message": "No export_presets.cfg / no presets. Configure export "
                       "presets in the Godot editor first.",
        }

    if preset is not None:
        if not any(p["name"] == preset for p in presets):
            return {
                "status": "error",
                "message": f"Preset not found: {preset!r}. Available: "
                           f"{[p['name'] for p in presets]}",
            }
        targets = [preset]
    else:
        targets = [p["name"] for p in presets if p["runnable"] and p["name"]]
        if not targets:
            return {
                "status": "error",
                "message": "No runnable presets to export.",
            }

    warmup_result = None
    if warmup:
        try:
            wr = import_project(project_path, timeout=timeout)
            warmup_result = {"returncode": wr["returncode"]}
        except RuntimeError as e:
            warmup_result = {"error": str(e)}

    results = []
    for name in targets:
        # per-preset output: only use output_path when exactly one named preset
        out = output_path if (preset is not None) else None
        results.append(_export_one(project_path, name, out, debug, timeout))

    all_ok = all(r["ok"] for r in results) if results else False
    return {
        "status": "ok" if all_ok else "error",
        "preset": preset or "all-runnable",
        "debug": debug,
        "warmup": warmup_result,
        "exported": results,
        "changed": all_ok,
    }


def export_all(project_path: str, debug: bool = False, **kwargs) -> dict:
    """Convenience: export every runnable preset (the `export build-all` path)."""
    return export_project(project_path, preset=None, debug=debug, **kwargs)
