"""Tests for the NODES layer: core/nodes.py, core/signals.py, commands.

Unit tests operate on synthetic .tscn text via temp files. Engine-validation
tests write the produced scene into a scratch project under
D:\\ClaudeWorkspace\\CLI-Anything-Study\\godot-build\\scratch and run a headless
SceneTree script (via godot_backend.run_generated_script) that load()s and
instantiate()s the scene and prints child counts / renamed-node presence.

No graceful skip on the engine path (per CONTRACT) — GODOT_BIN must be set.
"""

import uuid
from pathlib import Path

import pytest

from cli_anything.godot.core import nodes as N
from cli_anything.godot.core import signals as S
from cli_anything.godot.core.tscn import TscnFile
from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.utils.godot_backend import is_available, run_generated_script


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


# ──────────────────────────────────────────────────────────────────────
# helpers / fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_scene(tmp_path):
    """Return (project_dir, scene_rel) with a fresh empty project + a base scene."""
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    def _make(text: str, rel: str = "Test.tscn") -> tuple[str, str]:
        (tmp_path / rel).write_text(text, encoding="utf-8")
        return str(tmp_path), rel

    return _make


def _read(project, rel):
    return (Path(project) / rel).read_text(encoding="utf-8")


BASE_SCENE = """\
[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="Panel" type="Panel" parent="."]

[node name="Btn" type="Button" parent="Panel"]

[node name="Icon" type="Sprite2D" parent="Panel/Btn"]
"""


# ──────────────────────────────────────────────────────────────────────
# read / tree
# ──────────────────────────────────────────────────────────────────────

def test_read_scene(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.read_scene(proj, rel)
    assert res["status"] == "ok"
    paths = [n["path"] for n in res["nodes"]]
    assert paths == [".", "Panel", "Panel/Btn", "Panel/Btn/Icon"]


def test_scene_tree_string(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.scene_tree(proj, rel)
    assert "Root" in res["tree"]
    assert "Btn" in res["tree"]
    assert res["root"]["children"][0]["name"] == "Panel"


# ──────────────────────────────────────────────────────────────────────
# add / remove / move
# ──────────────────────────────────────────────────────────────────────

def test_add_node(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.add_node(proj, rel, "Label", "Label", "Panel")
    assert res["changed"] and res["path"] == "Panel/Label"
    assert N.read_scene(proj, rel)["nodes"][-1]["path"] in (
        "Panel/Label", "Panel/Btn/Icon")  # ordering-tolerant
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Label") is not None


def test_add_node_duplicate_name_errors(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    with pytest.raises(RuntimeError):
        N.add_node(proj, rel, "Btn", "Button", "Panel")


def test_remove_node_drops_descendants(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.remove_node(proj, rel, "Panel/Btn")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Btn") is None
    assert f.find("Panel/Btn/Icon") is None
    assert f.find("Panel") is not None


def test_remove_root_errors(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    with pytest.raises(RuntimeError):
        N.remove_node(proj, rel, ".")


def test_move_node_index(tmp_scene):
    scene = (
        "[gd_scene format=3]\n\n"
        '[node name="Root" type="Node2D"]\n\n'
        '[node name="A" type="Node" parent="."]\n\n'
        '[node name="B" type="Node" parent="."]\n\n'
        '[node name="C" type="Node" parent="."]\n'
    )
    proj, rel = tmp_scene(scene)
    res = N.move_node(proj, rel, "C", 0)
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    kids = [c.name for c in f.children_of(".")]
    assert kids[0] == "C"


# ──────────────────────────────────────────────────────────────────────
# rename (the dangerous one)
# ──────────────────────────────────────────────────────────────────────

def test_rename_updates_descendant_parents(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.rename_node(proj, rel, "Panel/Btn", "OkButton")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/OkButton") is not None
    assert f.find("Panel/OkButton/Icon") is not None
    assert f.find("Panel/Btn") is None


def test_rename_updates_connections_and_nodepaths(tmp_scene):
    scene = (
        "[gd_scene format=3]\n\n"
        '[node name="Root" type="Node2D"]\n'
        'follow = NodePath("Panel/Btn")\n\n'
        '[node name="Panel" type="Panel" parent="."]\n\n'
        '[node name="Btn" type="Button" parent="Panel"]\n\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="_on_pressed"]\n'
    )
    proj, rel = tmp_scene(scene)
    N.rename_node(proj, rel, "Panel/Btn", "OkButton")
    f = TscnFile.parse(_read(proj, rel))
    # connection rewritten
    assert f.connections[0].from_ == "Panel/OkButton"
    # NodePath component rewritten (not substring)
    assert f.root().props["follow"] == 'NodePath("Panel/OkButton")'


def test_rename_nodepath_component_not_substring(tmp_scene):
    scene = (
        "[gd_scene format=3]\n\n"
        '[node name="Root" type="Node2D"]\n'
        'a = NodePath("Btn")\n'
        'b = NodePath("BtnExtra")\n\n'
        '[node name="Btn" type="Button" parent="."]\n\n'
        '[node name="BtnExtra" type="Button" parent="."]\n'
    )
    proj, rel = tmp_scene(scene)
    N.rename_node(proj, rel, "Btn", "Ok")
    f = TscnFile.parse(_read(proj, rel))
    assert f.root().props["a"] == 'NodePath("Ok")'
    assert f.root().props["b"] == 'NodePath("BtnExtra")'  # untouched


def test_rename_sibling_collision_errors(tmp_scene):
    scene = (
        "[gd_scene format=3]\n\n"
        '[node name="Root" type="Node2D"]\n\n'
        '[node name="A" type="Node" parent="."]\n\n'
        '[node name="B" type="Node" parent="."]\n'
    )
    proj, rel = tmp_scene(scene)
    with pytest.raises(RuntimeError):
        N.rename_node(proj, rel, "A", "B")


# ──────────────────────────────────────────────────────────────────────
# reparent
# ──────────────────────────────────────────────────────────────────────

def test_reparent_updates_paths(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    # move Panel/Btn (with child Icon) to be a direct child of root
    res = N.reparent_node(proj, rel, "Panel/Btn", ".")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Btn") is not None
    assert f.find("Btn/Icon") is not None
    assert f.find("Panel/Btn") is None


def test_reparent_fixes_connections(tmp_scene):
    scene = (
        "[gd_scene format=3]\n\n"
        '[node name="Root" type="Node2D"]\n\n'
        '[node name="Panel" type="Panel" parent="."]\n\n'
        '[node name="Btn" type="Button" parent="Panel"]\n\n'
        '[connection signal="pressed" from="Panel/Btn" to="." method="_on_p"]\n'
    )
    proj, rel = tmp_scene(scene)
    N.reparent_node(proj, rel, "Panel/Btn", ".")
    f = TscnFile.parse(_read(proj, rel))
    assert f.connections[0].from_ == "Btn"


def test_reparent_under_self_errors(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    with pytest.raises(RuntimeError):
        N.reparent_node(proj, rel, "Panel", "Panel/Btn")


# ──────────────────────────────────────────────────────────────────────
# duplicate
# ──────────────────────────────────────────────────────────────────────

def test_duplicate_clones_subtree(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.duplicate_node(proj, rel, "Panel/Btn", "Btn2")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Btn2") is not None
    assert f.find("Panel/Btn2/Icon") is not None
    # original intact
    assert f.find("Panel/Btn/Icon") is not None


def test_duplicate_auto_name(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.duplicate_node(proj, rel, "Panel/Btn")
    f = TscnFile.parse(_read(proj, rel))
    assert f.find(res["new_path"]) is not None


# ──────────────────────────────────────────────────────────────────────
# props
# ──────────────────────────────────────────────────────────────────────

def test_set_prop_value_verbatim(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.set_prop(proj, rel, "Panel/Btn", "position", value="Vector2(10, 20)")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Btn").props["position"] == "Vector2(10, 20)"


def test_set_prop_kind_conversion(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    N.set_prop(proj, rel, "Panel/Btn", "position", value="10,20", kind="vector2")
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Btn").props["position"] == "Vector2(10, 20)"


def test_set_prop_ext_resource(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.set_prop(proj, rel, "Panel/Btn", "icon",
                     ext_resource="res://icon.png:Texture2D")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    val = f.find("Panel/Btn").props["icon"]
    assert val.startswith("ExtResource(")
    assert any(e.path == "res://icon.png" and e.type == "Texture2D" for e in f.ext)


def test_set_prop_sub_resource(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.set_prop(proj, rel, "Panel/Btn", "shape",
                     sub_resource="RectangleShape2D:size=Vector2(32, 48)")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    val = f.find("Panel/Btn").props["shape"]
    assert val.startswith("SubResource(")
    assert f.sub[0].type == "RectangleShape2D"
    assert f.sub[0].props["size"] == "Vector2(32, 48)"


def test_get_prop(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    N.set_prop(proj, rel, "Panel/Btn", "position", value="Vector2(10, 20)")
    res = N.get_prop(proj, rel, "Panel/Btn", "position")
    assert res["raw"] == "Vector2(10, 20)"
    assert res["value"] == (10, 20)


def test_set_prop_exclusive_args(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    with pytest.raises(RuntimeError):
        N.set_prop(proj, rel, "Panel/Btn", "x", value="1", ext_resource="res://y")


# ──────────────────────────────────────────────────────────────────────
# script / groups
# ──────────────────────────────────────────────────────────────────────

def test_attach_script(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.attach_script(proj, rel, "Panel/Btn", "res://btn.gd")
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    assert f.find("Panel/Btn").props["script"].startswith("ExtResource(")
    assert any(e.type == "Script" and e.path == "res://btn.gd" for e in f.ext)


def test_add_remove_group(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    N.add_to_group(proj, rel, "Panel/Btn", "buttons")
    f = TscnFile.parse(_read(proj, rel))
    assert "buttons" in f.find("Panel/Btn").groups
    N.remove_from_group(proj, rel, "Panel/Btn", "buttons")
    f = TscnFile.parse(_read(proj, rel))
    assert "buttons" not in f.find("Panel/Btn").groups


# ──────────────────────────────────────────────────────────────────────
# instancing
# ──────────────────────────────────────────────────────────────────────

def test_instance_scene(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = N.instance_scene(proj, rel, "res://Enemy.tscn", "E1", ".",
                           props=["position=Vector2(400, 300)"])
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    nd = f.find("E1")
    assert nd.instance is not None and nd.type is None
    assert nd.props["position"] == "Vector2(400, 300)"
    assert any(e.type == "PackedScene" for e in f.ext)


def test_make_editable_and_override(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    N.instance_scene(proj, rel, "res://Enemy.tscn", "E1", ".")
    N.make_editable(proj, rel, "E1")
    f = TscnFile.parse(_read(proj, rel))
    assert "E1" in f.editables
    N.override_child(proj, rel, "E1", "Sprite2D", ["modulate=Color(1, 0, 0, 1)"])
    f = TscnFile.parse(_read(proj, rel))
    ov = f.find("E1/Sprite2D")
    assert ov is not None and ov.type is None
    assert ov.props["modulate"] == "Color(1, 0, 0, 1)"


# ──────────────────────────────────────────────────────────────────────
# signals
# ──────────────────────────────────────────────────────────────────────

def test_signal_connect_default_flags_omitted(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    res = S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_pressed", flags=2)
    assert res["changed"]
    f = TscnFile.parse(_read(proj, rel))
    # flags == PERSIST(2) -> not stored
    assert f.connections[0].flags is None
    assert "flags" not in _read(proj, rel).split("[connection")[1]


def test_signal_connect_nondefault_flags_and_unbinds(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_p",
              flags=3, unbinds=1, binds="[42]")
    f = TscnFile.parse(_read(proj, rel))
    c = f.connections[0]
    assert c.flags == 3 and c.unbinds == 1 and c.binds == "[42]"


def test_signal_connect_idempotent(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_p")
    res2 = S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_p")
    assert res2["changed"] is False
    f = TscnFile.parse(_read(proj, rel))
    assert len(f.connections) == 1


def test_signal_disconnect_by_fields(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_p")
    res = S.disconnect(proj, rel, signal="pressed", from_="Panel/Btn",
                       to=".", method="_on_p")
    assert res["changed"]
    assert TscnFile.parse(_read(proj, rel)).connections == []


def test_signal_disconnect_by_index(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_a")
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_b")
    S.disconnect(proj, rel, index=0)
    f = TscnFile.parse(_read(proj, rel))
    assert len(f.connections) == 1 and f.connections[0].method == "_on_b"


def test_signal_list(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    S.connect(proj, rel, "pressed", "Panel/Btn", ".", "_on_p")
    res = S.list_signals(proj, rel)
    assert len(res["connections"]) == 1
    assert res["connections"][0]["from"] == "Panel/Btn"


def test_signal_connect_bad_node_errors(tmp_scene):
    proj, rel = tmp_scene(BASE_SCENE)
    with pytest.raises(RuntimeError):
        S.connect(proj, rel, "pressed", "NoSuch", ".", "_on_p")


# ══════════════════════════════════════════════════════════════════════
# Engine validation (real Godot 4.3 — no skip)
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def scratch_project():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    proj = SCRATCH / f"nodes_{uuid.uuid4().hex[:8]}"
    proj.mkdir()
    cf = ConfigFile()
    cf.set("", "config_version", "5")
    cf.set("application", "config/name", '"NodesTest"')
    cf.set("application", "config/features",
           'PackedStringArray("4.3", "GL Compatibility")')
    cf.set("rendering", "renderer/rendering_method", '"gl_compatibility"')
    cf.save(str(proj / "project.godot"))
    yield proj
    import shutil
    shutil.rmtree(proj, ignore_errors=True)


def _load_and_count(proj: Path, res_path: str, expect_marker: str = "OK") -> dict:
    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        f'    var p = load("{res_path}")\n'
        "    if p == null:\n"
        '        push_error("LOAD_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    var i = p.instantiate()\n"
        "    if i == null:\n"
        '        push_error("INSTANTIATE_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        '    print("OK total=", i.get_child_count())\n'
        '    print("NAMES=", i.find_children("*", "", true, false).map('
        "func(n): return n.name))\n"
        "    quit(0)\n"
    )
    result = run_generated_script(str(proj), runner, timeout=120)
    return result


def test_engine_available():
    assert is_available(), "Godot 4.3 binary must be reachable via GODOT_BIN"


def test_engine_add_setprop_signal_loads(scratch_project):
    """add -> set-prop -> signal connect -> save loads OK in the engine."""
    proj = scratch_project
    (proj / "btn.gd").write_text("extends Button\n", encoding="utf-8")
    rel = "Built.tscn"

    f = TscnFile.new_scene("Control", "UI")
    (proj / rel).write_text(f.serialize(), encoding="utf-8")
    project = str(proj)

    N.add_node(project, rel, "Panel", "Panel", ".")
    N.add_node(project, rel, "Ok", "Button", "Panel")
    N.set_prop(project, rel, "Panel/Ok", "text", value='"Confirm"')
    N.set_prop(project, rel, "Panel/Ok", "position", value="10,20", kind="vector2")
    N.attach_script(project, rel, "Panel/Ok", "res://btn.gd")
    S.connect(project, rel, "pressed", "Panel/Ok", ".", "_on_ok")

    result = _load_and_count(proj, "res://Built.tscn")
    combined = result["stdout"] + result["stderr"]
    assert "LOAD_FAILED" not in combined and "INSTANTIATE_FAILED" not in combined, (
        f"rc={result['returncode']}\nscene:\n{_read(project, rel)}\n"
        f"stdout:\n{result['stdout']}\nstderr:\n{result['stderr']}")
    assert "OK total=" in result["stdout"], result["stdout"] + result["stderr"]
    print("ENGINE add/set/signal proof:\n" + result["stdout"])


def test_engine_rename_keeps_references(scratch_project):
    """A rename updates parents+connections and the scene still loads with the new name."""
    proj = scratch_project
    rel = "Ren.tscn"
    project = str(proj)

    f = TscnFile.new_scene("Node2D", "Root")
    (proj / rel).write_text(f.serialize(), encoding="utf-8")
    N.add_node(project, rel, "Panel", "Panel", ".")
    N.add_node(project, rel, "Btn", "Button", "Panel")
    N.add_node(project, rel, "Icon", "Sprite2D", "Panel/Btn")
    S.connect(project, rel, "pressed", "Panel/Btn", ".", "_on_btn")
    N.rename_node(project, rel, "Panel/Btn", "OkButton")

    # verify references rewritten in file
    f2 = TscnFile.parse(_read(project, rel))
    assert f2.find("Panel/OkButton/Icon") is not None
    assert f2.connections[0].from_ == "Panel/OkButton"

    result = _load_and_count(proj, "res://Ren.tscn")
    combined = result["stdout"] + result["stderr"]
    assert "LOAD_FAILED" not in combined and "INSTANTIATE_FAILED" not in combined, (
        f"rc={result['returncode']}\nscene:\n{_read(project, rel)}\n"
        f"stderr:\n{result['stderr']}")
    assert "OkButton" in result["stdout"], result["stdout"] + result["stderr"]
    print("ENGINE rename proof:\n" + result["stdout"])


def test_engine_instance_loads_with_child(scratch_project):
    """An instanced scene loads with the child scene's nodes present."""
    proj = scratch_project
    project = str(proj)

    # child scene
    child = TscnFile.new_scene("Node2D", "Enemy")
    (proj / "child.tscn").write_text(child.serialize(), encoding="utf-8")
    N.add_node(project, "child.tscn", "Sprite2D", "Sprite2D", ".")

    # parent scene instancing the child
    parent = TscnFile.new_scene("Node2D", "Level")
    (proj / "level.tscn").write_text(parent.serialize(), encoding="utf-8")
    N.instance_scene(project, "level.tscn", "res://child.tscn", "E1", ".",
                     props=["position=Vector2(100, 100)"])

    runner = (
        "extends SceneTree\n"
        "func _init():\n"
        '    var p = load("res://level.tscn")\n'
        "    if p == null:\n"
        '        push_error("LOAD_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    var i = p.instantiate()\n"
        '    var e = i.get_node_or_null("E1")\n'
        "    if e == null:\n"
        '        push_error("NO_INSTANCE")\n'
        "        quit(1)\n"
        "        return\n"
        '    var spr = e.get_node_or_null("Sprite2D")\n'
        '    print("INST_OK child=", spr != null, " pos=", e.position)\n'
        "    quit(0)\n"
    )
    result = run_generated_script(project, runner, timeout=120)
    combined = result["stdout"] + result["stderr"]
    assert "LOAD_FAILED" not in combined and "NO_INSTANCE" not in combined, (
        f"rc={result['returncode']}\nstdout:\n{result['stdout']}\n"
        f"stderr:\n{result['stderr']}")
    assert "INST_OK child=true" in result["stdout"], (
        result["stdout"] + result["stderr"])
    print("ENGINE instance proof:\n" + result["stdout"])


def test_engine_repack_normalizes(scratch_project):
    """scene repack loads+packs+saves a hand-built scene via the engine."""
    proj = scratch_project
    project = str(proj)
    rel = "Repack.tscn"
    f = TscnFile.new_scene("Node2D", "Root")
    (proj / rel).write_text(f.serialize(), encoding="utf-8")
    N.add_node(project, rel, "Child", "Sprite2D", ".")
    N.set_prop(project, rel, "Child", "position", value="5,5", kind="vector2")

    res = N.repack_scene(project, rel)
    assert res["status"] == "ok"
    # after repack it must still load
    result = _load_and_count(proj, "res://Repack.tscn")
    assert "OK total=" in result["stdout"], result["stdout"] + result["stderr"]
    print("ENGINE repack proof:\n" + res["stdout"])
