"""Tests for the CONFIG layer: core/settings.py + commands/config.py.

Two tiers:
  1. Unit tests on the ConfigFile-level changes (fast, hermetic): settings,
     autoload, input-map encoding, groups, layer names.
  2. The DECISIVE engine validation — build a scratch project, apply the
     settings/autoload/input/group writes, then run a headless `extends SceneTree`
     script via run_generated_script and assert the ENGINE reads back what we
     wrote (proves the serialization, especially the InputEvent Object(...)
     encoding, is engine-valid).

Engine tests use the real Godot 4.3 binary via GODOT_BIN (no graceful skip).
"""

import os
from pathlib import Path

import pytest

from cli_anything.godot.core import settings as S
from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.utils.godot_backend import is_available, run_generated_script


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


# ──────────────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────────────

_MINIMAL_PROJECT = (
    "; Engine configuration file.\n\n"
    "config_version=5\n\n"
    "[application]\n\n"
    'config/name="Scratch"\n'
    'config/features=PackedStringArray("4.3", "GL Compatibility")\n\n'
    "[rendering]\n\n"
    'renderer/rendering_method="gl_compatibility"\n'
    'renderer/rendering_method.mobile="gl_compatibility"\n'
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.godot").write_text(_MINIMAL_PROJECT, encoding="utf-8")
    return str(tmp_path)


def _cf(project_dir):
    return ConfigFile.load(os.path.join(project_dir, "project.godot"))


# ──────────────────────────────────────────────────────────────────────
# settings get / set / unset / list
# ──────────────────────────────────────────────────────────────────────

def test_settings_set_get_typed(project):
    r = S.settings_set(project, "display/window/size/viewport_width", "1280", type="int")
    assert r["changed"] is True
    assert r["value"] == "1280"
    assert _cf(project).get("display", "window/size/viewport_width") == "1280"
    assert S.settings_get(project, "display/window/size/viewport_width")["value"] == "1280"

    # string keeps quotes
    S.settings_set(project, "application/config/name", "My Game", type="string")
    assert _cf(project).get("application", "config/name") == '"My Game"'

    # float / bool / color / vector2
    S.settings_set(project, "physics/2d/default_gravity", "980", type="float")
    assert _cf(project).get("physics", "2d/default_gravity") == "980.0"
    S.settings_set(project, "application/run/disable_stdout", "true", type="bool")
    assert _cf(project).get("application", "run/disable_stdout") == "true"
    S.settings_set(project, "x/clear", "0.3,0.3,0.3,1", type="color")
    assert _cf(project).get("x", "clear") == "Color(0.3, 0.3, 0.3, 1)"
    S.settings_set(project, "x/sz", "1280,720", type="vector2")
    assert _cf(project).get("x", "sz") == "Vector2(1280, 720)"


def test_settings_set_raw_verbatim(project):
    S.settings_set(project, "application/config/features",
                   'PackedStringArray("4.3", "Mobile")', type="raw")
    assert _cf(project).get("application", "config/features") == 'PackedStringArray("4.3", "Mobile")'


def test_settings_set_idempotent(project):
    S.settings_set(project, "a/b", "5", type="int")
    r = S.settings_set(project, "a/b", "5", type="int")
    assert r["changed"] is False


def test_settings_top_level_key(project):
    assert S.settings_get(project, "config_version")["value"] == "5"


def test_settings_unset(project):
    S.settings_set(project, "a/b", "5", type="int")
    r = S.settings_unset(project, "a/b")
    assert r["changed"] is True
    assert S.settings_get(project, "a/b")["found"] is False
    assert S.settings_unset(project, "a/b")["changed"] is False


def test_settings_list_section(project):
    out = S.settings_list(project, section="application")
    keys = {it["key"] for it in out["settings"]}
    assert "config/name" in keys


def test_settings_bad_type(project):
    with pytest.raises(RuntimeError):
        S.settings_set(project, "a/b", "x", type="nope")


# ──────────────────────────────────────────────────────────────────────
# autoload
# ──────────────────────────────────────────────────────────────────────

def test_autoload_add_enabled(project):
    r = S.autoload_add(project, "GameState", "res://globals/gs.gd")
    assert r["enabled"] is True
    assert _cf(project).get("autoload", "GameState") == '"*res://globals/gs.gd"'


def test_autoload_add_disabled_and_pathnorm(project):
    S.autoload_add(project, "Audio", "globals/audio.tscn", disabled=True)
    assert _cf(project).get("autoload", "Audio") == '"res://globals/audio.tscn"'


def test_autoload_enable_disable_toggle(project):
    S.autoload_add(project, "Audio", "res://a.gd")
    S.autoload_disable(project, "Audio")
    assert _cf(project).get("autoload", "Audio") == '"res://a.gd"'
    S.autoload_enable(project, "Audio")
    assert _cf(project).get("autoload", "Audio") == '"*res://a.gd"'


def test_autoload_remove_and_list(project):
    S.autoload_add(project, "A", "res://a.gd")
    S.autoload_add(project, "B", "res://b.gd", disabled=True)
    lst = S.autoload_list(project)["autoloads"]
    by = {x["name"]: x for x in lst}
    assert by["A"]["enabled"] is True and by["A"]["path"] == "res://a.gd"
    assert by["B"]["enabled"] is False
    assert S.autoload_remove(project, "A")["changed"] is True
    assert "A" not in {x["name"] for x in S.autoload_list(project)["autoloads"]}


def test_autoload_enable_missing(project):
    with pytest.raises(RuntimeError):
        S.autoload_enable(project, "Nope")


# ──────────────────────────────────────────────────────────────────────
# input map encoding
# ──────────────────────────────────────────────────────────────────────

def test_encode_physical_key_space():
    spec = {"kind": "key", "name": "SPACE", "physical": True}
    ev = S.encode_event(spec)
    assert ev.startswith("Object(InputEventKey,")
    assert '"device":-1' in ev
    assert '"physical_keycode":32' in ev
    assert '"keycode":0' in ev
    assert '"unicode":32' in ev
    assert ev.endswith('"script":null)')


def test_encode_key_letter_layout():
    # non-physical 'A' -> keycode 65, physical 0, unicode 97 (lowercase)
    ev = S.encode_event({"kind": "key", "name": "A", "physical": False})
    assert '"keycode":65' in ev
    assert '"physical_keycode":0' in ev
    assert '"unicode":97' in ev


def test_encode_mouse_joy():
    assert '"button_index":1' in S.encode_event({"kind": "mouse", "button": "left"})
    assert '"button_index":5' in S.encode_event({"kind": "mouse", "button": "wheel_down"})
    jb = S.encode_event({"kind": "joy_button", "button": "a"})
    assert jb.startswith("Object(InputEventJoypadButton,") and '"button_index":0' in jb
    jm = S.encode_event({"kind": "joy_axis", "axis": "lx", "value": -1.0})
    assert jm.startswith("Object(InputEventJoypadMotion,")
    assert '"axis":0' in jm and '"axis_value":-1.0' in jm


def test_parse_event_spec_exclusive():
    with pytest.raises(RuntimeError):
        S.parse_event_spec(key="A", mouse="left")
    with pytest.raises(RuntimeError):
        S.parse_event_spec()
    spec = S.parse_event_spec(joy_axis="lx:-1")
    assert spec == {"kind": "joy_axis", "axis": "lx", "value": -1.0}
    spec2 = S.parse_event_spec(joy_axis="rx")  # default value
    assert spec2["value"] == 1.0


def test_input_add_and_roundtrip(project):
    S.input_add(project, "jump", {"kind": "key", "name": "SPACE", "physical": True})
    raw = _cf(project).get("input", "jump")
    assert raw.startswith("{")
    assert '"deadzone": 0.5' in raw
    assert "InputEventKey" in raw and '"physical_keycode":32' in raw
    # re-parse for listing
    out = S.input_list(project, action="jump")
    assert out["deadzone"] == 0.5
    assert len(out["events"]) == 1 and out["events"][0]["type"] == "key"


def test_input_add_event_appends(project):
    S.input_add(project, "move_left", {"kind": "key", "name": "A", "physical": True})
    r = S.input_add_event(project, "move_left", {"kind": "joy_axis", "axis": "lx", "value": -1.0})
    assert r["event_count"] == 2
    out = S.input_list(project, action="move_left")
    types = [e["type"] for e in out["events"]]
    assert types == ["key", "joy_motion"]


def test_input_remove_event_index(project):
    S.input_add(project, "fire", {"kind": "mouse", "button": "left"})
    S.input_add_event(project, "fire", {"kind": "key", "name": "X", "physical": True})
    r = S.input_remove(project, "fire", event_index=0)
    assert r["event_count"] == 1
    out = S.input_list(project, action="fire")
    assert out["events"][0]["type"] == "key"


def test_input_remove_whole_action(project):
    S.input_add(project, "jump", {"kind": "key", "name": "SPACE", "physical": True})
    assert S.input_remove(project, "jump")["changed"] is True
    assert S.input_list(project)["actions"] == []


def test_input_remove_missing(project):
    with pytest.raises(RuntimeError):
        S.input_remove(project, "nope")


# ──────────────────────────────────────────────────────────────────────
# groups + layer names
# ──────────────────────────────────────────────────────────────────────

def test_group_add_remove_list(project):
    S.group_add(project, "enemies", description="Hostiles")
    S.group_add(project, "interactables")
    assert _cf(project).get("global_group", "enemies") == '"Hostiles"'
    assert _cf(project).get("global_group", "interactables") == '""'
    gl = {g["name"]: g["description"] for g in S.group_list(project)["groups"]}
    assert gl["enemies"] == "Hostiles"
    assert S.group_remove(project, "enemies")["changed"] is True


def test_layer_name(project):
    S.layer_name(project, "2d_physics", 1, "world")
    assert _cf(project).get("layer_names", "2d_physics/layer_1") == '"world"'
    with pytest.raises(RuntimeError):
        S.layer_name(project, "bogus_space", 1, "x")
    with pytest.raises(RuntimeError):
        S.layer_name(project, "2d_physics", 99, "x")


def test_not_a_project(tmp_path):
    with pytest.raises(RuntimeError):
        S.settings_get(str(tmp_path), "a/b")


# ──────────────────────────────────────────────────────────────────────
# DECISIVE engine validation: write -> Godot reads it back
# ──────────────────────────────────────────────────────────────────────

# An `extends SceneTree` script run against the project: autoload + InputMap +
# project settings are all loaded at startup, so the engine exposes everything we
# wrote. We print sentinels and assert on them.
_READBACK_GD = """extends SceneTree

func _init():
    print("NAME=", ProjectSettings.get_setting("application/config/name"))
    print("WIDTH=", ProjectSettings.get_setting("display/window/size/viewport_width"))
    print("AUTOLOAD=", ProjectSettings.has_setting("autoload/GameState"))
    print("AUTOLOAD_VAL=", ProjectSettings.get_setting("autoload/GameState"))
    print("HAS_JUMP=", InputMap.has_action("jump"))
    if InputMap.has_action("jump"):
        var evs = InputMap.action_get_events("jump")
        print("JUMP_EVENTS=", evs.size())
        for e in evs:
            if e is InputEventKey:
                print("JUMP_PHYS=", e.physical_keycode)
    print("HAS_MOVE_LEFT=", InputMap.has_action("move_left"))
    if InputMap.has_action("move_left"):
        print("MOVE_LEFT_EVENTS=", InputMap.action_get_events("move_left").size())
    print("GROUP=", ProjectSettings.has_setting("global_group/enemies"))
    print("LAYER=", ProjectSettings.get_setting("layer_names/2d_physics/layer_1"))
    print("READBACK_DONE")
    quit()
"""


@pytest.mark.skipif(not is_available(), reason="Godot binary not found (GODOT_BIN)")
def test_engine_readback(tmp_path):
    """The make-or-break proof: Godot parses our project.godot and reports the
    values we wrote — including the InputEvent Object(...) encoding."""
    project = str(tmp_path)
    (tmp_path / "project.godot").write_text(_MINIMAL_PROJECT, encoding="utf-8")

    # Apply a representative spread of writes.
    S.settings_set(project, "application/config/name", "Engine Readback", type="string")
    S.settings_set(project, "display/window/size/viewport_width", "1280", type="int")
    S.autoload_add(project, "GameState", "res://gs.gd")
    # GameState script must exist so the engine resolves the autoload cleanly.
    (tmp_path / "gs.gd").write_text("extends Node\n", encoding="utf-8")
    S.input_add(project, "jump", {"kind": "key", "name": "SPACE", "physical": True})
    S.input_add(project, "move_left", {"kind": "key", "name": "A", "physical": True})
    S.input_add_event(project, "move_left", {"kind": "joy_axis", "axis": "lx", "value": -1.0})
    S.group_add(project, "enemies", description="Hostiles")
    S.layer_name(project, "2d_physics", 1, "world")

    result = run_generated_script(project, _READBACK_GD, timeout=90)
    out = result["stdout"]
    print("\n--- engine stdout ---\n", out)
    print("--- engine stderr ---\n", result["stderr"])

    assert "READBACK_DONE" in out, f"script did not finish; stderr=\n{result['stderr']}"
    assert "NAME=Engine Readback" in out
    assert "WIDTH=1280" in out
    assert "AUTOLOAD=true" in out
    assert "HAS_JUMP=true" in out, "InputEventKey encoding rejected by engine!"
    assert "JUMP_EVENTS=1" in out
    assert "JUMP_PHYS=32" in out, "physical_keycode not read back as SPACE(32)"
    assert "HAS_MOVE_LEFT=true" in out
    assert "MOVE_LEFT_EVENTS=2" in out, "multi-event action (key + joy axis) not both parsed"
    assert "GROUP=true" in out
    assert "LAYER=world" in out
