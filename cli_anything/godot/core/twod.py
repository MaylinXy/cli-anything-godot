"""2D-specific convenience operations (offline layer).

These bundle several low-level steps into single, ergonomic operations on a
scene (`.tscn`) or resource (`.tres`) file. They build directly on the FOUND
``TscnFile`` / ``variant_fmt`` / ``godot_backend`` libraries (NOT on
``core/nodes.py``, which is owned by another agent).

Target: Godot **4.3** (``format=3``). Notable 4.3 facts honoured here:

  - TileMap is replaced by **TileMapLayer** (one node per layer); the tileset is
    referenced via ``tile_set = ExtResource(id)``.
  - Animation / AnimationLibrary / TileSet are complex typed resources whose text
    form is order-sensitive and easy to corrupt, so they are produced through the
    engine (SCRIPT mechanism: build via the class API + ``ResourceSaver.save``).

Mechanism split (see SPEC-offline §C.4):

  FILE   — add_sprite, add_camera, add_body, add_collision, set_physics_layer,
           add_animationplayer, tilemap_add
  SCRIPT — anim_create, anim_add_track, tileset_create, tilemap_paint

Every public function returns a dict with at least ``status`` and ``changed``.
Core functions never print or ``sys.exit``; they raise ``RuntimeError`` on
failure (the command layer formats it).
"""

from __future__ import annotations

import os
import re

from cli_anything.godot.core.tscn import TscnFile
from cli_anything.godot.core import variant_fmt
from cli_anything.godot.utils import godot_backend


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────

_BODY_TYPES = {"CharacterBody2D", "RigidBody2D", "StaticBody2D", "Area2D"}


def _read_scene(path: str) -> TscnFile:
    """Parse a `.tscn` file from disk, raising a clear error if missing/invalid."""
    if not os.path.isfile(path):
        raise RuntimeError(f"Scene file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return TscnFile.parse(fh.read())
    except RuntimeError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to parse scene {path}: {e}") from e


def _write_scene(path: str, scene: TscnFile) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(scene.serialize())


def _resolve_parent(scene: TscnFile, parent: str) -> str:
    """Validate the parent path and return the literal parent= value to use.

    ``parent`` is a scene path EXCLUDING the root name ('.' => root child).
    Raises if the parent does not exist.
    """
    parent = (parent or ".").strip()
    if scene.find(parent) is None:
        raise RuntimeError(
            f"Parent node not found in scene: {parent!r} "
            "(use '.' for the root, or a path excluding the root name)"
        )
    return parent


def _child_exists(scene: TscnFile, parent: str, name: str):
    """Return an existing child node with ``name`` under ``parent``, else None."""
    for nd in scene.children_of(parent):
        if nd.name == name:
            return nd
    return None


def _parse_bitmask(value) -> int:
    """Accept an int, a plain decimal string, or a ``0b....`` binary string."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.lower().startswith("0b"):
        return int(s, 2)
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 10)


def _project_root_of(path: str) -> str:
    """Walk up from a file path to find the directory containing project.godot."""
    d = os.path.dirname(os.path.abspath(path))
    cur = d
    while True:
        if os.path.isfile(os.path.join(cur, "project.godot")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            # No project found; fall back to the file's own directory.
            return d
        cur = parent


def _to_res_path(project_root: str, abs_or_res: str) -> str:
    """Return a ``res://`` path for a file given the project root."""
    p = abs_or_res
    if p.startswith("res://"):
        return p
    ap = os.path.abspath(p)
    try:
        rel = os.path.relpath(ap, project_root).replace(os.sep, "/")
    except ValueError:
        rel = os.path.basename(ap)
    return "res://" + rel


# ──────────────────────────────────────────────────────────────────────────
# FILE ops
# ──────────────────────────────────────────────────────────────────────────

def add_sprite(scene: str, name: str, parent: str = ".", *, texture: str,
               region: str | None = None) -> dict:
    """Add a ``Sprite2D`` with its texture set, as a child of ``parent``.

    The texture is registered as an ``ext_resource`` of type ``Texture2D`` and
    wired via ``texture = ExtResource(id)``. ``region`` is "x,y,w,h"; when given
    it sets ``region_enabled = true`` and ``region_rect = Rect2(...)``.
    """
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    eid = f.add_ext_resource("Texture2D", texture)
    node = f.add_node(name, "Sprite2D", parent)
    node.props["texture"] = variant_fmt.ext_ref(eid)
    if region:
        parts = [p.strip() for p in str(region).split(",") if p.strip() != ""]
        if len(parts) != 4:
            raise RuntimeError(
                f"--region must be 'x,y,w,h' (got {region!r})")
        node.props["region_enabled"] = "true"
        node.props["region_rect"] = variant_fmt.to_literal(parts, "rect2")

    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name, "type": "Sprite2D",
            "parent": parent, "texture": texture, "ext_resource_id": eid}


def add_camera(scene: str, name: str, parent: str = ".", *, current: bool = False,
               zoom: str | None = None) -> dict:
    """Add a ``Camera2D``. ``current=True`` makes it the active camera; ``zoom``
    is "x,y" -> ``zoom = Vector2(x, y)``."""
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    node = f.add_node(name, "Camera2D", parent)
    if current:
        node.props["enabled"] = "true"  # Camera2D uses 'enabled' for "current" in 4.3
    if zoom:
        node.props["zoom"] = variant_fmt.to_literal(zoom, "vector2")

    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name, "type": "Camera2D",
            "parent": parent, "current": current, "zoom": zoom}


def add_body(scene: str, name: str, type: str, parent: str = ".") -> dict:
    """Add a physics body node. ``type`` in {CharacterBody2D, RigidBody2D,
    StaticBody2D, Area2D}."""
    if type not in _BODY_TYPES:
        raise RuntimeError(
            f"Unknown body type {type!r}; expected one of {sorted(_BODY_TYPES)}")
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    f.add_node(name, type, parent)
    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name, "type": type,
            "parent": parent}


def _build_shape(shape: str):
    """Parse a shape spec like 'rectangle:size=Vector2(32,48)' and return
    (godot_type, props_dict) for a sub_resource.

    Supported:
      rectangle:size=Vector2(w,h)          -> RectangleShape2D
      circle:radius=R                      -> CircleShape2D
      capsule:radius=R;height=H            -> CapsuleShape2D
    Params are separated by ';'. Values are written verbatim as Godot literals,
    except bare numbers which are passed through.
    """
    spec = str(shape).strip()
    if ":" in spec:
        kind, _, rest = spec.partition(":")
    else:
        kind, rest = spec, ""
    kind = kind.strip().lower()

    params: dict[str, str] = {}
    for chunk in rest.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        k, _, v = chunk.partition("=")
        params[k.strip()] = v.strip()

    if kind in ("rectangle", "rect", "rectangleshape2d"):
        size = params.get("size", "Vector2(32, 32)")
        if not size.startswith("Vector2"):
            size = variant_fmt.to_literal(size, "vector2")
        return "RectangleShape2D", {"size": size}
    if kind in ("circle", "circleshape2d"):
        radius = params.get("radius", "16")
        return "CircleShape2D", {"radius": _num_literal(radius)}
    if kind in ("capsule", "capsuleshape2d"):
        radius = params.get("radius", "8")
        height = params.get("height", "32")
        props = {
            "radius": _num_literal(radius),
            "height": _num_literal(height),
        }
        return "CapsuleShape2D", props
    raise RuntimeError(
        f"Unknown shape kind {kind!r}; expected rectangle|circle|capsule")


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _num_literal(s: str) -> str:
    """A numeric param -> a float Godot literal; otherwise verbatim."""
    if _is_number(s):
        return variant_fmt.to_literal(float(s), "float")
    return s


def add_collision(scene: str, name: str, parent: str, *, shape: str) -> dict:
    """Add a ``CollisionShape2D`` with a generated Shape2D sub_resource.

    ``shape`` spec: "rectangle:size=Vector2(32,48)" | "circle:radius=16" |
    "capsule:radius=8;height=32". Writes ``shape = SubResource(id)``.
    """
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    shape_type, props = _build_shape(shape)
    sid = f.add_sub_resource(shape_type, props)
    node = f.add_node(name, "CollisionShape2D", parent)
    node.props["shape"] = variant_fmt.sub_ref(sid)

    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name,
            "type": "CollisionShape2D", "parent": parent,
            "shape_type": shape_type, "sub_resource_id": sid}


def set_physics_layer(scene: str, path: str, *, collision_layer=None,
                      collision_mask=None) -> dict:
    """Set ``collision_layer`` / ``collision_mask`` bitmask props on a node.

    Values may be int, a decimal string, or a ``0b...`` / ``0x...`` string.
    At least one of the two must be given.
    """
    if collision_layer is None and collision_mask is None:
        raise RuntimeError(
            "Provide --collision-layer and/or --collision-mask")
    f = _read_scene(scene)
    node = f.find(path)
    if node is None:
        raise RuntimeError(f"Node not found in scene: {path!r}")

    changed = False
    result = {"status": "ok", "path": path}
    if collision_layer is not None:
        v = _parse_bitmask(collision_layer)
        new = str(v)
        if node.props.get("collision_layer") != new:
            node.props["collision_layer"] = new
            changed = True
        result["collision_layer"] = v
    if collision_mask is not None:
        v = _parse_bitmask(collision_mask)
        new = str(v)
        if node.props.get("collision_mask") != new:
            node.props["collision_mask"] = new
            changed = True
        result["collision_mask"] = v

    if changed:
        _write_scene(scene, f)
    result["changed"] = changed
    return result


def add_animationplayer(scene: str, name: str, parent: str = ".") -> dict:
    """Add an empty ``AnimationPlayer`` node. Use ``anim_create`` to populate a
    library and wire it to this player."""
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    f.add_node(name, "AnimationPlayer", parent)
    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name,
            "type": "AnimationPlayer", "parent": parent}


def tilemap_add(scene: str, name: str, parent: str = ".", *, tileset: str) -> dict:
    """Add a ``TileMapLayer`` (Godot 4.3) with ``tile_set = ExtResource(id)``.

    Note: in 4.3 the monolithic ``TileMap`` is deprecated in favour of one
    ``TileMapLayer`` node per layer; we always create a TileMapLayer.
    """
    f = _read_scene(scene)
    parent = _resolve_parent(f, parent)
    if _child_exists(f, parent, name) is not None:
        return {"status": "ok", "changed": False, "name": name,
                "reason": "node already exists"}

    eid = f.add_ext_resource("TileSet", tileset)
    node = f.add_node(name, "TileMapLayer", parent)
    node.props["tile_set"] = variant_fmt.ext_ref(eid)

    _write_scene(scene, f)
    return {"status": "ok", "changed": True, "name": name, "type": "TileMapLayer",
            "parent": parent, "tileset": tileset, "ext_resource_id": eid}


# ──────────────────────────────────────────────────────────────────────────
# SCRIPT ops (engine-backed): animation library, tileset, tilemap paint
# ──────────────────────────────────────────────────────────────────────────

def _run_script_checked(project_root: str, src: str, *, marker: str,
                        timeout: int = 120) -> dict:
    """Run a generated SceneTree script and assert it printed ``marker``."""
    res = godot_backend.run_generated_script(project_root, src, timeout=timeout)
    combined = (res.get("stdout") or "") + (res.get("stderr") or "")
    if marker not in combined:
        raise RuntimeError(
            "Engine script did not complete successfully.\n"
            f"rc={res.get('returncode')}\n--- stdout ---\n{res.get('stdout')}\n"
            f"--- stderr ---\n{res.get('stderr')}"
        )
    return res


def anim_create(scene: str, player: str, name: str, length: float,
                loop: bool = False, *, library: str | None = None) -> dict:
    """Create an Animation called ``name`` inside an AnimationLibrary ``.tres``
    and wire that library into the AnimationPlayer ``player`` of ``scene``.

    SCRIPT mechanism: builds/loads the AnimationLibrary via the class API and
    saves it with ``ResourceSaver.save``, then loads the scene, calls
    ``AnimationPlayer.add_animation_library`` (re-loading the saved library),
    re-packs and saves the scene.

    The library ``.tres`` defaults to ``<scene-dir>/<player>_lib.tres`` (a
    ``res://`` sibling of the scene). The player's animations become addressable
    as ``"<libname>/<name>"`` where libname is the file stem (or "" if you wire
    it as the default library — here we use the file stem as the library key).

    Returns a dict including the absolute library path so callers know where it
    landed.
    """
    if not os.path.isfile(scene):
        raise RuntimeError(f"Scene file not found: {scene}")
    project_root = _project_root_of(scene)
    scene_res = _to_res_path(project_root, scene)

    if library is None:
        base = os.path.splitext(os.path.basename(scene))[0]
        lib_abs = os.path.join(os.path.dirname(os.path.abspath(scene)),
                               f"{base}_{player}_lib.tres")
    else:
        lib_abs = os.path.abspath(library)
    lib_res = _to_res_path(project_root, lib_abs)
    lib_key = os.path.splitext(os.path.basename(lib_abs))[0]

    loop_mode = 1 if loop else 0  # Animation.LOOP_LINEAR=1, LOOP_NONE=0
    src = f'''extends SceneTree
func _init():
    var lib_path = "{lib_res}"
    var lib: AnimationLibrary
    if ResourceLoader.exists(lib_path):
        lib = ResourceLoader.load(lib_path)
        if lib == null:
            lib = AnimationLibrary.new()
    else:
        lib = AnimationLibrary.new()
    var anim_name = "{name}"
    var anim: Animation
    if lib.has_animation(anim_name):
        anim = lib.get_animation(anim_name)
    else:
        anim = Animation.new()
        lib.add_animation(anim_name, anim)
    anim.length = {float(length)}
    anim.loop_mode = {loop_mode}
    var rc = ResourceSaver.save(lib, lib_path)
    if rc != OK:
        push_error("SAVE_LIB_FAILED %d" % rc)
        quit(1)
        return
    # Wire the library into the AnimationPlayer and re-save the scene.
    var ps = load("{scene_res}")
    if ps == null:
        push_error("SCENE_LOAD_FAILED")
        quit(1)
        return
    var root = ps.instantiate()
    var ap = root.find_child("{player}", true, false)
    if ap == null or not (ap is AnimationPlayer):
        push_error("PLAYER_NOT_FOUND")
        quit(1)
        return
    var fresh = ResourceLoader.load(lib_path, "", ResourceLoader.CACHE_MODE_IGNORE)
    if ap.has_animation_library("{lib_key}"):
        ap.remove_animation_library("{lib_key}")
    ap.add_animation_library("{lib_key}", fresh)
    # Re-pack: every saved node's owner must be the scene root.
    _set_owner(root, root)
    var packed = PackedScene.new()
    if packed.pack(root) != OK:
        push_error("PACK_FAILED")
        quit(1)
        return
    if ResourceSaver.save(packed, "{scene_res}") != OK:
        push_error("SCENE_SAVE_FAILED")
        quit(1)
        return
    print("ANIM_CREATE_OK ", anim.length, " ", anim.loop_mode)
    quit(0)

func _set_owner(node, owner_root):
    for c in node.get_children():
        if node != owner_root:
            node.owner = owner_root
        _set_owner(c, owner_root)
'''
    _run_script_checked(project_root, src, marker="ANIM_CREATE_OK")
    return {"status": "ok", "changed": True, "player": player, "anim": name,
            "length": float(length), "loop": loop, "library": lib_abs,
            "library_key": lib_key,
            "address": f"{lib_key}/{name}"}


def anim_add_track(scene: str, player: str, anim: str, track_type: str,
                   path: str, keys: list[tuple], *,
                   library: str | None = None) -> dict:
    """Add a track (with keyframes) to an existing animation in the library.

    ``track_type`` in {value, method}; ``path`` is a node-property path like
    "Sprite2D:frame"; ``keys`` is a list of (time, value) tuples. For method
    tracks each value is treated as a method name (no args).

    SCRIPT mechanism: loads the library `.tres`, mutates the Animation, saves it,
    and re-loads it into the player (so the scene picks up the change).
    """
    if not os.path.isfile(scene):
        raise RuntimeError(f"Scene file not found: {scene}")
    project_root = _project_root_of(scene)
    scene_res = _to_res_path(project_root, scene)

    if library is None:
        base = os.path.splitext(os.path.basename(scene))[0]
        lib_abs = os.path.join(os.path.dirname(os.path.abspath(scene)),
                               f"{base}_{player}_lib.tres")
    else:
        lib_abs = os.path.abspath(library)
    if not os.path.isfile(lib_abs):
        raise RuntimeError(
            f"Animation library not found: {lib_abs} "
            "(run 'anim create' first)")
    lib_res = _to_res_path(project_root, lib_abs)
    lib_key = os.path.splitext(os.path.basename(lib_abs))[0]

    tt = track_type.strip().lower()
    if tt not in ("value", "method"):
        raise RuntimeError("--track-type must be 'value' or 'method'")
    track_type_const = "Animation.TYPE_VALUE" if tt == "value" else "Animation.TYPE_METHOD"

    # Build the insert-key statements in GDScript.
    key_stmts = []
    for (t, v) in keys:
        tlit = float(t)
        if tt == "value":
            vlit = _gd_value_literal(v)
            key_stmts.append(f'    anim.track_insert_key(ti, {tlit}, {vlit})')
        else:
            # method track value = { "method": <name>, "args": [] }
            mname = str(v)
            key_stmts.append(
                f'    anim.track_insert_key(ti, {tlit}, '
                f'{{"method": "{mname}", "args": []}})')
    key_block = "\n".join(key_stmts) if key_stmts else "    pass"

    src = f'''extends SceneTree
func _init():
    var lib_path = "{lib_res}"
    var lib: AnimationLibrary = ResourceLoader.load(
        lib_path, "", ResourceLoader.CACHE_MODE_IGNORE)
    if lib == null:
        push_error("LIB_LOAD_FAILED")
        quit(1)
        return
    if not lib.has_animation("{anim}"):
        push_error("ANIM_NOT_FOUND")
        quit(1)
        return
    var anim: Animation = lib.get_animation("{anim}")
    var ti = anim.add_track({track_type_const})
    anim.track_set_path(ti, "{path}")
{key_block}
    var rc = ResourceSaver.save(lib, lib_path)
    if rc != OK:
        push_error("SAVE_LIB_FAILED %d" % rc)
        quit(1)
        return
    # Re-wire the updated library into the player + re-save the scene.
    var ps = load("{scene_res}")
    if ps != null:
        var root = ps.instantiate()
        var ap = root.find_child("{player}", true, false)
        if ap != null and ap is AnimationPlayer:
            var fresh = ResourceLoader.load(
                lib_path, "", ResourceLoader.CACHE_MODE_IGNORE)
            if ap.has_animation_library("{lib_key}"):
                ap.remove_animation_library("{lib_key}")
            ap.add_animation_library("{lib_key}", fresh)
            _set_owner(root, root)
            var packed = PackedScene.new()
            if packed.pack(root) == OK:
                ResourceSaver.save(packed, "{scene_res}")
    print("ANIM_TRACK_OK ", anim.get_track_count())
    quit(0)

func _set_owner(node, owner_root):
    for c in node.get_children():
        if node != owner_root:
            node.owner = owner_root
        _set_owner(c, owner_root)
'''
    _run_script_checked(project_root, src, marker="ANIM_TRACK_OK")
    return {"status": "ok", "changed": True, "player": player, "anim": anim,
            "track_type": tt, "path": path, "keys": len(keys),
            "library": lib_abs}


def _gd_value_literal(v) -> str:
    """Best-effort: turn a Python/CLI value into a GDScript expression for a key.

    Numbers and bools pass through; a string that already looks like a Godot
    constructor (``Vector2(...)`` etc.) is used verbatim; otherwise quote it.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if re.fullmatch(r"[+-]?\d+", s):
        return s
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+([eE][+-]?\d+))", s):
        return s
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(.*\)$", s):
        return s  # Vector2(...), Color(...), etc.
    if s in ("true", "false", "null"):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def tileset_create(tres: str, texture: str, tile_size: str,
                   tiles: list[str] | None = None) -> dict:
    """Create a ``TileSet`` (.tres) with one ``TileSetAtlasSource`` and tiles.

    SCRIPT mechanism: builds the TileSet + TileSetAtlasSource via the class API,
    calls ``create_tile`` per atlas coordinate, and saves with ResourceSaver.

    ``tile_size`` is "w,h". ``tiles`` is a list of "x,y" atlas coords; when None
    all tiles that fit the texture are created.

    Requires the texture to be importable: this runs ``--import`` first so the
    PNG has a usable ``.import`` / UID.
    """
    project_root = _project_root_of(tres)
    tres_res = _to_res_path(project_root, tres)
    tex_res = _to_res_path(project_root, texture)

    ts_parts = [p.strip() for p in str(tile_size).split(",") if p.strip()]
    if len(ts_parts) != 2:
        raise RuntimeError(f"--tile-size must be 'w,h' (got {tile_size!r})")
    tw, th = int(ts_parts[0]), int(ts_parts[1])

    if tiles:
        coord_lits = []
        for t in tiles:
            xy = [p.strip() for p in str(t).split(",") if p.strip()]
            if len(xy) != 2:
                raise RuntimeError(f"tile coord must be 'x,y' (got {t!r})")
            coord_lits.append(f"Vector2i({int(xy[0])}, {int(xy[1])})")
        tiles_block = (
            "    var coords = [" + ", ".join(coord_lits) + "]\n"
            "    for c in coords:\n"
            "        src.create_tile(c)\n"
            "        made += 1"
        )
    else:
        tiles_block = (
            "    var gx = int(tex.get_width() / {tw})\n"
            "    var gy = int(tex.get_height() / {th})\n"
            "    for yy in range(gy):\n"
            "        for xx in range(gx):\n"
            "            src.create_tile(Vector2i(xx, yy))\n"
            "            made += 1"
        ).format(tw=tw, th=th)

    # Warm the import cache so the texture loads.
    godot_backend.import_project(project_root, timeout=180)

    src = f'''extends SceneTree
func _init():
    var tex = load("{tex_res}")
    if tex == null:
        push_error("TEX_LOAD_FAILED")
        quit(1)
        return
    var ts = TileSet.new()
    ts.tile_size = Vector2i({tw}, {th})
    var src = TileSetAtlasSource.new()
    src.texture = tex
    src.texture_region_size = Vector2i({tw}, {th})
    var made = 0
{tiles_block}
    ts.add_source(src, 0)
    var rc = ResourceSaver.save(ts, "{tres_res}")
    if rc != OK:
        push_error("SAVE_FAILED %d" % rc)
        quit(1)
        return
    print("TILESET_OK ", made)
    quit(0)
'''
    res = _run_script_checked(project_root, src, marker="TILESET_OK")
    m = re.search(r"TILESET_OK\s+(\d+)", res.get("stdout", ""))
    count = int(m.group(1)) if m else None
    return {"status": "ok", "changed": True, "tres": os.path.abspath(tres),
            "texture": texture, "tile_size": [tw, th], "tiles_created": count}


def tilemap_paint(scene: str, layer: str, cells: list[str]) -> dict:
    """Paint cells onto a ``TileMapLayer`` and re-save the scene.

    SCRIPT mechanism: loads the scene, finds the layer, calls ``set_cell`` per
    cell, re-packs and saves.

    ``cells`` entries look like "x,y=source:atlasx,atlasy" (e.g. "0,0=0:1,2").
    """
    if not os.path.isfile(scene):
        raise RuntimeError(f"Scene file not found: {scene}")
    project_root = _project_root_of(scene)
    scene_res = _to_res_path(project_root, scene)

    set_stmts = []
    for c in cells:
        coord, _, rhs = str(c).partition("=")
        if not rhs:
            raise RuntimeError(
                f"cell must be 'x,y=source:atlasx,atlasy' (got {c!r})")
        cx = [p.strip() for p in coord.split(",") if p.strip()]
        srcid, _, atlas = rhs.partition(":")
        ax = [p.strip() for p in atlas.split(",") if p.strip()]
        if len(cx) != 2 or len(ax) != 2 or srcid.strip() == "":
            raise RuntimeError(
                f"cell must be 'x,y=source:atlasx,atlasy' (got {c!r})")
        set_stmts.append(
            f'    tm.set_cell(Vector2i({int(cx[0])}, {int(cx[1])}), '
            f'{int(srcid)}, Vector2i({int(ax[0])}, {int(ax[1])}))')
    set_block = "\n".join(set_stmts) if set_stmts else "    pass"

    # Make sure tileset textures are imported.
    godot_backend.import_project(project_root, timeout=180)

    src = f'''extends SceneTree
func _init():
    var ps = load("{scene_res}")
    if ps == null:
        push_error("SCENE_LOAD_FAILED")
        quit(1)
        return
    var root = ps.instantiate()
    var tm = root.find_child("{layer}", true, false)
    if tm == null or not (tm is TileMapLayer):
        push_error("LAYER_NOT_FOUND")
        quit(1)
        return
{set_block}
    _set_owner(root, root)
    var packed = PackedScene.new()
    if packed.pack(root) != OK:
        push_error("PACK_FAILED")
        quit(1)
        return
    if ResourceSaver.save(packed, "{scene_res}") != OK:
        push_error("SCENE_SAVE_FAILED")
        quit(1)
        return
    print("TILEMAP_PAINT_OK ", tm.get_used_cells().size())
    quit(0)

func _set_owner(node, owner_root):
    for c in node.get_children():
        if node != owner_root:
            node.owner = owner_root
        _set_owner(c, owner_root)
'''
    res = _run_script_checked(project_root, src, marker="TILEMAP_PAINT_OK")
    m = re.search(r"TILEMAP_PAINT_OK\s+(\d+)", res.get("stdout", ""))
    used = int(m.group(1)) if m else None
    return {"status": "ok", "changed": True, "scene": os.path.abspath(scene),
            "layer": layer, "cells_painted": len(cells), "used_cells": used}
