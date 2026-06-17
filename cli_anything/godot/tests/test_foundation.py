"""Tests for the FOUND (foundation) layer: variant_fmt, configfile, tscn,
output, godot_backend additions, and the export.py fix.

Engine-backed tests use the real Godot 4.3 binary via GODOT_BIN (no graceful
skip — per the FOUND contract). Pure file-format tests are unit tests.
"""

import os
from pathlib import Path

import pytest

from cli_anything.godot.core import variant_fmt as vf
from cli_anything.godot.core.variant_fmt import GDValue, ext_ref, sub_ref
from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.core.tscn import TscnFile
from cli_anything.godot.utils.godot_backend import (
    is_available,
    import_project,
    run_generated_script,
)
from cli_anything.godot.core.export import (
    export_project,
    list_export_presets,
    _enumerate_presets,
)


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


# ──────────────────────────────────────────────────────────────────────
# variant_fmt
# ──────────────────────────────────────────────────────────────────────

def test_to_literal_scalars():
    assert vf.to_literal(True) == "true"
    assert vf.to_literal(False) == "false"
    assert vf.to_literal(42) == "42"
    assert vf.to_literal(1.0) == "1.0"
    assert vf.to_literal(2.5) == "2.5"
    assert vf.to_literal("hi") == '"hi"'
    assert vf.to_literal(None) == "null"


def test_to_literal_typed():
    assert vf.to_literal((10, 20), "vector2") == "Vector2(10, 20)"
    assert vf.to_literal("10,20", "vector2") == "Vector2(10, 20)"
    assert vf.to_literal((1, 2, 3), "vector3") == "Vector3(1, 2, 3)"
    assert vf.to_literal((1, 1, 1, 1), "color") == "Color(1, 1, 1, 1)"
    assert vf.to_literal(5, "float") == "5.0"
    assert vf.to_literal("res://x:scale", "nodepath") == 'NodePath("res://x:scale")'
    assert vf.to_literal('Transform2D(1, 0, 0, 1, 0, 0)', "raw") == "Transform2D(1, 0, 0, 1, 0, 0)"


def test_to_literal_string_escaping():
    assert vf.to_literal('a"b') == '"a\\"b"'
    assert vf.to_literal("line\nbreak") == '"line\\nbreak"'


def test_parse_literal_roundtrip():
    assert vf.parse_literal("true") is True
    assert vf.parse_literal("false") is False
    assert vf.parse_literal("null") is None
    assert vf.parse_literal("42") == 42
    assert vf.parse_literal("2.5") == 2.5
    assert vf.parse_literal('"hello"') == "hello"
    assert vf.parse_literal('"a\\"b"') == 'a"b'
    assert vf.parse_literal("Vector2(10, 20)") == (10, 20)
    assert vf.parse_literal("Color(1, 1, 1, 1)") == (1, 1, 1, 1)
    assert vf.parse_literal("[1, 2, 3]") == [1, 2, 3]


def test_parse_literal_unknown_is_gdvalue():
    v = vf.parse_literal('ExtResource("1_x")')
    assert isinstance(v, GDValue)
    assert v.raw == 'ExtResource("1_x")'
    np = vf.parse_literal('NodePath("../Other:scale")')
    assert isinstance(np, GDValue)


def test_refs():
    assert ext_ref("1_x") == 'ExtResource("1_x")'
    assert sub_ref("RectangleShape2D_a1b2c") == 'SubResource("RectangleShape2D_a1b2c")'


def test_value_roundtrip_via_to_and_parse():
    for py in [True, 42, 3.5, "text"]:
        assert vf.parse_literal(vf.to_literal(py)) == py


# ──────────────────────────────────────────────────────────────────────
# ConfigFile
# ──────────────────────────────────────────────────────────────────────

_PROJECT_GODOT = '''config_version=5

[application]

config/name="TestGame"
config/features=PackedStringArray("4.3", "GL Compatibility")
run/main_scene="res://Main.tscn"

[input]

jump={
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"keycode":0,"physical_keycode":32,"script":null)
]
}

[rendering]

renderer/rendering_method="gl_compatibility"
'''


def test_configfile_roundtrip():
    cf = ConfigFile.parse(_PROJECT_GODOT)
    assert cf.get("", "config_version") == "5"
    assert cf.get("application", "config/name") == '"TestGame"'
    assert cf.has_section("input")
    # multi-line input value preserved
    jump = cf.get("input", "jump")
    assert jump is not None and jump.startswith("{") and jump.rstrip().endswith("}")
    assert "physical_keycode" in jump
    # re-parse of serialized output is stable
    cf2 = ConfigFile.parse(cf.serialize())
    assert cf2.get("application", "config/name") == '"TestGame"'
    assert cf2.get("input", "jump") == jump
    assert cf2.sections() == cf.sections()


def test_configfile_set_unset_order():
    cf = ConfigFile.parse(_PROJECT_GODOT)
    cf.set("application", "config/version", '"1.0.0"')
    assert cf.get("application", "config/version") == '"1.0.0"'
    # order preserved: config/name still first
    keys = [k for k, _ in cf.section_items("application")]
    assert keys[0] == "config/name"
    assert "config/version" in keys
    assert cf.unset("application", "config/version") is True
    assert cf.get("application", "config/version") is None
    cf.set("newsection", "k", "1")
    assert cf.has_section("newsection")


# ──────────────────────────────────────────────────────────────────────
# tscn parse/serialize (pure)
# ──────────────────────────────────────────────────────────────────────

_TSCN = '''[gd_scene load_steps=3 format=3 uid="uid://b8x7y6z5w4v3u"]

[ext_resource type="Script" path="res://enemy.gd" id="1_script"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_a1b2c"]
size = Vector2(32, 48)

[node name="Enemy" type="CharacterBody2D" groups=["enemies"]]
script = ExtResource("1_script")
speed = 200.0

[node name="Sprite2D" type="Sprite2D" parent="."]
position = Vector2(0, -16)

[node name="CollisionShape2D" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_a1b2c")

[connection signal="body_entered" from="." to="." method="_on_body_entered"]
'''


def test_tscn_parse():
    f = TscnFile.parse(_TSCN)
    assert f.kind == "scene"
    assert f.fmt == 3
    assert f.uid == "uid://b8x7y6z5w4v3u"
    assert len(f.ext) == 1 and f.ext[0].id == "1_script"
    assert len(f.sub) == 1 and f.sub[0].type == "RectangleShape2D"
    assert f.sub[0].props["size"] == "Vector2(32, 48)"
    root = f.root()
    assert root.name == "Enemy" and root.parent is None
    assert root.groups == ["enemies"]
    assert root.props["speed"] == "200.0"
    spr = f.find("Sprite2D")
    assert spr is not None and spr.parent == "."
    assert len(f.connections) == 1
    assert f.connections[0].signal == "body_entered"


def test_tscn_roundtrip_structure():
    f = TscnFile.parse(_TSCN)
    out = f.serialize()
    f2 = TscnFile.parse(out)
    assert f2.uid == f.uid
    assert len(f2.ext) == 1 and len(f2.sub) == 1
    assert f2.root().name == "Enemy"
    assert f2.root().props["speed"] == "200.0"
    assert f2.find("CollisionShape2D").props["shape"] == 'SubResource("RectangleShape2D_a1b2c")'
    assert len(f2.connections) == 1
    # load_steps recomputed correctly
    assert "load_steps=3" in out
    # heading attrs have no spaces; property lines have spaces
    assert 'type="Script"' in out
    assert "size = Vector2(32, 48)" in out


def test_tscn_load_steps_omitted_when_empty():
    f = TscnFile.new_scene("Node2D", "Root")
    out = f.serialize()
    assert "load_steps" not in out
    assert out.startswith("[gd_scene format=3]")
    # root has no parent
    assert 'parent=' not in out.split("\n")[2]


def test_tscn_instanced_node():
    f = TscnFile.new_scene("Node2D", "Root")
    eid = f.add_ext_resource("PackedScene", "res://Enemy.tscn")
    f.add_node("E1", None, ".", instance=eid)
    out = f.serialize()
    assert f'instance=ExtResource("{eid}")' in out
    # instanced node must NOT have type=
    enemy_line = [l for l in out.splitlines() if 'name="E1"' in l][0]
    assert "type=" not in enemy_line


def test_tscn_id_namespaces_separate():
    f = TscnFile.new_scene("Node2D", "Root")
    eid = f.add_ext_resource("Texture2D", "res://a.png")
    sid = f.add_sub_resource("RectangleShape2D", {"size": "Vector2(8, 8)"})
    assert eid.startswith("1_")
    assert sid.startswith("RectangleShape2D_")
    # dedup
    eid2 = f.add_ext_resource("Texture2D", "res://a.png")
    assert eid == eid2


def test_tscn_deterministic_ids():
    f1 = TscnFile.new_scene("Node2D", "Root")
    f2 = TscnFile.new_scene("Node2D", "Root")
    assert f1.add_ext_resource("Texture2D", "res://a.png") == \
        f2.add_ext_resource("Texture2D", "res://a.png")


def test_tscn_remove_node_drops_descendants():
    f = TscnFile.new_scene("Node2D", "Root")
    f.add_node("Panel", "Panel", ".")
    f.add_node("Btn", "Button", "Panel")
    assert f.find("Panel/Btn") is not None
    f.remove_node("Panel")
    assert f.find("Panel") is None
    assert f.find("Panel/Btn") is None


def test_tres_roundtrip():
    f = TscnFile.new_resource("Resource", script_path="res://stats.gd",
                              script_class="EnemyStats")
    f.resource_props["max_health"] = "100"
    f.resource_props["display_name"] = '"Goblin"'
    out = f.serialize()
    assert out.startswith("[gd_resource")
    assert 'script_class="EnemyStats"' in out
    assert "[resource]" in out
    assert "max_health = 100" in out
    f2 = TscnFile.parse(out)
    assert f2.kind == "resource"
    assert f2.resource_type == "Resource"
    assert f2.script_class == "EnemyStats"
    assert f2.resource_props["max_health"] == "100"


# ──────────────────────────────────────────────────────────────────────
# Engine-backed: prove the serializer produces engine-valid scenes
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def scratch_project():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    import uuid
    proj = SCRATCH / f"found_{uuid.uuid4().hex[:8]}"
    proj.mkdir()
    # project.godot via our own ConfigFile
    cf = ConfigFile()
    cf.set("", "config_version", "5")
    cf.set("application", "config/name", '"FoundTest"')
    cf.set("application", "config/features",
           'PackedStringArray("4.3", "GL Compatibility")')
    cf.set("rendering", "renderer/rendering_method", '"gl_compatibility"')
    cf.save(str(proj / "project.godot"))
    yield proj
    # cleanup
    import shutil
    shutil.rmtree(proj, ignore_errors=True)


def test_engine_available():
    assert is_available(), "Godot 4.3 binary must be reachable via GODOT_BIN"


def test_serializer_produces_engine_valid_scene(scratch_project):
    """Build a scene with TscnFile, write it, and have Godot load+instantiate it."""
    proj = scratch_project

    # A simple texture-less scene: root Node2D + child Sprite2D + a sub_resource
    # referenced by a CollisionShape2D, plus an ext_resource (a real .gd script).
    (proj / "noop.gd").write_text("extends Node\n", encoding="utf-8")

    f = TscnFile.new_scene("Node2D", "Root")
    sid = f.add_sub_resource("RectangleShape2D", {"size": "Vector2(32, 48)"})
    eid = f.add_ext_resource("Script", "res://noop.gd")
    f.add_node("Sprite2D", "Sprite2D", ".")
    f.find("Sprite2D").props["position"] = "Vector2(0, -16)"
    col = f.add_node("CollisionShape2D", "CollisionShape2D", ".")
    col.props["shape"] = f'SubResource("{sid}")'
    # attach the script to the root
    f.root().props["script"] = f'ExtResource("{eid}")'

    scene_text = f.serialize()
    (proj / "Test.tscn").write_text(scene_text, encoding="utf-8")

    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        "    var p = load(\"res://Test.tscn\")\n"
        "    if p == null:\n"
        "        push_error(\"LOAD_FAILED\")\n"
        "        quit(1)\n"
        "        return\n"
        "    var i = p.instantiate()\n"
        "    print(\"OK \", i.get_child_count())\n"
        "    quit(0)\n"
    )
    result = run_generated_script(str(proj), runner, timeout=120)
    combined = result["stdout"] + result["stderr"]
    assert "OK 2" in result["stdout"], (
        f"scene did not load/instantiate.\nrc={result['returncode']}\n"
        f"--- scene ---\n{scene_text}\n--- stdout ---\n{result['stdout']}\n"
        f"--- stderr ---\n{result['stderr']}"
    )
    assert "LOAD_FAILED" not in combined


def test_run_generated_script_cleans_up(scratch_project):
    proj = scratch_project
    src = 'extends SceneTree\nfunc _init():\n    print("HELLO_FOUND")\n    quit(0)\n'
    result = run_generated_script(str(proj), src, timeout=60)
    assert "HELLO_FOUND" in result["stdout"]
    # no leftover temp .gd files
    leftovers = list(proj.glob("__cli_anything_gen_*.gd"))
    assert leftovers == []


def test_import_project(scratch_project):
    proj = scratch_project
    (proj / "noop.gd").write_text("extends Node\n", encoding="utf-8")
    result = import_project(str(proj), timeout=120)
    assert result["returncode"] == 0, result["stderr"]
    # .godot cache should now exist
    assert (proj / ".godot").is_dir()


# ──────────────────────────────────────────────────────────────────────
# export.py fix
# ──────────────────────────────────────────────────────────────────────

_EXPORT_PRESETS = '''[preset.0]

name="Windows Desktop"
platform="Windows Desktop"
runnable=true
export_path="build/game.exe"

[preset.0.options]

binary_format/64_bits=true

[preset.1]

name="Disabled Web"
platform="Web"
runnable=false
export_path="build/index.html"

[preset.1.options]

variant/extensions_support=false
'''


def test_export_enumerates_presets(tmp_path):
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "export_presets.cfg").write_text(_EXPORT_PRESETS, encoding="utf-8")
    presets = _enumerate_presets(str(tmp_path))
    names = [p["name"] for p in presets]
    assert names == ["Windows Desktop", "Disabled Web"]
    assert presets[0]["runnable"] is True
    assert presets[1]["runnable"] is False
    listed = list_export_presets(str(tmp_path))
    assert listed["count"] == 2


def test_export_no_export_all_flag(tmp_path, monkeypatch):
    """Verify export_project never passes --export-all and loops per preset."""
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "export_presets.cfg").write_text(_EXPORT_PRESETS, encoding="utf-8")

    calls = []

    import cli_anything.godot.core.export as exp

    def fake_import(project_path, timeout=180):
        calls.append(("import", []))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    def fake_run(args, project_path=None, headless=True, timeout=120):
        calls.append(("run", list(args)))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(exp, "import_project", fake_import)
    monkeypatch.setattr(exp, "run_godot", fake_run)

    res = export_project(str(tmp_path))  # preset=None -> all runnable
    # never --export-all
    for kind, args in calls:
        assert "--export-all" not in args
    # warmup import ran
    assert ("import", []) in calls
    # exactly one --export-release for the single runnable preset
    export_calls = [a for k, a in calls if k == "run" and "--export-release" in a]
    assert len(export_calls) == 1
    assert "Windows Desktop" in export_calls[0]
    # the disabled preset was NOT exported
    assert all("Web" not in a for a in export_calls)
    assert res["status"] == "ok"


def test_export_unknown_preset_errors(tmp_path):
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "export_presets.cfg").write_text(_EXPORT_PRESETS, encoding="utf-8")
    res = export_project(str(tmp_path), preset="Nope", warmup=False)
    assert res["status"] == "error"
    assert "not found" in res["message"].lower()
