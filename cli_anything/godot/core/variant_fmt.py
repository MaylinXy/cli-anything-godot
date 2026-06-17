"""Godot Variant text <-> Python helpers.

Property values in `.tscn`/`.tres` are stored as RAW Godot-literal strings (the
text after `=`). This module converts between convenient Python inputs and that
raw text.

Target: Godot 4.3 text format (format=3). See SPEC-offline §B "Variant value
encodings".
"""

from __future__ import annotations

import re
from typing import Any


class GDValue:
    """Opaque wrapper for a raw Godot literal we don't decompose.

    ``.raw`` is the verbatim literal text (e.g. ``'Transform2D(1, 0, 0, 1, 0, 0)'``).
    Used when ``parse_literal`` meets something it can't turn into a plain Python
    value, and as an explicit "this is already a literal" marker for ``to_literal``.
    """

    __slots__ = ("raw",)

    def __init__(self, raw: str):
        self.raw = str(raw)

    def __repr__(self) -> str:
        return f"GDValue({self.raw!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GDValue) and other.raw == self.raw

    def __hash__(self) -> int:
        return hash(self.raw)

    def __str__(self) -> str:
        return self.raw


# ---------- reference helpers ----------

def ext_ref(id_: str) -> str:
    """Return the literal for an ext_resource reference: ``ExtResource("1_x")``."""
    return f'ExtResource("{id_}")'


def sub_ref(id_: str) -> str:
    """Return the literal for a sub_resource reference: ``SubResource("Type_x")``."""
    return f'SubResource("{id_}")'


# ---------- string escaping ----------

_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
}


def escape_string(s: str) -> str:
    """C-escape a Python string into a Godot quoted string literal (no quotes)."""
    out = []
    for ch in s:
        out.append(_ESCAPES.get(ch, ch))
    return "".join(out)


def quote_string(s: str) -> str:
    """Return a fully quoted Godot string literal: ``"..."``."""
    return '"' + escape_string(s) + '"'


def _unescape_string(s: str) -> str:
    """Reverse of escape_string for the contents between quotes."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ---------- number formatting (match Godot's serializer style) ----------

def _fmt_number(value) -> str:
    """Format an int/float the way Godot writes them (ints bare, floats with .0)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        if value == int(value) and abs(value) < 1e16:
            return f"{int(value)}.0"
        return repr(value)
    return str(value)


def _fmt_components(values) -> str:
    return ", ".join(_fmt_number(v) for v in values)


# ---------- to_literal ----------

_VECTOR_KINDS = {
    "vector2": ("Vector2", 2),
    "vector2i": ("Vector2i", 2),
    "vector3": ("Vector3", 3),
    "vector3i": ("Vector3i", 3),
    "vector4": ("Vector4", 4),
    "vector4i": ("Vector4i", 4),
    "color": ("Color", 4),
    "rect2": ("Rect2", 4),
    "rect2i": ("Rect2i", 4),
}


def to_literal(value: Any, kind: str | None = None) -> str:
    """Convert a Python value to Godot literal text.

    kind in {None,'int','float','bool','string','vector2','vector2i','vector3',
    'vector3i','vector4','color','rect2','nodepath','raw', ...}.

    - kind=None infers from the Python type.
    - kind='raw' returns ``str(value)`` verbatim (caller already wrote a literal).

    Examples::

        to_literal((10, 20), 'vector2')  -> 'Vector2(10, 20)'
        to_literal('hi')                 -> '"hi"'
        to_literal(True)                 -> 'true'
    """
    if kind == "raw":
        return str(value)

    if isinstance(value, GDValue):
        return value.raw

    if kind is not None:
        kind = kind.lower()

    # Explicit kind handling
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        return _fmt_number(float(value))
    if kind == "string":
        return quote_string(str(value))
    if kind == "nodepath":
        return f'NodePath({quote_string(str(value))})'
    if kind in _VECTOR_KINDS:
        name, count = _VECTOR_KINDS[kind]
        comps = _coerce_components(value, count)
        return f"{name}({_fmt_components(comps)})"

    # Inference (kind is None)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _fmt_number(value)
    if isinstance(value, str):
        return quote_string(value)
    if value is None:
        return "null"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(to_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ",\n".join(f"{quote_string(str(k))}: {to_literal(v)}" for k, v in value.items())
        return "{\n" + inner + "\n}"
    # Fallback: stringify
    return str(value)


def _coerce_components(value, count: int):
    """Turn a tuple/list/str into a list of numeric components of the right length."""
    if isinstance(value, str):
        # accept "10,20" forms
        parts = [p.strip() for p in value.split(",") if p.strip()]
        comps = [_str_to_number(p) for p in parts]
    elif isinstance(value, (list, tuple)):
        comps = list(value)
    else:
        comps = [value]
    return comps


def _str_to_number(s: str):
    s = s.strip()
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        return float(s)
    except ValueError:
        return s


# ---------- parse_literal ----------

_CTOR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$", re.DOTALL)
_NUMERIC_CTORS = {
    "Vector2", "Vector2i", "Vector3", "Vector3i", "Vector4", "Vector4i",
    "Color", "Rect2", "Rect2i",
}


def parse_literal(text: str):
    """Convert Godot literal text to a Python value where feasible.

    numbers -> int/float, bool -> bool, quoted string -> str, null -> None,
    Vector2/Color/Rect2/... -> tuple of numbers, NodePath("x") -> GDValue,
    arrays -> list (best effort). Anything unrecognized -> GDValue(raw=text).
    """
    if text is None:
        return None
    s = text.strip()
    if s == "":
        return GDValue("")

    # bool / null
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "null":
        return None

    # quoted string
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"' and _is_balanced_quote(s):
        return _unescape_string(s[1:-1])

    # number
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?", s) and (
        "." in s or "e" in s or "E" in s
    ):
        try:
            return float(s)
        except ValueError:
            pass

    # constructor forms like Vector2(...), Color(...)
    m = _CTOR_RE.match(s)
    if m:
        name, inner = m.group(1), m.group(2)
        if name in _NUMERIC_CTORS:
            try:
                comps = [_str_to_number(p) for p in _split_top_level(inner)]
                if all(isinstance(c, (int, float)) for c in comps):
                    return tuple(comps)
            except Exception:
                pass
        # NodePath, ExtResource, SubResource, Object(...), etc -> keep raw
        return GDValue(s)

    # untyped array
    if s[0] == "[" and s[-1] == "]":
        try:
            items = _split_top_level(s[1:-1])
            return [parse_literal(it) for it in items if it.strip() != ""]
        except Exception:
            return GDValue(s)

    return GDValue(s)


def _is_balanced_quote(s: str) -> bool:
    """True if s is a single string literal (no unescaped internal closing quote)."""
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return False
    i = 1
    n = len(s) - 1
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return False
        i += 1
    return True


def _split_top_level(s: str) -> list[str]:
    """Split a comma-separated list, respecting nested (), [], {} and strings."""
    parts = []
    depth = 0
    in_str = False
    buf = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_str:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts
