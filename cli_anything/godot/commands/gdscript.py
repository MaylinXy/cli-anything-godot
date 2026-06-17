"""``script`` command group — GDScript tooling.

Supersedes the inline ``script`` group in the baseline ``godot_cli.py``. The
``run`` / ``inline`` / ``validate`` subcommands call the EXISTING
``core.script`` functions so behaviour matches the baseline exactly; the rest
(new/validate-all/format/lint/docs/test) come from ``core.gdscript_tools``.

Per SPEC-offline §C.3. All commands inherit ``--project`` / ``--json`` from root.
"""

from __future__ import annotations

import os

import click

from cli_anything.godot.core.output import emit, handle_error
from cli_anything.godot.core import script as core_script
from cli_anything.godot.core import gdscript_tools as tools


script_group = click.Group(
    "script",
    help="GDScript tooling: new/run/inline/validate/validate-all/format/lint/docs/test.",
)


def _project(ctx) -> str:
    return os.path.abspath(ctx.obj.get("project") or os.getcwd())


# ── existing baseline behaviour (reused, not reimplemented) ─────────────

@script_group.command("run")
@click.argument("script_path")
@click.option("--timeout", default=60, help="Execution timeout in seconds.")
@click.pass_context
@handle_error
def script_run_cmd(ctx, script_path, timeout):
    """Execute a GDScript in headless mode (must extend SceneTree/MainLoop)."""
    emit(ctx, core_script.run_script(_project(ctx), script_path, timeout))


@script_group.command("inline")
@click.argument("code")
@click.option("--timeout", default=60, help="Execution timeout in seconds.")
@click.pass_context
@handle_error
def script_inline_cmd(ctx, code, timeout):
    """Run inline GDScript code (wrapped in SceneTree._init). Trusted input only."""
    emit(ctx, core_script.run_inline(_project(ctx), code, timeout))


@script_group.command("validate")
@click.argument("script_path")
@click.pass_context
@handle_error
def script_validate_cmd(ctx, script_path):
    """Validate GDScript syntax without executing (--check-only + stderr scan)."""
    emit(ctx, core_script.validate_script(_project(ctx), script_path))


# ── new subcommands ─────────────────────────────────────────────────────

@script_group.command("new")
@click.argument("path")
@click.option("--extends", "extends", default="Node", help="Base class.")
@click.option("--class-name", "class_name", default=None, help="Registered class_name.")
@click.option("--tool", "tool_flag", is_flag=True, help="Emit @tool (runs in editor).")
@click.pass_context
@handle_error
def script_new_cmd(ctx, path, extends, class_name, tool_flag):
    """Create a skeleton .gd script at PATH."""
    emit(ctx, tools.script_new(_project(ctx), path, extends=extends,
                               class_name=class_name, tool=tool_flag))


@script_group.command("validate-all")
@click.pass_context
@handle_error
def script_validate_all_cmd(ctx):
    """Validate every *.gd in the project."""
    emit(ctx, tools.validate_all(_project(ctx)))


@script_group.command("format")
@click.argument("path")
@click.option("--write", is_flag=True, help="Apply formatting in place.")
@click.option("--check", is_flag=True, help="Check-only (no writes); flags files needing format.")
@click.pass_context
@handle_error
def script_format_cmd(ctx, path, write, check):
    """Format a .gd file/dir with gdformat (optional gdtoolkit dep)."""
    emit(ctx, tools.script_format(_project(ctx), path, write=write, check=check))


@script_group.command("lint")
@click.argument("path")
@click.pass_context
@handle_error
def script_lint_cmd(ctx, path):
    """Lint a .gd file/dir with gdlint (optional gdtoolkit dep)."""
    emit(ctx, tools.script_lint(_project(ctx), path))


@script_group.command("docs")
@click.option("--out", "out_dir", required=True, help="Output dir for XML docs.")
@click.option("--path", "src_path", default="res://", help="Source path to document.")
@click.pass_context
@handle_error
def script_docs_cmd(ctx, out_dir, src_path):
    """Generate GDScript API docs from ## comments (--gdscript-docs)."""
    emit(ctx, tools.script_docs(_project(ctx), out_dir, src_path))


@script_group.command("test")
@click.option("--dir", "test_dir", default="res://tests", help="Test directory.")
@click.option("--pattern", default="test_*.gd", help="Test file glob pattern.")
@click.option("--timeout", default=180, help="Harness timeout in seconds.")
@click.pass_context
@handle_error
def script_test_cmd(ctx, test_dir, pattern, timeout):
    """Run a generated headless test harness over test_*.gd files."""
    emit(ctx, tools.script_test(_project(ctx), test_dir, pattern=pattern, timeout=timeout))
