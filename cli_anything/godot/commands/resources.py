"""``resource`` command group — create / edit / read ``.tres`` resources, plus
``create-curve`` / ``create-gradient`` for complex typed resources.

Per SPEC-offline §C.4 resource table. Simple props are pure FILE edits; complex
typed resources go through the engine (SCRIPT). All commands inherit ``--project``
and ``--json`` from the root CLI.
"""

from __future__ import annotations

import os

import click

from cli_anything.godot.core.output import emit, handle_error
from cli_anything.godot.core import resources as core


resource_group = click.Group(
    "resource",
    help="Create/edit/read .tres resources (and Curve/Gradient builders).",
)


def _project(ctx) -> str:
    return os.path.abspath(ctx.obj.get("project") or os.getcwd())


def _parse_kv(pairs) -> dict:
    """Parse ``k=v`` strings into a dict (values kept as raw strings)."""
    out = {}
    for item in pairs:
        if "=" not in item:
            raise RuntimeError(f"--prop must be key=value, got: {item}")
        k, _, v = item.partition("=")
        out[k.strip()] = v
    return out


@resource_group.command("create")
@click.argument("tres_path")
@click.option("--type", "resource_type", default="Resource",
              help="Base resource class (Resource, Curve, Gradient, ...).")
@click.option("--script", default=None, help="res:// path to a custom resource script.")
@click.option("--class-name", "class_name", default=None,
              help="Registered class_name of the attached script.")
@click.option("--prop", "props", multiple=True, metavar="K=V",
              help="Property key=value (repeatable). Values inferred or quoted.")
@click.pass_context
@handle_error
def resource_create_cmd(ctx, tres_path, resource_type, script, class_name, props):
    """Create a .tres resource at TRES_PATH (project-relative or res://)."""
    emit(ctx, core.resource_create(
        _project(ctx), tres_path, resource_type=resource_type,
        script=script, class_name=class_name, props=_parse_kv(props),
    ))


@resource_group.command("edit")
@click.argument("tres_path")
@click.option("--prop", "props", multiple=True, metavar="K=V",
              help="Property key=value (repeatable).")
@click.option("--ext-resource", "ext_resource", default=None, metavar="KEY=res://path[:Type]",
              help="Set KEY to an ExtResource reference (added/reused).")
@click.option("--sub-resource", "sub_resource", default=None, metavar="KEY=Type:k=v;k=v",
              help="Set KEY to a new SubResource of Type with sub-props.")
@click.pass_context
@handle_error
def resource_edit_cmd(ctx, tres_path, props, ext_resource, sub_resource):
    """Edit properties / references of an existing .tres."""
    ext = _parse_ext(ext_resource) if ext_resource else None
    sub = _parse_sub(sub_resource) if sub_resource else None
    emit(ctx, core.resource_edit(
        _project(ctx), tres_path, props=_parse_kv(props),
        ext_resource=ext, sub_resource=sub,
    ))


@resource_group.command("read")
@click.argument("tres_path")
@click.pass_context
@handle_error
def resource_read_cmd(ctx, tres_path):
    """Read and print the parsed properties of a .tres."""
    emit(ctx, core.resource_read(_project(ctx), tres_path))


@resource_group.command("create-curve")
@click.argument("tres_path")
@click.option("--point", "points", multiple=True, required=True, metavar="t,v",
              help="Curve point as t,v (repeatable). t,v,left,right also accepted.")
@click.pass_context
@handle_error
def resource_create_curve_cmd(ctx, tres_path, points):
    """Create a Curve .tres from --point t,v pairs (engine-backed)."""
    pts = [tuple(float(x) for x in p.split(",")) for p in points]
    emit(ctx, core.create_curve(_project(ctx), tres_path, pts))


@resource_group.command("create-gradient")
@click.argument("tres_path")
@click.option("--stop", "stops", multiple=True, required=True, metavar="offset,Color(...)",
              help="Gradient stop as offset,Color(r,g,b,a) (repeatable).")
@click.pass_context
@handle_error
def resource_create_gradient_cmd(ctx, tres_path, stops):
    """Create a Gradient .tres from --stop offset,color pairs (engine-backed)."""
    parsed = []
    for s in stops:
        offset, _, color = s.partition(",")
        parsed.append((float(offset), color.strip()))
    emit(ctx, core.create_gradient(_project(ctx), tres_path, parsed))


# ── option parsing helpers ─────────────────────────────────────────────

def _parse_ext(spec: str) -> tuple:
    """Parse ``KEY=res://path[:Type]`` -> (key, path[, type])."""
    if "=" not in spec:
        raise RuntimeError(f"--ext-resource must be KEY=path, got: {spec}")
    key, _, rest = spec.partition("=")
    # split a trailing ':Type' that is NOT part of res://
    path = rest
    etype = None
    if ":" in rest:
        head, _, tail = rest.rpartition(":")
        # avoid splitting 'res://...' scheme colon
        if head and not head.endswith("/") and "://" not in tail:
            path, etype = head, tail
    if etype:
        return (key.strip(), path, etype)
    return (key.strip(), path)


def _parse_sub(spec: str) -> tuple:
    """Parse ``KEY=Type:k=v;k=v`` -> (key, type, {props})."""
    if "=" not in spec:
        raise RuntimeError(f"--sub-resource must be KEY=Type:..., got: {spec}")
    key, _, rest = spec.partition("=")
    if ":" in rest:
        stype, _, propstr = rest.partition(":")
        sprops = {}
        for kv in filter(None, propstr.split(";")):
            k, _, v = kv.partition("=")
            sprops[k.strip()] = v
        return (key.strip(), stype.strip(), sprops)
    return (key.strip(), rest.strip(), {})
