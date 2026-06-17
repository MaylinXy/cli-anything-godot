"""``live`` command group — real-time control of a running Godot editor.

Each subcommand opens a :class:`LiveBridge`, calls one bridge op, and emits the
result. Connection config comes from ``--host/--port/--token`` flags, or the
``GODOT_LIVE_HOST/PORT/TOKEN`` env vars, or the defaults ``127.0.0.1:8787``.

``live install`` is the exception: it copies the bundled ``addons/live_bridge``
addon into a target project and enables it in ``project.godot`` (no editor
connection needed). See SPEC-live-bridge.md §A.3 / §E.1.
"""

from __future__ import annotations

import os
import shutil

import click

# Shared output helpers (created by FOUND in core/output.py).
from cli_anything.godot.core.output import emit, handle_error
from cli_anything.godot.utils.live_client import LiveBridge, LiveBridgeError


live_group = click.Group("live", help="Real-time control of a running Godot editor (live bridge).")


# ── connection helpers ─────────────────────────────────────────────────

def _conn_opts(func):
    """Attach shared --host/--port/--token options to a command."""
    func = click.option("--host", default=None, help="Bridge host (env GODOT_LIVE_HOST, default 127.0.0.1).")(func)
    func = click.option("--port", default=None, type=int, help="Bridge port (env GODOT_LIVE_PORT, default 8787).")(func)
    func = click.option("--token", default=None, help="Auth token (env GODOT_LIVE_TOKEN, default none).")(func)
    return func


def _bridge(host, port, token) -> LiveBridge:
    """Build a connected LiveBridge from flags / env / defaults."""
    host = host or os.environ.get("GODOT_LIVE_HOST", "127.0.0.1")
    port = int(port or os.environ.get("GODOT_LIVE_PORT", "8787"))
    token = token or os.environ.get("GODOT_LIVE_TOKEN") or None
    return LiveBridge(host=host, port=port, token=token).connect()


def _run(ctx, host, port, token, fn):
    """Connect, run ``fn(bridge)``, emit the result, mapping bridge errors to
    RuntimeError so the handle_error decorator formats them uniformly."""
    bridge = None
    try:
        bridge = _bridge(host, port, token)
        result = fn(bridge)
    except LiveBridgeError as e:
        raise RuntimeError(str(e)) from e
    finally:
        if bridge is not None:
            bridge.close()
    data = {"status": "ok"}
    if isinstance(result, dict):
        data.update(result)
    else:
        data["result"] = result
    emit(ctx, data)


def _project(ctx) -> str:
    return os.path.abspath(ctx.obj.get("project") or os.getcwd())


# ── install (no editor connection) ──────────────────────────────────────

@live_group.command("install")
@click.option("--force", is_flag=True, help="Overwrite an existing addon copy.")
@click.pass_context
@handle_error
def live_install(ctx, force):
    """Copy the live_bridge addon into the project and enable it in project.godot.

    Run this while the editor is CLOSED, then launch the editor; the plugin loads
    on startup and the bridge begins listening on 127.0.0.1:8787.
    """
    project = _project(ctx)
    project_godot = os.path.join(project, "project.godot")
    if not os.path.isfile(project_godot):
        raise RuntimeError(f"Not a Godot project (no project.godot): {project}")

    # Source addon ships as package data next to cli_anything/godot/addons/.
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons", "live_bridge")
    if not os.path.isdir(src):
        raise RuntimeError(f"Bundled addon not found at {src}")

    dst = os.path.join(project, "addons", "live_bridge")
    if os.path.isdir(dst) and not force:
        raise RuntimeError(f"Addon already installed at {dst}. Use --force to overwrite.")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)

    enabled = _enable_plugin(project_godot)

    emit(ctx, {
        "status": "ok",
        "changed": True,
        "installed_to": dst,
        "plugin_enabled": enabled,
        "next": "Launch the editor on this project; the bridge listens on 127.0.0.1:8787.",
    })


def _enable_plugin(project_godot: str) -> bool:
    """Idempotently add the plugin path to [editor_plugins] enabled in project.godot.

    Done with a minimal text edit (no dependency on FOUND's configfile) so install
    works standalone. Returns True if a change was written.
    """
    # Godot 4.x references the plugin.cfg (not plugin.gd) in [editor_plugins].
    plugin_path = "res://addons/live_bridge/plugin.cfg"
    with open(project_godot, "r", encoding="utf-8") as f:
        text = f.read()

    if plugin_path in text:
        return False  # already enabled (idempotent)

    lines = text.splitlines()
    out: list[str] = []
    in_editor_plugins = False
    handled = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # Leaving a section: if it was [editor_plugins] and we never saw an
            # `enabled=` key, inject one before the next section.
            if in_editor_plugins and not handled:
                out.append(f'enabled=PackedStringArray("{plugin_path}")')
                handled = True
            in_editor_plugins = stripped == "[editor_plugins]"
            out.append(line)
            continue

        if in_editor_plugins and stripped.startswith("enabled=PackedStringArray("):
            # Append our path to the existing PackedStringArray(...).
            inner = stripped[len("enabled=PackedStringArray("):].rstrip(")")
            new_inner = (inner + ", " if inner.strip() else "") + f'"{plugin_path}"'
            out.append(f"enabled=PackedStringArray({new_inner})")
            handled = True
            continue

        out.append(line)

    if in_editor_plugins and not handled:
        out.append(f'enabled=PackedStringArray("{plugin_path}")')
        handled = True

    if not handled:
        # No [editor_plugins] section existed; append one.
        if out and out[-1].strip() != "":
            out.append("")
        out.append("[editor_plugins]")
        out.append("")
        out.append(f'enabled=PackedStringArray("{plugin_path}")')

    with open(project_godot, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return True


# ── status / probe ──────────────────────────────────────────────────────

@live_group.command("status")
@_conn_opts
@click.pass_context
@handle_error
def live_status(ctx, host, port, token):
    """Probe the bridge and show editor/session info (editor.info)."""
    _run(ctx, host, port, token, lambda b: b.info())


# ── introspection ───────────────────────────────────────────────────────

@live_group.command("tree")
@click.option("--from", "frm", default=".", help="Subtree root NodePath.")
@click.option("--depth", default=-1, type=int, help="Max depth (-1 = unlimited).")
@_conn_opts
@click.pass_context
@handle_error
def live_tree(ctx, frm, depth, host, port, token):
    """Dump the live scene tree (node.get_tree)."""
    _run(ctx, host, port, token, lambda b: b.get_tree(frm, depth))


# ── node CRUD ───────────────────────────────────────────────────────────

@live_group.command("add")
@click.argument("type")
@click.option("--parent", default=".", help="Parent NodePath.")
@click.option("--name", default=None, help="New node name.")
@_conn_opts
@click.pass_context
@handle_error
def live_add(ctx, type, parent, name, host, port, token):
    """Add a node of TYPE under --parent (node.add)."""
    _run(ctx, host, port, token, lambda b: b.add(parent, type, name))


@live_group.command("delete")
@click.argument("path")
@_conn_opts
@click.pass_context
@handle_error
def live_delete(ctx, path, host, port, token):
    """Delete the node at PATH (node.delete)."""
    _run(ctx, host, port, token, lambda b: b.delete(path))


# ── properties ──────────────────────────────────────────────────────────

@live_group.command("set")
@click.argument("path")
@click.argument("prop")
@click.option("--value", default=None, help="Raw JSON value (e.g. '7', '\"hi\"', '{...}').")
@click.option("--vec2", nargs=2, type=float, default=None, help="Set a Vector2 value: --vec2 X Y.")
@click.option("--vec3", nargs=3, type=float, default=None, help="Set a Vector3 value: --vec3 X Y Z.")
@click.option("--color", default=None, help="Set a Color from html, e.g. --color '#ff8800'.")
@click.option("--res", "res_path", default=None, help="Set a Resource by path, e.g. --res res://hero.png.")
@_conn_opts
@click.pass_context
@handle_error
def live_set(ctx, path, prop, value, vec2, vec3, color, res_path, host, port, token):
    """Set PROP on the node at PATH (node.set_prop).

    Exactly one of --value/--vec2/--vec3/--color/--res selects the value form.
    """
    import json as _json

    chosen = [x for x in (value, vec2 or None, vec3 or None, color, res_path) if x is not None]
    if len(chosen) != 1:
        raise RuntimeError("Provide exactly one of --value/--vec2/--vec3/--color/--res.")

    if vec2:
        v = LiveBridge.vec2(vec2[0], vec2[1])
    elif vec3:
        v = LiveBridge.vec3(vec3[0], vec3[1], vec3[2])
    elif color:
        v = LiveBridge.color_html(color)
    elif res_path:
        v = LiveBridge.res(res_path)
    else:
        try:
            v = _json.loads(value)
        except _json.JSONDecodeError:
            v = value  # treat as a bare string
    _run(ctx, host, port, token, lambda b: b.set_prop(path, prop, v))


@live_group.command("get")
@click.argument("path")
@click.argument("prop")
@_conn_opts
@click.pass_context
@handle_error
def live_get(ctx, path, prop, host, port, token):
    """Read PROP from the node at PATH (node.get_prop)."""
    _run(ctx, host, port, token, lambda b: b.get_prop(path, prop))


# ── signals ─────────────────────────────────────────────────────────────

@live_group.command("connect")
@click.argument("from_path")
@click.argument("signal")
@click.argument("to_path")
@click.argument("method")
@_conn_opts
@click.pass_context
@handle_error
def live_connect(ctx, from_path, signal, to_path, method, host, port, token):
    """Connect FROM_PATH.SIGNAL to TO_PATH.METHOD (signal.connect, persisted)."""
    _run(ctx, host, port, token, lambda b: b.connect_signal(from_path, signal, to_path, method))


# ── scenes ──────────────────────────────────────────────────────────────

@live_group.command("instance")
@click.argument("scene")
@click.option("--parent", default=".", help="Parent NodePath.")
@click.option("--name", default=None, help="Instance node name.")
@_conn_opts
@click.pass_context
@handle_error
def live_instance(ctx, scene, parent, name, host, port, token):
    """Instance a PackedScene (res://...) under --parent (scene.instance)."""
    _run(ctx, host, port, token, lambda b: b.instance(parent, scene, name))


@live_group.command("save")
@click.argument("path", required=False)
@_conn_opts
@click.pass_context
@handle_error
def live_save(ctx, path, host, port, token):
    """Save the current scene, or Save As if PATH given (scene.save)."""
    _run(ctx, host, port, token, lambda b: b.save_scene(path))


# ── selection ───────────────────────────────────────────────────────────

@live_group.command("select")
@click.argument("paths", nargs=-1, required=True)
@_conn_opts
@click.pass_context
@handle_error
def live_select(ctx, paths, host, port, token):
    """Set the editor selection to PATHS (selection.set)."""
    _run(ctx, host, port, token, lambda b: b.selection_set(list(paths)))


# ── play ────────────────────────────────────────────────────────────────

@live_group.command("play")
@click.argument("scene", required=False)
@_conn_opts
@click.pass_context
@handle_error
def live_play(ctx, scene, host, port, token):
    """Play the current scene, or SCENE if given (play.run)."""
    _run(ctx, host, port, token, lambda b: b.play(scene))


@live_group.command("stop")
@_conn_opts
@click.pass_context
@handle_error
def live_stop(ctx, host, port, token):
    """Stop the running game (play.stop)."""
    _run(ctx, host, port, token, lambda b: b.stop())


# ── undo / redo ─────────────────────────────────────────────────────────

@live_group.command("undo")
@click.argument("count", default=1, type=int)
@_conn_opts
@click.pass_context
@handle_error
def live_undo(ctx, count, host, port, token):
    """Undo the last COUNT editor actions (undo)."""
    _run(ctx, host, port, token, lambda b: b.undo(count))


@live_group.command("redo")
@click.argument("count", default=1, type=int)
@_conn_opts
@click.pass_context
@handle_error
def live_redo(ctx, count, host, port, token):
    """Redo the last COUNT editor actions (redo)."""
    _run(ctx, host, port, token, lambda b: b.redo(count))
