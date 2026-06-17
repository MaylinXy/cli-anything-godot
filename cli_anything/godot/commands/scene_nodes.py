"""Click command groups for scene/node CRUD, signals, and instancing.

Defines three module-global ``click.Group`` objects the orchestrator registers:

    scene_group   -> `scene`  (create/read/tree/instance/make-editable/
                               override-child/repack)
    node_group    -> `node`   (add/remove/move/reparent/rename/duplicate/
                               get-prop/set-prop/attach-script/
                               add-to-group/remove-from-group)
    signal_group  -> `signal` (connect/disconnect/list)

All command bodies are thin wrappers over ``core.nodes`` / ``core.signals``;
output goes through ``core.output.emit`` and errors through ``handle_error``.
Project dir is resolved as ``ctx.obj.get("project") or os.getcwd()``.
"""

from __future__ import annotations

import os

import click

from cli_anything.godot.core.output import emit, handle_error
from cli_anything.godot.core import nodes as N
from cli_anything.godot.core import signals as S


def _project(ctx) -> str:
    return os.path.abspath(ctx.obj.get("project") or os.getcwd())


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# ══════════════════════════════════════════════════════════════════════
# scene group
# ══════════════════════════════════════════════════════════════════════

scene_group = click.Group(
    "scene", help="Scene CRUD: create/read/tree/instance/editable/repack.")


@scene_group.command("create")
@click.argument("scene")
@click.option("--root-type", default="Node2D", help="Root node type.")
@click.option("--root-name", default=None, help="Root node name (defaults to file stem).")
@click.pass_context
@handle_error
def scene_create(ctx, scene, root_type, root_name):
    """Create a new .tscn scene at SCENE (project-relative)."""
    emit(ctx, N.create_scene(_project(ctx), scene, root_type, root_name))


@scene_group.command("read")
@click.argument("scene")
@click.pass_context
@handle_error
def scene_read(ctx, scene):
    """Read a scene: node tree + ext/sub resources + connections (JSON-able)."""
    emit(ctx, N.read_scene(_project(ctx), scene))


@scene_group.command("tree")
@click.argument("scene")
@click.pass_context
@handle_error
def scene_tree_cmd(ctx, scene):
    """Print a pretty indented node tree of SCENE."""
    res = N.scene_tree(_project(ctx), scene)
    if not ctx.obj.get("json"):
        click.echo(res["tree"])
    else:
        emit(ctx, res)


@scene_group.command("instance")
@click.argument("scene")
@click.option("--child-scene", required=True, help="res:// path of the PackedScene to instance.")
@click.option("--name", required=True, help="Name for the instanced node.")
@click.option("--parent", default=".", help="Parent node path (default: root).")
@click.option("--prop", "props", multiple=True, help="Override prop k=literal (repeatable).")
@click.option("--index", type=int, default=None, help="Sibling index.")
@click.pass_context
@handle_error
def scene_instance(ctx, scene, child_scene, name, parent, props, index):
    """Instance a child scene (PackedScene + instance=ExtResource)."""
    emit(ctx, N.instance_scene(_project(ctx), scene, child_scene, name, parent,
                               props=list(props), index=index))


@scene_group.command("make-editable")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the instanced node to make editable.")
@click.pass_context
@handle_error
def scene_make_editable(ctx, scene, path):
    """Write [editable path="X"] for an instanced node."""
    emit(ctx, N.make_editable(_project(ctx), scene, path))


@scene_group.command("override-child")
@click.argument("scene")
@click.option("--instance", required=True, help="Path of the instanced node.")
@click.option("--child", required=True, help="Child node path inside the instance.")
@click.option("--prop", "props", multiple=True, help="Override prop k=literal (repeatable).")
@click.pass_context
@handle_error
def scene_override_child(ctx, scene, instance, child, props):
    """Override a property on a node inside an instanced scene."""
    emit(ctx, N.override_child(_project(ctx), scene, instance, child, list(props)))


@scene_group.command("repack")
@click.argument("scene")
@click.option("--timeout", type=int, default=120, help="Engine timeout (seconds).")
@click.pass_context
@handle_error
def scene_repack(ctx, scene, timeout):
    """Normalize/validate SCENE via the engine (load+pack+save)."""
    emit(ctx, N.repack_scene(_project(ctx), scene, timeout=timeout))


# ══════════════════════════════════════════════════════════════════════
# node group
# ══════════════════════════════════════════════════════════════════════

node_group = click.Group(
    "node", help="Node CRUD: add/remove/move/reparent/rename/duplicate/props/...")


@node_group.command("add")
@click.argument("scene")
@click.option("--name", required=True, help="Node name.")
@click.option("--type", "type_", required=True, help="Node type (Sprite2D, Button, ...).")
@click.option("--parent", default=".", help="Parent node path (default: root).")
@click.option("--index", type=int, default=None, help="Sibling index.")
@click.option("--groups", default=None, help="Comma-separated group names.")
@click.pass_context
@handle_error
def node_add(ctx, scene, name, type_, parent, index, groups):
    """Add a child node."""
    emit(ctx, N.add_node(_project(ctx), scene, name, type_, parent,
                         index=index, groups=_csv(groups)))


@node_group.command("remove")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the node to remove.")
@click.pass_context
@handle_error
def node_remove(ctx, scene, path):
    """Remove a node + its descendants + their connections."""
    emit(ctx, N.remove_node(_project(ctx), scene, path))


@node_group.command("move")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the node to move.")
@click.option("--index", type=int, required=True, help="New sibling index.")
@click.pass_context
@handle_error
def node_move(ctx, scene, path, index):
    """Reorder a node among its siblings."""
    emit(ctx, N.move_node(_project(ctx), scene, path, index))


@node_group.command("reparent")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the node to reparent.")
@click.option("--to-parent", required=True, help="New parent node path.")
@click.option("--index", type=int, default=None, help="Sibling index under the new parent.")
@click.pass_context
@handle_error
def node_reparent(ctx, scene, path, to_parent, index):
    """Move a node (and subtree) under a new parent; fix all paths."""
    emit(ctx, N.reparent_node(_project(ctx), scene, path, to_parent, index=index))


@node_group.command("rename")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the node to rename.")
@click.option("--to", required=True, help="New node name.")
@click.pass_context
@handle_error
def node_rename(ctx, scene, path, to):
    """Rename a node and update all references (parents/connections/NodePaths)."""
    emit(ctx, N.rename_node(_project(ctx), scene, path, to))


@node_group.command("duplicate")
@click.argument("scene")
@click.option("--path", required=True, help="Path of the node to duplicate.")
@click.option("--name", default=None, help="Name for the clone (auto if omitted).")
@click.pass_context
@handle_error
def node_duplicate(ctx, scene, path, name):
    """Clone a node + its subtree under the same parent."""
    emit(ctx, N.duplicate_node(_project(ctx), scene, path, name))


@node_group.command("get-prop")
@click.argument("scene")
@click.option("--path", required=True, help="Node path.")
@click.option("--prop", required=True, help="Property name.")
@click.pass_context
@handle_error
def node_get_prop(ctx, scene, path, prop):
    """Read a node property (raw literal + parsed value)."""
    emit(ctx, N.get_prop(_project(ctx), scene, path, prop))


@node_group.command("set-prop")
@click.argument("scene")
@click.option("--path", required=True, help="Node path.")
@click.option("--prop", required=True, help="Property name.")
@click.option("--value", default=None, help="Raw Godot literal value.")
@click.option("--type", "kind", default=None,
              help="Convert --value via this kind (int,float,bool,string,vector2,color,nodepath,raw...).")
@click.option("--raw", is_flag=True, help="Write --value verbatim.")
@click.option("--ext-resource", "ext_resource", default=None,
              help="res://path[:Type] — adds/reuses an ext_resource, writes ExtResource(...).")
@click.option("--sub-resource", "sub_resource", default=None,
              help="Type:k=v,... — creates a sub_resource, writes SubResource(...).")
@click.pass_context
@handle_error
def node_set_prop(ctx, scene, path, prop, value, kind, raw, ext_resource, sub_resource):
    """Set a node property (value / ext-resource / sub-resource)."""
    emit(ctx, N.set_prop(_project(ctx), scene, path, prop, value=value, kind=kind,
                         ext_resource=ext_resource, sub_resource=sub_resource, raw=raw))


@node_group.command("attach-script")
@click.argument("scene")
@click.option("--path", required=True, help="Node path.")
@click.option("--script", required=True, help="res:// path of the .gd script.")
@click.pass_context
@handle_error
def node_attach_script(ctx, scene, path, script):
    """Attach a GDScript to a node (ext_resource + script =)."""
    emit(ctx, N.attach_script(_project(ctx), scene, path, script))


@node_group.command("add-to-group")
@click.argument("scene")
@click.option("--path", required=True, help="Node path.")
@click.option("--group", required=True, help="Group name.")
@click.pass_context
@handle_error
def node_add_to_group(ctx, scene, path, group):
    """Add a node to a scene group."""
    emit(ctx, N.add_to_group(_project(ctx), scene, path, group))


@node_group.command("remove-from-group")
@click.argument("scene")
@click.option("--path", required=True, help="Node path.")
@click.option("--group", required=True, help="Group name.")
@click.pass_context
@handle_error
def node_remove_from_group(ctx, scene, path, group):
    """Remove a node from a scene group."""
    emit(ctx, N.remove_from_group(_project(ctx), scene, path, group))


# ══════════════════════════════════════════════════════════════════════
# signal group
# ══════════════════════════════════════════════════════════════════════

signal_group = click.Group(
    "signal", help="Signal connections: connect/disconnect/list.")


@signal_group.command("connect")
@click.argument("scene")
@click.option("--signal", "signal_", required=True, help="Signal name.")
@click.option("--from", "from_", required=True, help="Emitter node path.")
@click.option("--to", required=True, help="Receiver node path.")
@click.option("--method", required=True, help="Callback method name.")
@click.option("--flags", type=int, default=None, help="Connect flags (only stored when != 2).")
@click.option("--unbinds", type=int, default=None, help="Unbinds (only stored when > 0).")
@click.option("--binds", default=None, help="Bound args array literal, e.g. [42].")
@click.pass_context
@handle_error
def signal_connect(ctx, scene, signal_, from_, to, method, flags, unbinds, binds):
    """Connect a signal (write a [connection] line)."""
    emit(ctx, S.connect(_project(ctx), scene, signal_, from_, to, method,
                        flags=flags, unbinds=unbinds, binds=binds))


@signal_group.command("disconnect")
@click.argument("scene")
@click.option("--signal", "signal_", default=None, help="Signal name.")
@click.option("--from", "from_", default=None, help="Emitter node path.")
@click.option("--to", default=None, help="Receiver node path.")
@click.option("--method", default=None, help="Callback method name.")
@click.option("--index", type=int, default=None, help="Remove by connection index instead.")
@click.pass_context
@handle_error
def signal_disconnect(ctx, scene, signal_, from_, to, method, index):
    """Disconnect a signal by fields or by --index."""
    emit(ctx, S.disconnect(_project(ctx), scene, signal=signal_, from_=from_,
                           to=to, method=method, index=index))


@signal_group.command("list")
@click.argument("scene")
@click.option("--from", "from_", default=None, help="Filter by emitter node path.")
@click.pass_context
@handle_error
def signal_list(ctx, scene, from_):
    """List outgoing connections in SCENE."""
    emit(ctx, S.list_signals(_project(ctx), scene, from_))
