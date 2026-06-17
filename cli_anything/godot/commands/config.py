"""Config command groups: project settings / autoload / input map / groups / layers.

Defines five module-global :class:`click.Group` objects for the orchestrator to
register (see CONTRACT "Click group export convention"):

  - ``settings_group``  (Click name "settings"): get / set / unset / list
  - ``autoload_group``  (Click name "autoload"): add / remove / enable / disable / list
  - ``input_group``     (Click name "input"): add / add-event / remove / list
  - ``pgroup_group``    (Click name "group"): add / remove / list  ([global_group], 4.3+)
  - ``layer_group``     (Click name "layer"): name  ([layer_names])

All commands inherit ``--json`` and ``--project/-p`` from the root CLI context and
use the shared ``emit`` / ``handle_error`` from core/output. See SPEC-offline §C.1.
"""

from __future__ import annotations

import os

import click

from cli_anything.godot.core import settings as _settings
from cli_anything.godot.core.output import emit, handle_error


def _project(ctx) -> str:
    """Resolve the project directory (matches the baseline convention)."""
    return os.path.abspath(ctx.obj.get("project") or os.getcwd())


# ─────────────────────────────────────────────────────────────────────────
# settings
# ─────────────────────────────────────────────────────────────────────────

settings_group = click.Group("settings", help="Project settings: get/set/unset/list (project.godot).")


@settings_group.command("get")
@click.argument("key")
@click.pass_context
@handle_error
def settings_get_cmd(ctx, key):
    """Read a setting, e.g. `settings get application/config/name`."""
    emit(ctx, _settings.settings_get(_project(ctx), key))


@settings_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option(
    "--type", "type_",
    type=click.Choice(["string", "int", "float", "bool", "color", "vector2", "raw"]),
    default="string", show_default=True,
    help="How to interpret VALUE. 'raw' writes it verbatim.",
)
@click.pass_context
@handle_error
def settings_set_cmd(ctx, key, value, type_):
    """Set a setting, e.g. `settings set display/window/size/viewport_width 1280 --type int`."""
    emit(ctx, _settings.settings_set(_project(ctx), key, value, type=type_))


@settings_group.command("unset")
@click.argument("key")
@click.pass_context
@handle_error
def settings_unset_cmd(ctx, key):
    """Delete a setting line (revert to engine default)."""
    emit(ctx, _settings.settings_unset(_project(ctx), key))


@settings_group.command("list")
@click.option("--section", default=None, help="Limit to one section, e.g. --section application.")
@click.pass_context
@handle_error
def settings_list_cmd(ctx, section):
    """List settings (optionally for one section)."""
    emit(ctx, _settings.settings_list(_project(ctx), section=section))


# ─────────────────────────────────────────────────────────────────────────
# autoload
# ─────────────────────────────────────────────────────────────────────────

autoload_group = click.Group("autoload", help="Autoload singletons: add/remove/enable/disable/list.")


@autoload_group.command("add")
@click.argument("name")
@click.argument("path")
@click.option("--disabled", is_flag=True, help="Register but leave the singleton disabled (no leading *).")
@click.pass_context
@handle_error
def autoload_add_cmd(ctx, name, path, disabled):
    """Register an autoload, e.g. `autoload add GameState res://globals/gs.gd`."""
    emit(ctx, _settings.autoload_add(_project(ctx), name, path, disabled=disabled))


@autoload_group.command("remove")
@click.argument("name")
@click.pass_context
@handle_error
def autoload_remove_cmd(ctx, name):
    """Remove an autoload."""
    emit(ctx, _settings.autoload_remove(_project(ctx), name))


@autoload_group.command("enable")
@click.argument("name")
@click.pass_context
@handle_error
def autoload_enable_cmd(ctx, name):
    """Enable an autoload (add the leading *)."""
    emit(ctx, _settings.autoload_enable(_project(ctx), name))


@autoload_group.command("disable")
@click.argument("name")
@click.pass_context
@handle_error
def autoload_disable_cmd(ctx, name):
    """Disable an autoload (remove the leading *)."""
    emit(ctx, _settings.autoload_disable(_project(ctx), name))


@autoload_group.command("list")
@click.pass_context
@handle_error
def autoload_list_cmd(ctx):
    """List autoloads with enabled state."""
    emit(ctx, _settings.autoload_list(_project(ctx)))


# ─────────────────────────────────────────────────────────────────────────
# input map
# ─────────────────────────────────────────────────────────────────────────

input_group = click.Group("input", help="Input map: add/add-event/remove/list (project.godot [input]).")


def _event_opts(func):
    """Attach the mutually-exclusive event-source flags to a command."""
    func = click.option("--key", default=None, help="Key by name (current layout), e.g. SPACE / A / F1.")(func)
    func = click.option("--physical-key", "physical_key", default=None,
                        help="Physical key by name (layout-independent), e.g. SPACE / W.")(func)
    func = click.option("--mouse", default=None, help="Mouse button: left/right/middle/wheel_up/wheel_down.")(func)
    func = click.option("--joy-button", "joy_button", default=None, help="Joypad button: a/b/x/y/lb/rb/...")(func)
    func = click.option("--joy-axis", "joy_axis", default=None, help="Joypad axis with value, e.g. lx:-1 (default value 1.0).")(func)
    return func


@input_group.command("add")
@click.argument("action")
@_event_opts
@click.option("--deadzone", default=0.5, type=float, show_default=True, help="Action deadzone.")
@click.pass_context
@handle_error
def input_add_cmd(ctx, action, key, physical_key, mouse, joy_button, joy_axis, deadzone):
    """Create an action with one event, e.g. `input add jump --physical-key SPACE`."""
    spec = _settings.parse_event_spec(
        key=key, physical_key=physical_key, mouse=mouse,
        joy_button=joy_button, joy_axis=joy_axis,
    )
    emit(ctx, _settings.input_add(_project(ctx), action, spec, deadzone=deadzone))


@input_group.command("add-event")
@click.argument("action")
@_event_opts
@click.pass_context
@handle_error
def input_add_event_cmd(ctx, action, key, physical_key, mouse, joy_button, joy_axis):
    """Append another event to an existing action, e.g. `input add-event jump --joy-button a`."""
    spec = _settings.parse_event_spec(
        key=key, physical_key=physical_key, mouse=mouse,
        joy_button=joy_button, joy_axis=joy_axis,
    )
    emit(ctx, _settings.input_add_event(_project(ctx), action, spec))


@input_group.command("remove")
@click.argument("action")
@click.option("--event-index", "event_index", default=None, type=int,
              help="Remove only the event at this index (default: remove the whole action).")
@click.pass_context
@handle_error
def input_remove_cmd(ctx, action, event_index):
    """Remove an action, or one event with --event-index N."""
    emit(ctx, _settings.input_remove(_project(ctx), action, event_index=event_index))


@input_group.command("list")
@click.argument("action", required=False)
@click.pass_context
@handle_error
def input_list_cmd(ctx, action):
    """List input actions, or one action's events if ACTION is given."""
    emit(ctx, _settings.input_list(_project(ctx), action=action))


# ─────────────────────────────────────────────────────────────────────────
# project groups ([global_group], 4.3+) — Click name "group"
# ─────────────────────────────────────────────────────────────────────────

pgroup_group = click.Group("group", help="Project-wide groups ([global_group], 4.3+): add/remove/list.")


@pgroup_group.command("add")
@click.argument("name")
@click.option("--description", default="", help="Group description.")
@click.pass_context
@handle_error
def group_add_cmd(ctx, name, description):
    """Add a project group, e.g. `group add enemies --description "Hostiles"`."""
    emit(ctx, _settings.group_add(_project(ctx), name, description=description))


@pgroup_group.command("remove")
@click.argument("name")
@click.pass_context
@handle_error
def group_remove_cmd(ctx, name):
    """Remove a project group."""
    emit(ctx, _settings.group_remove(_project(ctx), name))


@pgroup_group.command("list")
@click.pass_context
@handle_error
def group_list_cmd(ctx):
    """List project groups."""
    emit(ctx, _settings.group_list(_project(ctx)))


# ─────────────────────────────────────────────────────────────────────────
# layer names ([layer_names]) — Click name "layer", subcommand "name"
# ─────────────────────────────────────────────────────────────────────────

layer_group = click.Group("layer", help="Collision/render/navigation layer names ([layer_names]).")


@layer_group.command("name")
@click.argument("name")
@click.option(
    "--space",
    type=click.Choice(sorted(_settings._LAYER_SPACES)),
    required=True,
    help="Layer space (2d_physics, 3d_render, ...).",
)
@click.option("--layer", "layer_num", type=int, required=True, help="Layer number (1..32).")
@click.pass_context
@handle_error
def layer_name_cmd(ctx, name, space, layer_num):
    """Name a layer, e.g. `layer name world --space 2d_physics --layer 1`."""
    emit(ctx, _settings.layer_name(_project(ctx), space, layer_num, name))
