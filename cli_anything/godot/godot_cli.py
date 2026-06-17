"""cli-anything-godot — Agent-native CLI for the Godot game engine (hybrid).

Two layers:
  * OFFLINE — direct edits to .tscn/.tres/project.godot + headless engine scripts.
  * LIVE    — real-time control of a running editor via the live_bridge addon.

Command groups:
    project   create/info/scenes/scripts/resources/reimport
    scene     create/read/tree/instance/make-editable/override-child/repack
    node      add/remove/move/reparent/rename/duplicate/get-prop/set-prop/...
    signal    connect/disconnect/list
    settings  get/set/unset/list
    autoload  add/remove/enable/disable/list
    input     add/add-event/remove/list
    group     add/remove/list                 (project-wide groups, 4.3+)
    layer     name                            (collision/render layer names)
    resource  create/edit/read/create-curve/create-gradient
    script    new/run/inline/validate/validate-all/format/lint/docs/test
    2d        add-sprite/add-camera/add-body/add-collision/.../tilemap/anim
    export    build/build-all/presets/pack
    engine    version/status
    live      install/status/tree/add/set/get/connect/instance/.../play/stop/undo/redo
    session   interactive REPL
"""

import json as json_mod
import os
import shlex
import sys

import click

from cli_anything.godot.utils.godot_backend import (
    get_version,
    is_available,
    find_godot_binary,
)

# New command groups (each module owns its groups; see godot-build/CONTRACT.md).
from cli_anything.godot.commands.scene_nodes import scene_group, node_group, signal_group
from cli_anything.godot.commands.config import (
    settings_group,
    autoload_group,
    input_group,
    pgroup_group,
    layer_group,
)
from cli_anything.godot.commands.resources import resource_group
from cli_anything.godot.commands.gdscript import script_group
from cli_anything.godot.commands.twod import twod_group
from cli_anything.godot.commands.live import live_group


# ── Output helpers ─────────────────────────────────────────────────────

def _out(ctx, data: dict) -> None:
    """Print result as JSON or human-readable based on context."""
    if ctx.obj.get("json"):
        click.echo(json_mod.dumps(data, indent=2, ensure_ascii=False))
    else:
        status = data.get("status", "")
        if status == "error":
            click.secho(f"Error: {data.get('message', data.get('stderr', 'unknown'))}", fg="red")
            return
        for key, value in data.items():
            if key == "status":
                continue
            if isinstance(value, list):
                click.secho(f"{key} ({len(value)}):", fg="cyan", bold=True)
                for item in value:
                    if isinstance(item, dict):
                        parts = [f"{k}={v}" for k, v in item.items()]
                        click.echo(f"  - {', '.join(parts)}")
                    else:
                        click.echo(f"  - {item}")
            elif isinstance(value, dict):
                click.secho(f"{key}:", fg="cyan", bold=True)
                for k, v in value.items():
                    click.echo(f"  {k}: {v}")
            else:
                click.echo(f"{key}: {value}")


def _handle_error(func):
    """Decorator to catch RuntimeError and format output."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RuntimeError as e:
            ctx = click.get_current_context()
            _out(ctx, {"status": "error", "message": str(e)})
            if not ctx.obj.get("repl"):
                sys.exit(1)
    return wrapper


# ── Root CLI group ─────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output JSON for agent consumption.")
@click.option("--project", "-p", "project", default=None, help="Path to Godot project directory.")
@click.pass_context
def cli(ctx, use_json, project):
    """cli-anything-godot — Agent-native CLI for the Godot game engine."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    ctx.obj["project"] = os.path.abspath(project) if project else None
    ctx.obj["repl"] = ctx.obj.get("repl", False)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def _get_project(ctx) -> str:
    """Resolve project path from context or cwd."""
    p = ctx.obj.get("project") or os.getcwd()
    return os.path.abspath(p)


# ── Project commands ───────────────────────────────────────────────────

@cli.group()
@click.pass_context
def project(ctx):
    """Manage Godot projects — create, inspect, list assets."""
    pass


@project.command("create")
@click.argument("path")
@click.option("--name", default=None, help="Project display name.")
@click.pass_context
@_handle_error
def project_create(ctx, path, name):
    """Create a new Godot project at PATH."""
    from cli_anything.godot.core.project import create_project
    _out(ctx, create_project(os.path.abspath(path), name))


@project.command("info")
@click.pass_context
@_handle_error
def project_info(ctx):
    """Show project metadata from project.godot."""
    from cli_anything.godot.core.project import get_project_info
    _out(ctx, get_project_info(_get_project(ctx)))


@project.command("scenes")
@click.pass_context
@_handle_error
def project_scenes(ctx):
    """List all scene files (.tscn, .scn) in the project."""
    from cli_anything.godot.core.project import list_scenes
    _out(ctx, list_scenes(_get_project(ctx)))


@project.command("scripts")
@click.pass_context
@_handle_error
def project_scripts(ctx):
    """List all GDScript files (.gd) in the project."""
    from cli_anything.godot.core.project import list_scripts
    _out(ctx, list_scripts(_get_project(ctx)))


@project.command("resources")
@click.pass_context
@_handle_error
def project_resources(ctx):
    """List all resource files (.tres, .res) in the project."""
    from cli_anything.godot.core.project import list_resources
    _out(ctx, list_resources(_get_project(ctx)))


@project.command("reimport")
@click.pass_context
@_handle_error
def project_reimport(ctx):
    """Force re-import of all project resources via Godot."""
    from cli_anything.godot.core.project import reimport_project
    _out(ctx, reimport_project(_get_project(ctx)))


# ── Export commands ────────────────────────────────────────────────────

@cli.group("export")
@click.pass_context
def export_group(ctx):
    """Export Godot projects to target platforms."""
    pass


@export_group.command("build")
@click.option("--preset", default=None, help="Export preset name. Omit to export all runnable presets.")
@click.option("--output", default=None, help="Output file path (single named preset only).")
@click.option("--debug", is_flag=True, help="Use debug export instead of release.")
@click.pass_context
@_handle_error
def export_build(ctx, preset, output, debug):
    """Build/export the project using configured presets."""
    from cli_anything.godot.core.export import export_project
    _out(ctx, export_project(_get_project(ctx), preset, output, debug))


@export_group.command("build-all")
@click.option("--debug", is_flag=True, help="Use debug export instead of release.")
@click.pass_context
@_handle_error
def export_build_all(ctx, debug):
    """Export every runnable preset (replaces the bogus --export-all)."""
    from cli_anything.godot.core.export import export_all
    _out(ctx, export_all(_get_project(ctx), debug=debug))


@export_group.command("presets")
@click.pass_context
@_handle_error
def export_presets(ctx):
    """List configured export presets."""
    from cli_anything.godot.core.export import list_export_presets
    _out(ctx, list_export_presets(_get_project(ctx)))


# ── Engine commands ────────────────────────────────────────────────────

@cli.group()
@click.pass_context
def engine(ctx):
    """Godot engine info — version, status."""
    pass


@engine.command("version")
@click.pass_context
@_handle_error
def engine_version(ctx):
    """Show Godot engine version."""
    _out(ctx, get_version())


@engine.command("status")
@click.pass_context
@_handle_error
def engine_status(ctx):
    """Check if Godot binary is available."""
    available = is_available()
    binary = find_godot_binary()
    _out(ctx, {
        "status": "ok",
        "available": available,
        "binary": binary or "not found",
    })


# ── Backward-compat alias: `scene add-node` (superseded by `node add`) ──
# The canonical command is now `node add`. This thin alias keeps older
# scripts/tests working by delegating to the original core.scene.add_node.
@scene_group.command("add-node")
@click.argument("scene_path")
@click.option("--name", "node_name", required=True, help="Name of the new node.")
@click.option("--type", "node_type", required=True, help="Node type (Sprite2D, Camera2D, ...).")
@click.option("--parent", default=".", help="Parent node path (default: root).")
@click.pass_context
@_handle_error
def scene_add_node_alias(ctx, scene_path, node_name, node_type, parent):
    """[deprecated alias] Add a child node to a scene — prefer `node add`."""
    from cli_anything.godot.core.scene import add_node
    _out(ctx, add_node(_get_project(ctx), scene_path, node_name, node_type, parent))


# ── Register modular command groups ────────────────────────────────────
# Offline layer
cli.add_command(scene_group)      # scene
cli.add_command(node_group)       # node
cli.add_command(signal_group)     # signal
cli.add_command(settings_group)   # settings
cli.add_command(autoload_group)   # autoload
cli.add_command(input_group)      # input
cli.add_command(pgroup_group)     # group
cli.add_command(layer_group)      # layer
cli.add_command(resource_group)   # resource
cli.add_command(script_group)     # script
cli.add_command(twod_group)       # 2d
# Live layer
cli.add_command(live_group)       # live


# ── REPL session ───────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def session(ctx):
    """Start an interactive REPL session."""
    ctx.obj["repl"] = True

    try:
        from cli_anything.godot.utils.repl_skin import ReplSkin
        skin = ReplSkin("godot", version="1.0.0")
        skin.print_banner()
    except ImportError:
        skin = None
        click.secho("cli-anything-godot REPL", fg="green", bold=True)
        click.echo("Type 'help' for commands, 'exit' to quit.\n")

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    prompt_session = PromptSession(history=InMemoryHistory())
    project_path = ctx.obj.get("project")
    project_name = os.path.basename(project_path) if project_path else "no-project"

    while True:
        try:
            if skin:
                prompt_text = skin.prompt(project_name=project_name, modified=False)
            else:
                prompt_text = f"godot ({project_name})> "

            line = prompt_session.prompt(prompt_text)
            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit", "q"):
                break
            if line == "help":
                click.echo(cli.get_help(click.Context(cli)))
                continue

            try:
                args = shlex.split(line)
            except ValueError as e:
                click.secho(f"Parse error: {e}", fg="red")
                continue

            try:
                cli.main(args=args, standalone_mode=False, obj=ctx.obj)
            except SystemExit:
                pass
            except click.exceptions.UsageError as e:
                click.secho(str(e), fg="red")

        except KeyboardInterrupt:
            continue
        except EOFError:
            break

    if skin:
        skin.print_goodbye()
    else:
        click.echo("Goodbye.")


# ── Entry point ────────────────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
