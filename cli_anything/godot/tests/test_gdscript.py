"""Tests for RES_SCRIPT GDScript tooling (core/gdscript_tools.py).

Engine-backed via the real Godot 4.3 binary (GODOT_BIN). gdtoolkit is optional:
format/lint must degrade cleanly when it is absent, and work when present.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.core import gdscript_tools as tools
from cli_anything.godot.core.script import validate_script
from cli_anything.godot.utils.godot_backend import is_available


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


@pytest.fixture
def scratch_project():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    proj = SCRATCH / f"gd_{uuid.uuid4().hex[:8]}"
    proj.mkdir()
    cf = ConfigFile()
    cf.set("", "config_version", "5")
    cf.set("application", "config/name", '"GdTest"')
    cf.set("application", "config/features",
           'PackedStringArray("4.3", "GL Compatibility")')
    cf.set("rendering", "renderer/rendering_method", '"gl_compatibility"')
    cf.save(str(proj / "project.godot"))
    yield proj
    shutil.rmtree(proj, ignore_errors=True)


def test_engine_available():
    assert is_available(), "Godot 4.3 binary must be reachable via GODOT_BIN"


# ──────────────────────────────────────────────────────────────────────
# script_new + validate
# ──────────────────────────────────────────────────────────────────────

def test_script_new_validates_clean(scratch_project):
    proj = scratch_project
    out = tools.script_new(str(proj), "src/player.gd",
                           extends="Node2D", class_name="Player")
    assert out["changed"] and out["path"] == "src/player.gd"
    text = (proj / "src" / "player.gd").read_text()
    assert "class_name Player" in text
    assert "extends Node2D" in text

    v = validate_script(str(proj), "src/player.gd")
    assert v["valid"] is True, v["errors"]


def test_script_new_tool_flag(scratch_project):
    proj = scratch_project
    tools.script_new(str(proj), "src/ed.gd", extends="Node", tool=True)
    text = (proj / "src" / "ed.gd").read_text()
    assert text.startswith("@tool")


def test_validate_catches_broken_script(scratch_project):
    proj = scratch_project
    (proj / "broken.gd").write_text(
        "extends Node\nfunc _ready():\n\tthis is not valid gdscript !!\n",
        encoding="utf-8",
    )
    v = validate_script(str(proj), "broken.gd")
    assert v["valid"] is False
    assert v["errors"]  # stderr markers captured


def test_validate_all(scratch_project):
    proj = scratch_project
    tools.script_new(str(proj), "a.gd", extends="Node")
    tools.script_new(str(proj), "b.gd", extends="Node2D")
    out = tools.validate_all(str(proj))
    assert out["valid"] is True
    assert out["count"] == 2
    names = {s["script"] for s in out["scripts"]}
    assert {"a.gd", "b.gd"} <= names

    # add a broken one -> aggregate invalid
    (proj / "bad.gd").write_text("extends Node\nfunc x(: !!\n", encoding="utf-8")
    out2 = tools.validate_all(str(proj))
    assert out2["valid"] is False


# ──────────────────────────────────────────────────────────────────────
# format / lint (optional gdtoolkit)
# ──────────────────────────────────────────────────────────────────────

def test_format_degrades_or_works(scratch_project):
    proj = scratch_project
    tools.script_new(str(proj), "f.gd", extends="Node")
    out = tools.script_format(str(proj), "f.gd")
    if shutil.which("gdformat"):
        assert out["status"] == "ok" and out["available"] is True
    else:
        assert out["status"] == "skipped"
        assert "gdtoolkit" in out["message"]
        assert out["available"] is False


def test_lint_degrades_or_works(scratch_project):
    proj = scratch_project
    tools.script_new(str(proj), "g.gd", extends="Node")
    out = tools.script_lint(str(proj), "g.gd")
    if shutil.which("gdlint"):
        assert out["status"] == "ok" and out["available"] is True
    else:
        assert out["status"] == "skipped"
        assert "gdtoolkit" in out["message"]


# ──────────────────────────────────────────────────────────────────────
# docs (FLAG --gdscript-docs)
# ──────────────────────────────────────────────────────────────────────

def test_script_docs_runs(scratch_project):
    proj = scratch_project
    (proj / "src").mkdir()
    (proj / "src" / "documented.gd").write_text(
        "extends Node\n"
        "class_name Documented\n"
        "## A documented class.\n"
        "## Tracks a counter.\n"
        "var count: int = 0\n"
        "## Increment the counter.\n"
        "func bump() -> void:\n"
        "\tcount += 1\n",
        encoding="utf-8",
    )
    out = tools.script_docs(str(proj), "docs_out", path="res://src")
    assert out["status"] == "ok", out.get("stderr")
    # the engine writes at least one XML for the documented class
    assert any("Documented" in f or f.endswith(".xml") for f in out["files"]) or out["files"]


# ──────────────────────────────────────────────────────────────────────
# test runner (SCRIPT)
# ──────────────────────────────────────────────────────────────────────

def test_script_test_passes(scratch_project):
    proj = scratch_project
    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_math.gd").write_text(
        "extends RefCounted\n"
        "func test_add():\n"
        "\tassert(2 + 2 == 4)\n"
        "func test_truth():\n"
        "\tassert(true)\n",
        encoding="utf-8",
    )
    out = tools.script_test(str(proj), "res://tests")
    assert out["status"] == "ok", out
    assert out["passed"] == 2
    assert out["failed"] == 0
    methods = {t["method"] for t in out["tests"]}
    assert {"test_add", "test_truth"} <= methods


def test_script_test_detects_failure(scratch_project):
    proj = scratch_project
    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_bad.gd").write_text(
        "extends RefCounted\n"
        "func test_fails():\n"
        "\tassert(1 == 2)\n",
        encoding="utf-8",
    )
    out = tools.script_test(str(proj), "res://tests")
    assert out["status"] == "error"


def test_script_test_no_tests(scratch_project):
    proj = scratch_project
    (proj / "tests").mkdir()
    out = tools.script_test(str(proj), "res://tests")
    assert out["total"] == 0
