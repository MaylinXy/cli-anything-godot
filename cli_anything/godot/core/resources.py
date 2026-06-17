"""Resource (`.tres`) create / edit / read, plus complex-resource builders.

Two mechanisms (see SPEC-offline §C.4 resource table, §D):

  * **FILE** — for a plain ``Resource`` (or any base type) with simple, scalar
    properties we write the `.tres` directly via
    :class:`cli_anything.godot.core.tscn.TscnFile.new_resource`. This is fast,
    hermetic, and needs no engine.
  * **SCRIPT** — for typed/complex resources (``Curve``, ``Gradient``, …) whose
    text form is large and order-sensitive, we build the object via the engine's
    own class API in a generated ``extends SceneTree`` script and persist it with
    ``ResourceSaver.save`` so the engine produces canonical output.

Every mutating function returns a dict with at least ``status`` and ``changed``.
Core functions never print / sys.exit; they raise ``RuntimeError`` on failure so
the ``handle_error`` decorator in the command layer can format it.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_anything.godot.core import variant_fmt
from cli_anything.godot.core.tscn import TscnFile
from cli_anything.godot.utils import godot_backend


# Base resource classes that cannot be safely hand-written as simple props and
# must be built through the engine class API instead (typed/complex resources).
_COMPLEX_BASE_TYPES = {
    "Curve", "Curve2D", "Curve3D", "Gradient", "GradientTexture1D",
    "GradientTexture2D", "ArrayMesh", "Mesh", "TileSet", "AnimationLibrary",
    "Animation", "NavigationMesh", "NavigationPolygon",
}


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────

def _abs(project_path: str, tres_path: str) -> Path:
    """Resolve a (possibly ``res://``) `.tres` path under the project."""
    rel = tres_path
    if rel.startswith("res://"):
        rel = rel[len("res://"):]
    return Path(project_path) / rel


def _res_path(project_path: str, abs_path: Path) -> str:
    """Return the ``res://`` form of ``abs_path`` within the project."""
    rel = abs_path.relative_to(Path(project_path)).as_posix()
    return f"res://{rel}"


def _coerce_prop(value):
    """Turn a Python value from the caller into a raw Godot literal string.

    Accepts already-literal strings (passed through verbatim when they look like
    a Godot constructor / reference) or plain Python scalars (inferred).
    """
    if isinstance(value, variant_fmt.GDValue):
        return value.raw
    if isinstance(value, str):
        # A bare string from the CLI is ambiguous. Heuristic: if it already
        # looks like a Godot literal (number, bool, constructor, ref, quoted,
        # array/dict) keep it raw; otherwise quote it as a String.
        return _infer_literal(value)
    return variant_fmt.to_literal(value)


_LITERAL_PREFIXES = (
    "Vector", "Color", "Rect2", "Transform", "NodePath", "ExtResource",
    "SubResource", "Array", "Dictionary", "Packed", "Object", "Basis",
    "Quaternion", "Plane", "AABB", "Projection", "StringName",
)


def _infer_literal(s: str) -> str:
    """Best-effort: decide whether a CLI string is already a Godot literal."""
    t = s.strip()
    if t in ("true", "false", "null"):
        return t
    if t.startswith('"') and t.endswith('"'):
        return t
    if t and (t[0] in "[{" or t.startswith(_LITERAL_PREFIXES)):
        return t
    # number?
    try:
        int(t)
        return t
    except ValueError:
        pass
    try:
        float(t)
        return variant_fmt._fmt_number(float(t))
    except ValueError:
        pass
    # plain text -> quoted string
    return variant_fmt.quote_string(s)


def _props_to_raw(props: dict | None) -> dict:
    """Convert a {key: pyvalue} dict into {key: raw_literal}."""
    out = {}
    for k, v in (props or {}).items():
        out[k] = _coerce_prop(v)
    return out


# ──────────────────────────────────────────────────────────────────────
# resource create / edit / read (FILE; SCRIPT fallback for complex types)
# ──────────────────────────────────────────────────────────────────────

def resource_create(project_path: str, tres_path: str, resource_type: str = "Resource",
                    script: str | None = None, class_name: str | None = None,
                    props: dict | None = None) -> dict:
    """Create a ``.tres`` resource file.

    Simple props on a plain/scriptable base type are written directly (FILE).
    When ``resource_type`` is a known complex base (Curve/Gradient/…) and no
    custom ``script`` is attached, we build a bare instance via the engine
    (SCRIPT) so the engine serializes it canonically, then apply any simple
    props on top via a follow-up FILE edit.

    Args:
        project_path: Godot project dir.
        tres_path: Output path (``res://`` or project-relative).
        resource_type: Base resource class (default ``Resource``).
        script: Optional ``res://`` path to a custom resource script.
        class_name: Optional registered ``class_name`` of that script.
        props: Optional {key: value} of @export / resource properties.

    Returns:
        Dict with status / changed / path / mechanism.
    """
    abs_path = _abs(project_path, tres_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    raw_props = _props_to_raw(props)

    complex_no_script = resource_type in _COMPLEX_BASE_TYPES and not script
    if complex_no_script:
        # Build a bare instance through the engine, then layer simple props.
        res_out = _res_path(project_path, abs_path)
        gd = _save_bare_resource_gd(resource_type, res_out)
        run = godot_backend.run_generated_script(project_path, gd, timeout=120)
        _assert_saved(run, abs_path, what=resource_type)
        if raw_props:
            # parse what the engine wrote and apply props on top
            f = TscnFile.parse(abs_path.read_text(encoding="utf-8"))
            for k, v in raw_props.items():
                f.resource_props[k] = v
            abs_path.write_text(f.serialize(), encoding="utf-8")
        return {
            "status": "ok", "changed": True, "path": tres_path,
            "type": resource_type, "mechanism": "SCRIPT",
        }

    # FILE path: write the .tres directly.
    f = TscnFile.new_resource(resource_type, script_path=script, script_class=class_name)
    for k, v in raw_props.items():
        f.resource_props[k] = v
    abs_path.write_text(f.serialize(), encoding="utf-8")
    return {
        "status": "ok", "changed": True, "path": tres_path,
        "type": resource_type, "mechanism": "FILE",
    }


def resource_edit(project_path: str, tres_path: str, props: dict | None = None,
                  ext_resource: tuple | None = None,
                  sub_resource: tuple | None = None) -> dict:
    """Edit an existing ``.tres`` resource (FILE).

    Args:
        props: {key: value} property overrides (added/updated in [resource]).
        ext_resource: optional (prop_key, "res://path"[, type]) — add/reuse an
            ext_resource and set ``prop_key = ExtResource(id)``.
        sub_resource: optional (prop_key, type, {sub_props}) — create a
            ``[sub_resource]`` and set ``prop_key = SubResource(id)``.

    Returns:
        Dict status / changed / updated keys.
    """
    abs_path = _abs(project_path, tres_path)
    if not abs_path.exists():
        raise RuntimeError(f"Resource not found: {tres_path}")
    f = TscnFile.parse(abs_path.read_text(encoding="utf-8"))
    if f.kind != "resource":
        raise RuntimeError(f"Not a .tres resource: {tres_path}")

    before = dict(f.resource_props)
    updated: list[str] = []

    for k, v in _props_to_raw(props).items():
        f.resource_props[k] = v
        updated.append(k)

    if ext_resource:
        key = ext_resource[0]
        path = ext_resource[1]
        etype = ext_resource[2] if len(ext_resource) > 2 else _guess_ext_type(path)
        eid = f.add_ext_resource(etype, path)
        f.resource_props[key] = variant_fmt.ext_ref(eid)
        updated.append(key)

    if sub_resource:
        key = sub_resource[0]
        stype = sub_resource[1]
        sprops = _props_to_raw(sub_resource[2]) if len(sub_resource) > 2 else {}
        sid = f.add_sub_resource(stype, sprops)
        f.resource_props[key] = variant_fmt.sub_ref(sid)
        updated.append(key)

    changed = dict(f.resource_props) != before or bool(ext_resource) or bool(sub_resource)
    if changed:
        abs_path.write_text(f.serialize(), encoding="utf-8")
    return {
        "status": "ok", "changed": changed, "path": tres_path,
        "updated": sorted(set(updated)),
    }


def resource_read(project_path: str, tres_path: str) -> dict:
    """Read a ``.tres`` and return its parsed properties (FILE).

    Property values are parsed to Python where feasible (numbers/bool/strings/
    vectors); references and unrecognized literals are returned as their raw
    text.
    """
    abs_path = _abs(project_path, tres_path)
    if not abs_path.exists():
        raise RuntimeError(f"Resource not found: {tres_path}")
    f = TscnFile.parse(abs_path.read_text(encoding="utf-8"))
    if f.kind != "resource":
        raise RuntimeError(f"Not a .tres resource: {tres_path}")

    parsed = {}
    for k, raw in f.resource_props.items():
        val = variant_fmt.parse_literal(raw)
        parsed[k] = val.raw if isinstance(val, variant_fmt.GDValue) else val

    ext = [{"id": e.id, "type": e.type, "path": e.path} for e in f.ext]
    sub = [{"id": s.id, "type": s.type} for s in f.sub]
    return {
        "status": "ok", "changed": False, "path": tres_path,
        "type": f.resource_type, "script_class": f.script_class,
        "props": parsed, "ext_resources": ext, "sub_resources": sub,
    }


# ──────────────────────────────────────────────────────────────────────
# complex resources via SCRIPT (Curve / Gradient)
# ──────────────────────────────────────────────────────────────────────

def create_curve(project_path: str, tres_path: str, points: list) -> dict:
    """Create a ``Curve`` ``.tres`` with the given (t, v) points (SCRIPT).

    Builds the Curve through the engine class API (``add_point``) and persists
    via ``ResourceSaver.save`` so point ordering/baking is engine-correct.

    Args:
        points: list of (t, value) or (t, value, left_tangent, right_tangent).
    """
    if not points:
        raise RuntimeError("create_curve requires at least one point")
    abs_path = _abs(project_path, tres_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    res_out = _res_path(project_path, abs_path)

    add_lines = []
    for p in points:
        t = float(p[0])
        v = float(p[1])
        if len(p) >= 4:
            add_lines.append(
                f"\tcurve.add_point(Vector2({t}, {v}), {float(p[2])}, {float(p[3])})"
            )
        else:
            add_lines.append(f"\tcurve.add_point(Vector2({t}, {v}))")
    body = "\n".join(add_lines)

    gd = (
        "extends SceneTree\n"
        "func _init():\n"
        "\tvar curve = Curve.new()\n"
        f"{body}\n"
        f'\tvar err = ResourceSaver.save(curve, "{res_out}")\n'
        '\tif err != OK:\n'
        '\t\tpush_error("SAVE_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("SAVED ", curve.point_count)\n'
        "\tquit(0)\n"
    )
    run = godot_backend.run_generated_script(project_path, gd, timeout=120)
    _assert_saved(run, abs_path, what="Curve")
    return {
        "status": "ok", "changed": True, "path": tres_path,
        "type": "Curve", "mechanism": "SCRIPT", "point_count": len(points),
    }


def create_gradient(project_path: str, tres_path: str, stops: list) -> dict:
    """Create a ``Gradient`` ``.tres`` with (offset, color) stops (SCRIPT).

    Args:
        stops: list of (offset, color) where color is either a Godot ``Color(...)``
            literal string or an (r, g, b[, a]) tuple.
    """
    if not stops:
        raise RuntimeError("create_gradient requires at least one stop")
    abs_path = _abs(project_path, tres_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    res_out = _res_path(project_path, abs_path)

    offsets = ", ".join(str(float(o)) for o, _ in stops)
    colors = ", ".join(_color_literal(c) for _, c in stops)

    # Gradient ships with 2 default points and enforces a minimum, so removing
    # them in a loop can spin forever. Instead assign the offsets/colors packed
    # arrays directly (engine resizes the point list to match).
    gd = (
        "extends SceneTree\n"
        "func _init():\n"
        "\tvar grad = Gradient.new()\n"
        f"\tgrad.offsets = PackedFloat32Array([{offsets}])\n"
        f"\tgrad.colors = PackedColorArray([{colors}])\n"
        f'\tvar err = ResourceSaver.save(grad, "{res_out}")\n'
        '\tif err != OK:\n'
        '\t\tpush_error("SAVE_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("SAVED ", grad.get_point_count())\n'
        "\tquit(0)\n"
    )
    run = godot_backend.run_generated_script(project_path, gd, timeout=120)
    _assert_saved(run, abs_path, what="Gradient")
    return {
        "status": "ok", "changed": True, "path": tres_path,
        "type": "Gradient", "mechanism": "SCRIPT", "point_count": len(stops),
    }


# ──────────────────────────────────────────────────────────────────────
# internal SCRIPT helpers
# ──────────────────────────────────────────────────────────────────────

def _save_bare_resource_gd(resource_type: str, res_out: str) -> str:
    return (
        "extends SceneTree\n"
        "func _init():\n"
        f"\tvar r = {resource_type}.new()\n"
        f'\tvar err = ResourceSaver.save(r, "{res_out}")\n'
        '\tif err != OK:\n'
        '\t\tpush_error("SAVE_FAILED")\n'
        "\t\tquit(1)\n"
        "\t\treturn\n"
        '\tprint("SAVED")\n'
        "\tquit(0)\n"
    )


def _assert_saved(run: dict, abs_path: Path, *, what: str) -> None:
    out = (run.get("stdout") or "")
    err = (run.get("stderr") or "")
    if "SAVED" not in out or not abs_path.exists():
        raise RuntimeError(
            f"Engine failed to build/save {what}.\n"
            f"rc={run.get('returncode')}\nstdout={out}\nstderr={err}"
        )


def _color_literal(color) -> str:
    if isinstance(color, str):
        return color if color.startswith("Color(") else variant_fmt.to_literal(color)
    if isinstance(color, (list, tuple)):
        comps = list(color)
        if len(comps) == 3:
            comps.append(1.0)
        return variant_fmt.to_literal(tuple(comps), "color")
    raise RuntimeError(f"Unsupported color value: {color!r}")


def _guess_ext_type(path: str) -> str:
    p = path.lower()
    if p.endswith(".gd"):
        return "Script"
    if p.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
        return "Texture2D"
    if p.endswith((".tscn", ".scn")):
        return "PackedScene"
    return "Resource"
