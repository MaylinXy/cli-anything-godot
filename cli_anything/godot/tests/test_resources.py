"""Tests for RES_SCRIPT resource tooling (core/resources.py).

Engine-backed via the real Godot 4.3 binary (GODOT_BIN, no graceful skip). Each
.tres we build is proven to load by a headless ``extends SceneTree`` script that
``load()``s it and prints a property / point count.
"""

import uuid
from pathlib import Path

import pytest

from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.core import resources as res
from cli_anything.godot.utils.godot_backend import run_generated_script, is_available


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


@pytest.fixture
def scratch_project():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    proj = SCRATCH / f"res_{uuid.uuid4().hex[:8]}"
    proj.mkdir()
    cf = ConfigFile()
    cf.set("", "config_version", "5")
    cf.set("application", "config/name", '"ResTest"')
    cf.set("application", "config/features",
           'PackedStringArray("4.3", "GL Compatibility")')
    cf.set("rendering", "renderer/rendering_method", '"gl_compatibility"')
    cf.save(str(proj / "project.godot"))
    yield proj
    import shutil
    shutil.rmtree(proj, ignore_errors=True)


def test_engine_available():
    assert is_available(), "Godot 4.3 binary must be reachable via GODOT_BIN"


# ──────────────────────────────────────────────────────────────────────
# FILE: plain Resource with simple props
# ──────────────────────────────────────────────────────────────────────

def test_resource_create_plain_and_read(scratch_project):
    proj = scratch_project
    r = res.resource_create(
        str(proj), "data/cfg.tres", resource_type="Resource",
        props={"speed": 250.0, "title": "Goblin", "alive": True},
    )
    assert r["status"] == "ok" and r["changed"] and r["mechanism"] == "FILE"
    tres = proj / "data" / "cfg.tres"
    assert tres.exists()

    read = res.resource_read(str(proj), "data/cfg.tres")
    assert read["type"] == "Resource"
    assert read["props"]["speed"] == 250.0
    assert read["props"]["title"] == "Goblin"
    assert read["props"]["alive"] is True


def test_resource_edit_props(scratch_project):
    proj = scratch_project
    res.resource_create(str(proj), "data/cfg.tres", props={"price": 100})
    out = res.resource_edit(str(proj), "data/cfg.tres", props={"price": 120})
    assert out["changed"] is True and "price" in out["updated"]
    read = res.resource_read(str(proj), "data/cfg.tres")
    assert read["props"]["price"] == 120


# ──────────────────────────────────────────────────────────────────────
# Script-backed custom resource + ENGINE LOAD PROOF
# ──────────────────────────────────────────────────────────────────────

def test_script_backed_resource_engine_load(scratch_project):
    """Build a custom-resource .tres (FILE) and have the engine load it and
    print a property value — the .tres engine-load proof."""
    proj = scratch_project
    (proj / "stats.gd").write_text(
        "extends Resource\n"
        "class_name EnemyStats\n"
        "@export var max_health: int = 10\n"
        "@export var display_name: String = \"\"\n",
        encoding="utf-8",
    )
    r = res.resource_create(
        str(proj), "data/goblin.tres", resource_type="Resource",
        script="res://stats.gd", class_name="EnemyStats",
        props={"max_health": 100, "display_name": "Goblin"},
    )
    assert r["mechanism"] == "FILE"
    tres = proj / "data" / "goblin.tres"
    assert tres.exists()

    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        '\tvar r = load("res://data/goblin.tres")\n'
        "\tif r == null:\n"
        '\t\tpush_error("LOAD_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("PROP ", r.max_health, " ", r.display_name)\n'
        "\tquit(0)\n"
    )
    result = run_generated_script(str(proj), runner, timeout=120)
    assert "PROP 100 Goblin" in result["stdout"], (
        f"rc={result['returncode']}\n--- tres ---\n{tres.read_text()}\n"
        f"--- stdout ---\n{result['stdout']}\n--- stderr ---\n{result['stderr']}"
    )


# ──────────────────────────────────────────────────────────────────────
# SCRIPT: create_curve / create_gradient (engine builders)
# ──────────────────────────────────────────────────────────────────────

def test_create_curve_engine(scratch_project):
    proj = scratch_project
    r = res.create_curve(str(proj), "data/ramp.tres",
                         [(0.0, 0.0), (0.5, 0.8), (1.0, 1.0)])
    assert r["mechanism"] == "SCRIPT" and r["point_count"] == 3
    assert (proj / "data" / "ramp.tres").exists()

    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        '\tvar c = load("res://data/ramp.tres")\n'
        "\tif c == null:\n"
        '\t\tpush_error("LOAD_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("CURVE ", c.point_count)\n'
        "\tquit(0)\n"
    )
    result = run_generated_script(str(proj), runner, timeout=120)
    assert "CURVE 3" in result["stdout"], (
        f"rc={result['returncode']}\nstdout={result['stdout']}\nstderr={result['stderr']}"
    )


def test_create_gradient_engine(scratch_project):
    proj = scratch_project
    r = res.create_gradient(str(proj), "data/fade.tres",
                            [(0.0, "Color(0, 0, 0, 1)"), (1.0, "Color(1, 1, 1, 1)")])
    assert r["mechanism"] == "SCRIPT" and r["point_count"] == 2
    assert (proj / "data" / "fade.tres").exists()

    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        '\tvar g = load("res://data/fade.tres")\n'
        "\tif g == null:\n"
        '\t\tpush_error("LOAD_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("GRAD ", g.get_point_count())\n'
        "\tquit(0)\n"
    )
    result = run_generated_script(str(proj), runner, timeout=120)
    assert "GRAD 2" in result["stdout"], (
        f"rc={result['returncode']}\nstdout={result['stdout']}\nstderr={result['stderr']}"
    )


def test_create_complex_base_type_via_script(scratch_project):
    """resource_create on a known complex base (Curve) falls back to SCRIPT."""
    proj = scratch_project
    r = res.resource_create(str(proj), "data/empty_curve.tres", resource_type="Curve")
    assert r["mechanism"] == "SCRIPT"
    assert (proj / "data" / "empty_curve.tres").exists()
