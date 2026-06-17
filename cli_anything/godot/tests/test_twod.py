"""Tests for the TWOD (2D conveniences) layer: core/twod.py.

FILE-based ops (sprite/camera/body/collision/physics-layer/animationplayer/
tilemap-add) are validated by building a scene in a scratch project and having
real Godot 4.3 load() + instantiate() it via a headless SceneTree script that
prints node classes / shapes — then asserting on that output.

SCRIPT ops (anim create, anim add-track, tileset create, tilemap paint) are run
for real and their artifacts re-loaded in a headless script (animation length /
loop / track count / tile count) and asserted.

Engine-backed throughout, using GODOT_BIN (no graceful skip — per the contract).
Tileset/tilemap tests generate a tiny PNG and --import it first.
"""

import os
import struct
import uuid
import zlib
import shutil
from pathlib import Path

import pytest

from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.core.tscn import TscnFile
from cli_anything.godot.core import twod
from cli_anything.godot.utils.godot_backend import (
    is_available,
    run_generated_script,
)


SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch")


# ──────────────────────────────────────────────────────────────────────
# fixtures / helpers
# ──────────────────────────────────────────────────────────────────────

def _write_png(path: Path, w: int = 32, h: int = 32):
    """Write a minimal valid RGBA PNG (solid magenta) without external deps."""
    def chunk(typ: bytes, data: bytes) -> bytes:
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = bytearray()
    for _ in range(h):
        raw.append(0)  # filter type 0
        for _ in range(w):
            raw += bytes((255, 0, 255, 255))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    idat = zlib.compress(bytes(raw), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png)


@pytest.fixture
def scratch_project():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    proj = SCRATCH / f"twod_{uuid.uuid4().hex[:8]}"
    proj.mkdir()
    cf = ConfigFile()
    cf.set("", "config_version", "5")
    cf.set("application", "config/name", '"TwodTest"')
    cf.set("application", "config/features",
           'PackedStringArray("4.3", "GL Compatibility")')
    cf.set("rendering", "renderer/rendering_method", '"gl_compatibility"')
    cf.save(str(proj / "project.godot"))
    yield proj
    shutil.rmtree(proj, ignore_errors=True)


def _new_scene(proj: Path, name: str = "Test.tscn", root_type: str = "Node2D",
               root_name: str = "Root") -> str:
    f = TscnFile.new_scene(root_type, root_name)
    path = proj / name
    path.write_text(f.serialize(), encoding="utf-8")
    return str(path)


def _inspect(proj: Path, scene_res: str, gd_body: str, marker: str = "OK",
             timeout: int = 120) -> dict:
    """Run a headless SceneTree that loads scene_res and runs gd_body, returning
    the run dict. gd_body has access to `root` (instantiated scene root)."""
    src = (
        "extends SceneTree\n"
        "func _init():\n"
        f'    var ps = load("{scene_res}")\n'
        "    if ps == null:\n"
        '        push_error("LOAD_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    var root = ps.instantiate()\n"
        + gd_body +
        "    quit(0)\n"
    )
    res = run_generated_script(str(proj), src, timeout=timeout)
    combined = res["stdout"] + res["stderr"]
    assert "LOAD_FAILED" not in combined, combined
    assert marker in res["stdout"], (
        f"marker {marker!r} missing.\nrc={res['returncode']}\n"
        f"--- stdout ---\n{res['stdout']}\n--- stderr ---\n{res['stderr']}"
    )
    return res


# ──────────────────────────────────────────────────────────────────────
# pure-ish unit checks (no engine)
# ──────────────────────────────────────────────────────────────────────

def test_engine_available():
    assert is_available(), "Godot 4.3 binary must be reachable via GODOT_BIN"


def test_add_body_bad_type(tmp_path):
    scene = tmp_path / "s.tscn"
    scene.write_text(TscnFile.new_scene("Node2D", "Root").serialize(), encoding="utf-8")
    with pytest.raises(RuntimeError):
        twod.add_body(str(scene), "X", "NotABody", ".")


def test_add_sprite_idempotent(tmp_path):
    scene = tmp_path / "s.tscn"
    scene.write_text(TscnFile.new_scene("Node2D", "Root").serialize(), encoding="utf-8")
    r1 = twod.add_sprite(str(scene), "Hero", ".", texture="res://x.png")
    assert r1["changed"] is True
    r2 = twod.add_sprite(str(scene), "Hero", ".", texture="res://x.png")
    assert r2["changed"] is False


def test_build_shape_specs():
    assert twod._build_shape("rectangle:size=Vector2(16, 24)") == (
        "RectangleShape2D", {"size": "Vector2(16, 24)"})
    t, p = twod._build_shape("circle:radius=16")
    assert t == "CircleShape2D" and p["radius"] == "16.0"
    t, p = twod._build_shape("capsule:radius=8;height=32")
    assert t == "CapsuleShape2D" and p["radius"] == "8.0" and p["height"] == "32.0"


def test_parse_bitmask():
    assert twod._parse_bitmask("0b0110") == 6
    assert twod._parse_bitmask("0x0A") == 10
    assert twod._parse_bitmask(3) == 3
    assert twod._parse_bitmask("5") == 5


# ──────────────────────────────────────────────────────────────────────
# FILE ops — engine-validated
# ──────────────────────────────────────────────────────────────────────

def test_sprite_camera_body_collision_engine(scratch_project):
    """Sprite2D + Camera2D + body + collision all in one scene; engine validates
    classes and the CollisionShape2D's RectangleShape2D size."""
    proj = scratch_project
    _write_png(proj / "hero.png")
    scene = _new_scene(proj)

    r = twod.add_sprite(scene, "Hero", ".", texture="res://hero.png",
                        region="0,0,16,16")
    assert r["changed"]
    twod.add_camera(scene, "Cam", ".", current=True, zoom="2,2")
    twod.add_body(scene, "Player", "CharacterBody2D", ".")
    rc = twod.add_collision(scene, "Col", "Player",
                            shape="rectangle:size=Vector2(16, 24)")
    assert rc["shape_type"] == "RectangleShape2D"

    body = (
        '    var hero = root.get_node("Hero")\n'
        '    var cam = root.get_node("Cam")\n'
        '    var player = root.get_node("Player")\n'
        '    var col = root.get_node("Player/Col")\n'
        '    print("HERO ", hero.get_class())\n'
        '    print("CAM ", cam.get_class())\n'
        '    print("PLAYER ", player.get_class())\n'
        '    print("COL ", col.get_class())\n'
        '    print("SHAPE ", col.shape.get_class())\n'
        '    print("SIZE ", col.shape.size)\n'
        '    print("REGION ", hero.region_enabled, " ", hero.region_rect)\n'
        '    print("ZOOM ", cam.zoom)\n'
        '    print("OK")\n'
    )
    res = _inspect(proj, "res://Test.tscn", body)
    out = res["stdout"]
    assert "HERO Sprite2D" in out
    assert "CAM Camera2D" in out
    assert "PLAYER CharacterBody2D" in out
    assert "COL CollisionShape2D" in out
    assert "SHAPE RectangleShape2D" in out
    assert "SIZE (16, 24)" in out
    assert "REGION true [P: (0, 0), S: (16, 16)]" in out
    assert "ZOOM (2, 2)" in out


def test_collision_circle_and_capsule(scratch_project):
    proj = scratch_project
    scene = _new_scene(proj, "Shapes.tscn")
    twod.add_body(scene, "B", "StaticBody2D", ".")
    twod.add_collision(scene, "C1", "B", shape="circle:radius=12")
    twod.add_collision(scene, "C2", "B", shape="capsule:radius=8;height=40")
    body = (
        '    var c1 = root.get_node("B/C1")\n'
        '    var c2 = root.get_node("B/C2")\n'
        '    print("C1 ", c1.shape.get_class(), " ", c1.shape.radius)\n'
        '    print("C2 ", c2.shape.get_class(), " ", c2.shape.radius, " ", c2.shape.height)\n'
        '    print("OK")\n'
    )
    out = _inspect(proj, "res://Shapes.tscn", body)["stdout"]
    assert "C1 CircleShape2D 12" in out
    assert "C2 CapsuleShape2D 8 40" in out


def test_set_physics_layer(scratch_project):
    proj = scratch_project
    scene = _new_scene(proj, "Phys.tscn")
    twod.add_body(scene, "P", "CharacterBody2D", ".")
    r = twod.set_physics_layer(scene, "P", collision_layer="0b0001",
                               collision_mask="0b0110")
    assert r["changed"] and r["collision_layer"] == 1 and r["collision_mask"] == 6
    # idempotent
    r2 = twod.set_physics_layer(scene, "P", collision_layer=1, collision_mask=6)
    assert r2["changed"] is False

    body = (
        '    var p = root.get_node("P")\n'
        '    print("LAYER ", p.collision_layer)\n'
        '    print("MASK ", p.collision_mask)\n'
        '    print("OK")\n'
    )
    out = _inspect(proj, "res://Phys.tscn", body)["stdout"]
    assert "LAYER 1" in out
    assert "MASK 6" in out


def test_animationplayer_node(scratch_project):
    proj = scratch_project
    scene = _new_scene(proj, "Anim.tscn")
    twod.add_animationplayer(scene, "AnimPlayer", ".")
    body = (
        '    var ap = root.get_node("AnimPlayer")\n'
        '    print("AP ", ap.get_class())\n'
        '    print("OK")\n'
    )
    out = _inspect(proj, "res://Anim.tscn", body)["stdout"]
    assert "AP AnimationPlayer" in out


# ──────────────────────────────────────────────────────────────────────
# SCRIPT ops — engine-backed, artifacts re-loaded & asserted
# ──────────────────────────────────────────────────────────────────────

def test_tileset_create_and_tilemap(scratch_project):
    """tileset create -> .tres with N tiles; tilemap add -> TileMapLayer wired;
    tilemap paint -> cells set. Re-load and assert tile/cell counts."""
    proj = scratch_project
    _write_png(proj / "tiles.png", 32, 32)  # 32x32 with 16x16 tiles => 4 tiles
    tres = str(proj / "world.tres")

    r = twod.tileset_create(tres, "res://tiles.png", "16,16")
    assert r["tiles_created"] == 4, r

    # tilemap add (FILE)
    scene = _new_scene(proj, "Level.tscn")
    ra = twod.tilemap_add(scene, "TM", ".", tileset="res://world.tres")
    assert ra["type"] == "TileMapLayer"

    # validate the TileMapLayer loads with the tileset
    body = (
        '    var tm = root.get_node("TM")\n'
        '    print("TM ", tm.get_class())\n'
        '    print("HASTS ", tm.tile_set != null)\n'
        '    print("SOURCES ", tm.tile_set.get_source_count())\n'
        '    print("OK")\n'
    )
    out = _inspect(proj, "res://Level.tscn", body)["stdout"]
    assert "TM TileMapLayer" in out
    assert "HASTS true" in out
    assert "SOURCES 1" in out

    # tilemap paint (SCRIPT)
    rp = twod.tilemap_paint(scene, "TM", ["0,0=0:0,0", "1,0=0:1,0", "0,1=0:0,1"])
    assert rp["used_cells"] == 3, rp

    out2 = _inspect(proj, "res://Level.tscn",
                    '    var tm = root.get_node("TM")\n'
                    '    print("USED ", tm.get_used_cells().size())\n'
                    '    print("OK")\n')["stdout"]
    assert "USED 3" in out2


def test_anim_create_and_track(scratch_project):
    """anim create -> Animation in a library wired to the player; anim add-track
    -> a value track with keys. Re-load library and assert length/loop/tracks."""
    proj = scratch_project
    scene = _new_scene(proj, "Hero.tscn")
    twod.add_sprite(scene, "Sprite2D", ".", texture="res://hero.png")
    _write_png(proj / "hero.png")
    twod.add_animationplayer(scene, "AnimPlayer", ".")

    rc = twod.anim_create(scene, "AnimPlayer", "walk", 0.6, loop=True)
    assert rc["changed"]
    lib_abs = rc["library"]
    assert os.path.isfile(lib_abs), lib_abs
    lib_key = rc["library_key"]

    rt = twod.anim_add_track(scene, "AnimPlayer", "walk", "value",
                             "Sprite2D:frame",
                             [(0.0, 0), (0.3, 1), (0.6, 2)])
    assert rt["keys"] == 3

    # Load the library directly and assert.
    lib_res = "res://" + os.path.relpath(lib_abs, proj).replace(os.sep, "/")
    src = (
        "extends SceneTree\n"
        "func _init():\n"
        f'    var lib = ResourceLoader.load("{lib_res}", "", ResourceLoader.CACHE_MODE_IGNORE)\n'
        "    if lib == null:\n"
        '        push_error("NOLIB")\n        quit(1)\n        return\n'
        '    var a = lib.get_animation("walk")\n'
        '    print("LEN ", a.length)\n'
        '    print("LOOP ", a.loop_mode)\n'
        '    print("TRACKS ", a.get_track_count())\n'
        '    print("TPATH ", a.track_get_path(0))\n'
        '    print("KEYS ", a.track_get_key_count(0))\n'
        '    print("OK")\n'
        "    quit(0)\n"
    )
    res = run_generated_script(str(proj), src, timeout=120)
    out = res["stdout"]
    assert "OK" in out, res["stdout"] + res["stderr"]
    assert "LEN 0.6" in out
    assert "LOOP 1" in out
    assert "TRACKS 1" in out
    assert "TPATH Sprite2D:frame" in out
    assert "KEYS 3" in out

    # And the player picked up the library.
    body = (
        '    var ap = root.get_node("AnimPlayer")\n'
        f'    print("HASLIB ", ap.has_animation_library("{lib_key}"))\n'
        f'    print("HASANIM ", ap.has_animation("{lib_key}/walk"))\n'
        '    print("OK")\n'
    )
    out2 = _inspect(proj, "res://Hero.tscn", body)["stdout"]
    assert "HASLIB true" in out2
    assert "HASANIM true" in out2
