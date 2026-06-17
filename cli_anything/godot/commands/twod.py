"""``2d`` command group — 2D-specific scene conveniences (SPEC-offline §C.4).

These bundle several low-level steps (add a node + its texture ext_resource, a
body + collision + shape, etc.) into single commands. FILE-mechanism commands
edit the `.tscn` directly; SCRIPT-mechanism commands (anim/tileset/tilemap-paint)
drive the engine to build complex typed resources safely.

The module exposes a single module-global ``twod_group`` (CLI name ``2d``) per the
Click export convention in CONTRACT.md; the orchestrator registers it.
"""

from __future__ import annotations

import os

import click

from cli_anything.godot.core.output import emit, handle_error
from cli_anything.godot.core import twod as twod_core


twod_group = click.Group("2d", help="2D conveniences: sprites, camera, bodies, "
                                    "collision, physics layers, animation, tilemap.")


def _scene_path(ctx, scene: str) -> str:
    """Resolve a scene argument to an absolute path under the project dir."""
    if os.path.isabs(scene):
        return scene
    project = ctx.obj.get("project") or os.getcwd()
    return os.path.abspath(os.path.join(project, scene))


# ── sprites / camera / bodies ───────────────────────────────────────────

@twod_group.command("add-sprite")
@click.argument("scene")
@click.option("--name", required=True, help="Node name for the new Sprite2D.")
@click.option("--parent", default=".", help="Parent path ('.' = root). Default '.'.")
@click.option("--texture", required=True, help="Texture path (res:// or filesystem).")
@click.option("--region", default=None, help="Region rect 'x,y,w,h' (sets region_enabled).")
@click.pass_context
@handle_error
def add_sprite_cmd(ctx, scene, name, parent, texture, region):
    """Add a Sprite2D with its texture set (Sprite2D + Texture2D ext_resource)."""
    data = twod_core.add_sprite(_scene_path(ctx, scene), name, parent,
                                texture=texture, region=region)
    emit(ctx, data)


@twod_group.command("add-camera")
@click.argument("scene")
@click.option("--name", default="Camera2D", help="Node name. Default 'Camera2D'.")
@click.option("--parent", default=".", help="Parent path ('.' = root).")
@click.option("--current", is_flag=True, help="Make this the active camera.")
@click.option("--zoom", default=None, help="Zoom 'x,y' -> Vector2(x, y).")
@click.pass_context
@handle_error
def add_camera_cmd(ctx, scene, name, parent, current, zoom):
    """Add a Camera2D."""
    data = twod_core.add_camera(_scene_path(ctx, scene), name, parent,
                                current=current, zoom=zoom)
    emit(ctx, data)


@twod_group.command("add-body")
@click.argument("scene")
@click.option("--name", required=True, help="Node name for the body.")
@click.option("--type", "body_type", required=True,
              type=click.Choice(["CharacterBody2D", "RigidBody2D",
                                 "StaticBody2D", "Area2D"]),
              help="Physics body type.")
@click.option("--parent", default=".", help="Parent path ('.' = root).")
@click.pass_context
@handle_error
def add_body_cmd(ctx, scene, name, body_type, parent):
    """Add a physics body (CharacterBody2D / RigidBody2D / StaticBody2D / Area2D)."""
    data = twod_core.add_body(_scene_path(ctx, scene), name, body_type, parent)
    emit(ctx, data)


@twod_group.command("add-collision")
@click.argument("scene")
@click.option("--name", default="CollisionShape2D", help="Node name.")
@click.option("--parent", required=True, help="Parent body path.")
@click.option("--shape", required=True,
              help="Shape spec: 'rectangle:size=Vector2(32,48)' | "
                   "'circle:radius=16' | 'capsule:radius=8;height=32'.")
@click.pass_context
@handle_error
def add_collision_cmd(ctx, scene, name, parent, shape):
    """Add a CollisionShape2D with a generated Shape2D sub_resource."""
    data = twod_core.add_collision(_scene_path(ctx, scene), name, parent,
                                   shape=shape)
    emit(ctx, data)


@twod_group.command("set-physics-layer")
@click.argument("scene")
@click.option("--path", required=True, help="Node path to set layers on.")
@click.option("--collision-layer", default=None,
              help="Layer bitmask (int, decimal, 0b..., or 0x...).")
@click.option("--collision-mask", default=None,
              help="Mask bitmask (int, decimal, 0b..., or 0x...).")
@click.pass_context
@handle_error
def set_physics_layer_cmd(ctx, scene, path, collision_layer, collision_mask):
    """Set collision_layer / collision_mask bitmask properties on a node."""
    data = twod_core.set_physics_layer(_scene_path(ctx, scene), path,
                                       collision_layer=collision_layer,
                                       collision_mask=collision_mask)
    emit(ctx, data)


@twod_group.command("add-animationplayer")
@click.argument("scene")
@click.option("--name", default="AnimationPlayer", help="Node name.")
@click.option("--parent", default=".", help="Parent path ('.' = root).")
@click.pass_context
@handle_error
def add_animationplayer_cmd(ctx, scene, name, parent):
    """Add an empty AnimationPlayer node."""
    data = twod_core.add_animationplayer(_scene_path(ctx, scene), name, parent)
    emit(ctx, data)


# ── animation (SCRIPT) ──────────────────────────────────────────────────

anim_group = click.Group("anim", help="Animation library / track authoring (engine-backed).")
twod_group.add_command(anim_group)


@anim_group.command("create")
@click.argument("scene")
@click.option("--player", required=True, help="AnimationPlayer node name.")
@click.option("--name", required=True, help="Animation name to create.")
@click.option("--length", required=True, type=float, help="Animation length (seconds).")
@click.option("--loop", is_flag=True, help="Loop the animation (LOOP_LINEAR).")
@click.option("--library", default=None,
              help="Library .tres path (default: <scene>_<player>_lib.tres).")
@click.pass_context
@handle_error
def anim_create_cmd(ctx, scene, player, name, length, loop, library):
    """Create an Animation in an AnimationLibrary .tres and wire it to the player."""
    data = twod_core.anim_create(_scene_path(ctx, scene), player, name, length,
                                 loop=loop, library=library)
    emit(ctx, data)


@anim_group.command("add-track")
@click.argument("scene")
@click.option("--player", required=True, help="AnimationPlayer node name.")
@click.option("--anim", required=True, help="Target animation name.")
@click.option("--track-type", default="value",
              type=click.Choice(["value", "method"]), help="Track type.")
@click.option("--path", required=True, help="Track path, e.g. 'Sprite2D:frame'.")
@click.option("--key", "keys", multiple=True,
              help="Keyframe 'time,value' (repeatable). e.g. --key 0,0 --key 0.3,1")
@click.option("--library", default=None, help="Library .tres path (default matches create).")
@click.pass_context
@handle_error
def anim_add_track_cmd(ctx, scene, player, anim, track_type, path, keys, library):
    """Add a value/method track with keyframes to an animation."""
    parsed = []
    for k in keys:
        t, _, v = str(k).partition(",")
        if v == "":
            raise click.BadParameter(f"--key must be 'time,value' (got {k!r})")
        parsed.append((float(t.strip()), v.strip()))
    data = twod_core.anim_add_track(_scene_path(ctx, scene), player, anim,
                                    track_type, path, parsed, library=library)
    emit(ctx, data)


# ── tilemap / tileset ───────────────────────────────────────────────────

tilemap_group = click.Group("tilemap", help="TileMapLayer add / paint (4.3).")
tileset_group = click.Group("tileset", help="TileSet authoring (engine-backed).")
twod_group.add_command(tilemap_group)
twod_group.add_command(tileset_group)


@tilemap_group.command("add")
@click.argument("scene")
@click.option("--name", default="TileMapLayer", help="Node name.")
@click.option("--parent", default=".", help="Parent path ('.' = root).")
@click.option("--tileset", required=True, help="TileSet .tres path.")
@click.pass_context
@handle_error
def tilemap_add_cmd(ctx, scene, name, parent, tileset):
    """Add a TileMapLayer node with tile_set = ExtResource(tileset)."""
    data = twod_core.tilemap_add(_scene_path(ctx, scene), name, parent,
                                 tileset=tileset)
    emit(ctx, data)


@tilemap_group.command("paint")
@click.argument("scene")
@click.option("--layer", required=True, help="TileMapLayer node name.")
@click.option("--cells", required=True,
              help="Space-separated cells 'x,y=source:atlasx,atlasy'.")
@click.pass_context
@handle_error
def tilemap_paint_cmd(ctx, scene, layer, cells):
    """Paint cells onto a TileMapLayer (engine-backed; re-packs the scene)."""
    cell_list = [c for c in str(cells).split() if c.strip()]
    if not cell_list:
        raise click.BadParameter("--cells must contain at least one cell")
    data = twod_core.tilemap_paint(_scene_path(ctx, scene), layer, cell_list)
    emit(ctx, data)


@tileset_group.command("create")
@click.argument("tres")
@click.option("--texture", required=True, help="Atlas texture path (PNG).")
@click.option("--tile-size", required=True, help="Tile size 'w,h'.")
@click.option("--tiles", default=None,
              help="Space-separated atlas coords 'x,y' to create (default: all).")
@click.pass_context
@handle_error
def tileset_create_cmd(ctx, tres, texture, tile_size, tiles):
    """Create a TileSet .tres with a TileSetAtlasSource + tiles (engine-backed)."""
    tres_path = _scene_path(ctx, tres)
    tile_list = [t for t in str(tiles).split() if t.strip()] if tiles else None
    data = twod_core.tileset_create(tres_path, texture, tile_size, tile_list)
    emit(ctx, data)
