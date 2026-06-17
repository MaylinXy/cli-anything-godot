"""Project-settings / autoload / input-map / group / layer operations.

All functions operate on a Godot project directory: they load `project.godot`
via FOUND's :class:`ConfigFile`, mutate it, and save it back. Values are kept as
RAW Godot-literal text (ConfigFile stores the verbatim text after `=`).

Target: Godot 4.3 (`config_version=5`; `[global_group]` exists). See
SPEC-offline-layer §C.1 (behavior) and §B.1 (project.godot format, especially the
input-map `Object(InputEventKey,...)` encoding).

Each mutating function returns a dict with ``status`` and ``changed`` keys; core
functions never print or sys.exit — they raise ``RuntimeError`` on failure (per
CONTRACT).
"""

from __future__ import annotations

import os
from typing import Any

from cli_anything.godot.core.configfile import ConfigFile
from cli_anything.godot.core import variant_fmt


# ─────────────────────────────────────────────────────────────────────────
# project.godot helpers
# ─────────────────────────────────────────────────────────────────────────

def _project_godot(project_dir: str) -> str:
    path = os.path.join(project_dir, "project.godot")
    if not os.path.isfile(path):
        raise RuntimeError(f"Not a Godot project (no project.godot): {project_dir}")
    return path


def _load(project_dir: str) -> tuple[ConfigFile, str]:
    path = _project_godot(project_dir)
    return ConfigFile.load(path), path


def _split_key(key: str) -> tuple[str, str]:
    """Split a settings key like ``application/config/name`` into
    (section, subkey) = ("application", "config/name").

    A key with no slash (e.g. ``config_version``) maps to the implicit top
    section ('').
    """
    key = key.strip().strip("/")
    if "/" not in key:
        return "", key
    section, _, rest = key.partition("/")
    return section, rest


# ─────────────────────────────────────────────────────────────────────────
# settings get / set / unset / list
# ─────────────────────────────────────────────────────────────────────────

# kinds accepted by `settings set --type`
_SET_TYPES = {"string", "int", "float", "bool", "color", "vector2", "raw"}


def settings_get(project_dir: str, key: str) -> dict:
    """Return the raw literal value for a settings key (or None if unset)."""
    cf, _ = _load(project_dir)
    section, subkey = _split_key(key)
    raw = cf.get(section, subkey, None)
    return {
        "status": "ok",
        "key": key,
        "value": raw,
        "found": raw is not None,
    }


def settings_set(project_dir: str, key: str, value: Any, type: str = "string") -> dict:
    """Set a project setting.

    ``type`` in {string,int,float,bool,color,vector2,raw}. ``raw`` writes the
    value verbatim; the others are converted to the proper Godot literal via
    variant_fmt.to_literal.
    """
    kind = (type or "string").lower()
    if kind not in _SET_TYPES:
        raise RuntimeError(
            f"Unknown --type {type!r}; expected one of {sorted(_SET_TYPES)}"
        )

    if kind == "bool":
        # Accept common truthy/falsey strings from the CLI.
        if isinstance(value, str):
            literal = "true" if value.strip().lower() in ("1", "true", "yes", "on") else "false"
        else:
            literal = "true" if value else "false"
    else:
        # to_literal handles raw/string/int/float/color/vector2.
        literal = variant_fmt.to_literal(value, kind)

    cf, path = _load(project_dir)
    section, subkey = _split_key(key)
    old = cf.get(section, subkey, None)
    changed = old != literal
    if changed:
        cf.set(section, subkey, literal)
        cf.save(path)
    return {
        "status": "ok",
        "changed": changed,
        "key": key,
        "value": literal,
    }


def settings_unset(project_dir: str, key: str) -> dict:
    """Delete a settings line (revert to engine default)."""
    cf, path = _load(project_dir)
    section, subkey = _split_key(key)
    removed = cf.unset(section, subkey)
    if removed:
        cf.save(path)
    return {"status": "ok", "changed": removed, "key": key}


def settings_list(project_dir: str, section: str | None = None) -> dict:
    """List settings. With ``section`` given, list only that section's keys;
    otherwise list every (section, key, value).
    """
    cf, _ = _load(project_dir)
    items: list[dict] = []
    if section:
        if not cf.has_section(section):
            return {"status": "ok", "section": section, "settings": []}
        for k, v in cf.section_items(section):
            items.append({"key": k, "value": v})
        return {"status": "ok", "section": section, "settings": items}

    for sec in cf.sections():
        for k, v in cf.section_items(sec):
            full = f"{sec}/{k}" if sec else k
            items.append({"key": full, "value": v})
    return {"status": "ok", "settings": items}


# ─────────────────────────────────────────────────────────────────────────
# autoload
# ─────────────────────────────────────────────────────────────────────────
#
# [autoload] entries: NAME="*res://path"  (leading * = enabled).
# Value is a quoted string whose contents start with '*' when enabled.

def _autoload_res_path(path: str) -> str:
    """Normalize a user-supplied path to a res:// path (leave res:// as-is)."""
    p = path.strip()
    if p.startswith("res://") or p.startswith("user://"):
        return p
    # Strip a leading project-relative slash and prefix res://.
    return "res://" + p.lstrip("/")


def autoload_add(project_dir: str, name: str, path: str, disabled: bool = False) -> dict:
    """Register an autoload singleton.

    Writes ``NAME="*res://..."`` (enabled) or ``NAME="res://..."`` (disabled).
    """
    res_path = _autoload_res_path(path)
    star = "" if disabled else "*"
    literal = variant_fmt.quote_string(f"{star}{res_path}")

    cf, cfg_path = _load(project_dir)
    old = cf.get("autoload", name, None)
    changed = old != literal
    if changed:
        cf.set("autoload", name, literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "name": name,
        "path": res_path,
        "enabled": not disabled,
    }


def autoload_remove(project_dir: str, name: str) -> dict:
    cf, cfg_path = _load(project_dir)
    removed = cf.unset("autoload", name)
    if removed:
        cf.save(cfg_path)
    return {"status": "ok", "changed": removed, "name": name}


def _autoload_set_enabled(project_dir: str, name: str, enabled: bool) -> dict:
    cf, cfg_path = _load(project_dir)
    raw = cf.get("autoload", name, None)
    if raw is None:
        raise RuntimeError(f"Autoload {name!r} not found")
    inner = variant_fmt.parse_literal(raw)
    if not isinstance(inner, str):
        # Unexpected; fall back to stripping quotes manually.
        inner = raw.strip().strip('"')
    bare = inner[1:] if inner.startswith("*") else inner
    new_inner = ("*" + bare) if enabled else bare
    literal = variant_fmt.quote_string(new_inner)
    changed = literal != raw
    if changed:
        cf.set("autoload", name, literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "name": name,
        "enabled": enabled,
    }


def autoload_enable(project_dir: str, name: str) -> dict:
    return _autoload_set_enabled(project_dir, name, True)


def autoload_disable(project_dir: str, name: str) -> dict:
    return _autoload_set_enabled(project_dir, name, False)


def autoload_list(project_dir: str) -> dict:
    cf, _ = _load(project_dir)
    items: list[dict] = []
    if cf.has_section("autoload"):
        for name, raw in cf.section_items("autoload"):
            inner = variant_fmt.parse_literal(raw)
            if not isinstance(inner, str):
                inner = raw.strip().strip('"')
            enabled = inner.startswith("*")
            items.append({
                "name": name,
                "path": inner[1:] if enabled else inner,
                "enabled": enabled,
            })
    return {"status": "ok", "autoloads": items}


# ─────────────────────────────────────────────────────────────────────────
# input map — the load-bearing encoder
# ─────────────────────────────────────────────────────────────────────────
#
# An action is stored as:
#   action={ "deadzone": <float>, "events": [ <Object(...)>, ... ] }
# where each event is one of:
#   Object(InputEventKey,"...":v,...,"script":null)
#   Object(InputEventMouseButton,...)
#   Object(InputEventJoypadButton,...)
#   Object(InputEventJoypadMotion,...)
# Field ORDER matters for Godot's parser; we reproduce the editor's exact order.

# Key enum values (== uppercase-ASCII for letters; Space=32). physical_keycode
# is layout-independent and preferred. unicode is the codepoint of the lowercase
# character ('a'=97, space=32).
_KEY_NAMES: dict[str, int] = {
    "SPACE": 32,
    "ENTER": 4194309,
    "RETURN": 4194309,
    "ESCAPE": 4194305,
    "ESC": 4194305,
    "TAB": 4194306,
    "BACKSPACE": 4194308,
    "DELETE": 4194312,
    "LEFT": 4194319,
    "RIGHT": 4194321,
    "UP": 4194320,
    "DOWN": 4194322,
    "SHIFT": 4194325,
    "CTRL": 4194326,
    "ALT": 4194328,
}
# Letters A-Z -> 65..90, digits 0-9 -> 48..57 added below.
for _c in range(ord("A"), ord("Z") + 1):
    _KEY_NAMES[chr(_c)] = _c
for _d in range(0, 10):
    _KEY_NAMES[str(_d)] = ord("0") + _d

# Function keys F1..F12 (Key enum 4194332.. for F1).
for _f in range(1, 13):
    _KEY_NAMES[f"F{_f}"] = 4194332 + (_f - 1)


def _key_unicode(keycode: int) -> int:
    """Best-effort unicode codepoint for a printable key (else 0).

    For letters Godot stores the lowercase codepoint; for space it is 32; for
    digits the ASCII digit. Non-printable keys (arrows, F-keys) get 0.
    """
    if keycode == 32:
        return 32
    if ord("A") <= keycode <= ord("Z"):
        return keycode + 32  # lowercase
    if ord("0") <= keycode <= ord("9"):
        return keycode
    return 0


_MOUSE_BUTTONS: dict[str, int] = {
    "left": 1, "l": 1,
    "right": 2, "r": 2,
    "middle": 3, "mid": 3, "m": 3,
    "wheel_up": 4, "wheelup": 4, "up": 4,
    "wheel_down": 5, "wheeldown": 5, "down": 5,
}

# JoyButton enum: 0=A 1=B 2=X 3=Y 4=LeftStick? -> use the canonical SDL layout.
_JOY_BUTTONS: dict[str, int] = {
    "a": 0, "b": 1, "x": 2, "y": 3,
    "back": 4, "guide": 5, "start": 6,
    "ls": 7, "left_stick": 7, "rs": 8, "right_stick": 8,
    "lb": 9, "left_shoulder": 9, "rb": 10, "right_shoulder": 10,
    "dpad_up": 11, "dpad_down": 12, "dpad_left": 13, "dpad_right": 14,
}

# JoyAxis enum: 0=LX 1=LY 2=RX 3=RY 4=LTrig 5=RTrig.
_JOY_AXES: dict[str, int] = {
    "lx": 0, "ly": 1, "rx": 2, "ry": 3,
    "ltrigger": 4, "ltrig": 4, "lt": 4,
    "rtrigger": 5, "rtrig": 5, "rt": 5,
}


def _fmt_float(v: float) -> str:
    """Format a float the way Godot writes it inside the input dict (e.g. 0.5)."""
    return variant_fmt._fmt_number(float(v))


def _encode_key_event(keycode: int, physical: bool) -> str:
    """Serialize an Object(InputEventKey,...) literal.

    physical=True: use physical_keycode (layout-independent), keycode=0.
    physical=False: use keycode (current layout), physical_keycode=0.
    Field order matches the Godot 4.3 editor serializer.
    """
    if physical:
        keycode_field = 0
        physical_field = keycode
    else:
        keycode_field = keycode
        physical_field = 0
    unicode = _key_unicode(keycode)
    return (
        'Object(InputEventKey,'
        '"resource_local_to_scene":false,'
        '"resource_name":"",'
        '"device":-1,'
        '"window_id":0,'
        '"alt_pressed":false,'
        '"shift_pressed":false,'
        '"ctrl_pressed":false,'
        '"meta_pressed":false,'
        '"pressed":false,'
        f'"keycode":{keycode_field},'
        f'"physical_keycode":{physical_field},'
        '"key_label":0,'
        f'"unicode":{unicode},'
        '"location":0,'
        '"echo":false,'
        '"script":null)'
    )


def _encode_mouse_event(button_index: int) -> str:
    return (
        'Object(InputEventMouseButton,'
        '"resource_local_to_scene":false,'
        '"resource_name":"",'
        '"device":-1,'
        '"window_id":0,'
        '"alt_pressed":false,'
        '"shift_pressed":false,'
        '"ctrl_pressed":false,'
        '"meta_pressed":false,'
        '"button_mask":0,'
        '"position":Vector2(0, 0),'
        '"global_position":Vector2(0, 0),'
        '"factor":1.0,'
        f'"button_index":{button_index},'
        '"canceled":false,'
        '"pressed":false,'
        '"double_click":false,'
        '"script":null)'
    )


def _encode_joy_button_event(button_index: int) -> str:
    return (
        'Object(InputEventJoypadButton,'
        '"resource_local_to_scene":false,'
        '"resource_name":"",'
        '"device":-1,'
        f'"button_index":{button_index},'
        '"pressure":0.0,'
        '"pressed":false,'
        '"script":null)'
    )


def _encode_joy_motion_event(axis: int, axis_value: float) -> str:
    return (
        'Object(InputEventJoypadMotion,'
        '"resource_local_to_scene":false,'
        '"resource_name":"",'
        '"device":-1,'
        f'"axis":{axis},'
        f'"axis_value":{_fmt_float(axis_value)},'
        '"script":null)'
    )


def encode_event(event_spec: dict) -> str:
    """Encode a single input event from a parsed spec dict.

    The spec dict has a ``kind`` and kind-specific fields:
      {"kind":"key", "name":"SPACE", "physical":bool}
      {"kind":"mouse", "button":"left"}
      {"kind":"joy_button", "button":"a"}
      {"kind":"joy_axis", "axis":"lx", "value":-1.0}
    """
    kind = event_spec["kind"]
    if kind == "key":
        name = str(event_spec["name"]).upper()
        if name not in _KEY_NAMES:
            raise RuntimeError(
                f"Unknown key name {event_spec['name']!r}. "
                f"Use a letter (A-Z), digit, SPACE, ENTER, arrows, F1-F12, etc."
            )
        return _encode_key_event(_KEY_NAMES[name], bool(event_spec.get("physical")))
    if kind == "mouse":
        b = str(event_spec["button"]).lower()
        if b not in _MOUSE_BUTTONS:
            raise RuntimeError(
                f"Unknown mouse button {event_spec['button']!r}. "
                f"Use left/right/middle/wheel_up/wheel_down."
            )
        return _encode_mouse_event(_MOUSE_BUTTONS[b])
    if kind == "joy_button":
        b = str(event_spec["button"]).lower()
        if b not in _JOY_BUTTONS:
            raise RuntimeError(
                f"Unknown joypad button {event_spec['button']!r}. Use a/b/x/y/lb/rb/..."
            )
        return _encode_joy_button_event(_JOY_BUTTONS[b])
    if kind == "joy_axis":
        ax = str(event_spec["axis"]).lower()
        if ax not in _JOY_AXES:
            raise RuntimeError(
                f"Unknown joypad axis {event_spec['axis']!r}. Use lx/ly/rx/ry/lt/rt."
            )
        value = float(event_spec.get("value", 1.0))
        return _encode_joy_motion_event(_JOY_AXES[ax], value)
    raise RuntimeError(f"Unknown event kind {kind!r}")


def parse_event_spec(
    *,
    key: str | None = None,
    physical_key: str | None = None,
    mouse: str | None = None,
    joy_button: str | None = None,
    joy_axis: str | None = None,
) -> dict:
    """Turn the mutually-exclusive CLI flags into a single event-spec dict.

    Exactly one of the parameters must be provided. ``joy_axis`` accepts the
    ``axis:value`` form (e.g. ``lx:-1``); a bare axis defaults to value 1.0.
    """
    chosen = [
        ("key", key),
        ("physical_key", physical_key),
        ("mouse", mouse),
        ("joy_button", joy_button),
        ("joy_axis", joy_axis),
    ]
    present = [(n, v) for n, v in chosen if v is not None]
    if len(present) != 1:
        raise RuntimeError(
            "Provide exactly one of --key / --physical-key / --mouse / "
            "--joy-button / --joy-axis."
        )
    name, val = present[0]
    if name == "key":
        return {"kind": "key", "name": val, "physical": False}
    if name == "physical_key":
        return {"kind": "key", "name": val, "physical": True}
    if name == "mouse":
        return {"kind": "mouse", "button": val}
    if name == "joy_button":
        return {"kind": "joy_button", "button": val}
    # joy_axis: "lx:-1" or "lx"
    axis_part, _, value_part = str(val).partition(":")
    value = float(value_part) if value_part != "" else 1.0
    return {"kind": "joy_axis", "axis": axis_part, "value": value}


def _build_action_literal(deadzone: float, event_literals: list[str]) -> str:
    """Assemble the ``{ "deadzone": d, "events": [ ... ] }`` action literal.

    Matches the editor's multi-line layout: events comma-separated, each
    ``Object(...)`` after a leading ``, `` on its own line.
    """
    if event_literals:
        events_inner = "\n" + "\n, ".join(event_literals) + "\n"
    else:
        events_inner = ""
    return (
        "{\n"
        f'"deadzone": {_fmt_float(deadzone)},\n'
        f'"events": [{events_inner}]\n'
        "}"
    )


def _split_events(action_raw: str) -> tuple[float, list[str]]:
    """Parse an existing action literal into (deadzone, [event_literal, ...]).

    Returns the raw Object(...) literals so we can append/remove without
    re-encoding. Falls back to deadzone 0.5 / no events if it can't parse.
    """
    deadzone = 0.5
    events: list[str] = []
    parsed = variant_fmt.parse_literal(action_raw)
    # parse_literal returns GDValue for dicts; we parse manually instead.
    text = action_raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return deadzone, events
    inner = text[1:-1]
    # Top-level "key": value pairs.
    for part in variant_fmt._split_top_level(inner):
        k, _, v = part.partition(":")
        k = k.strip().strip('"')
        v = v.strip()
        if k == "deadzone":
            try:
                deadzone = float(v)
            except ValueError:
                pass
        elif k == "events":
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                arr = v[1:-1].strip()
                if arr:
                    for ev in variant_fmt._split_top_level(arr):
                        ev = ev.strip()
                        if ev:
                            events.append(ev)
    return deadzone, events


def input_add(
    project_dir: str,
    action: str,
    event_spec: dict,
    deadzone: float = 0.5,
) -> dict:
    """Create (or replace) an action with a single event."""
    literal = encode_event(event_spec)
    action_literal = _build_action_literal(deadzone, [literal])

    cf, cfg_path = _load(project_dir)
    old = cf.get("input", action, None)
    changed = old != action_literal
    if changed:
        cf.set("input", action, action_literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "action": action,
        "deadzone": deadzone,
        "event_count": 1,
    }


def input_add_event(project_dir: str, action: str, event_spec: dict) -> dict:
    """Append an additional event to an existing action (creating it if absent)."""
    literal = encode_event(event_spec)
    cf, cfg_path = _load(project_dir)
    old = cf.get("input", action, None)
    if old is None:
        deadzone, events = 0.5, []
    else:
        deadzone, events = _split_events(old)
    events.append(literal)
    action_literal = _build_action_literal(deadzone, events)
    changed = action_literal != old
    if changed:
        cf.set("input", action, action_literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "action": action,
        "event_count": len(events),
    }


def input_remove(project_dir: str, action: str, event_index: int | None = None) -> dict:
    """Remove a whole action, or just one event by index."""
    cf, cfg_path = _load(project_dir)
    old = cf.get("input", action, None)
    if old is None:
        raise RuntimeError(f"Input action {action!r} not found")

    if event_index is None:
        cf.unset("input", action)
        cf.save(cfg_path)
        return {"status": "ok", "changed": True, "action": action, "removed": "action"}

    deadzone, events = _split_events(old)
    if event_index < 0 or event_index >= len(events):
        raise RuntimeError(
            f"Event index {event_index} out of range (action has {len(events)} events)"
        )
    del events[event_index]
    action_literal = _build_action_literal(deadzone, events)
    cf.set("input", action, action_literal)
    cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": True,
        "action": action,
        "event_count": len(events),
    }


def _classify_event(ev: str) -> dict:
    """Best-effort description of a raw Object(...) event literal for listing."""
    info: dict[str, Any] = {"raw": ev}
    if "InputEventKey" in ev:
        info["type"] = "key"
    elif "InputEventMouseButton" in ev:
        info["type"] = "mouse_button"
    elif "InputEventJoypadButton" in ev:
        info["type"] = "joy_button"
    elif "InputEventJoypadMotion" in ev:
        info["type"] = "joy_motion"
    else:
        info["type"] = "other"
    return info


def input_list(project_dir: str, action: str | None = None) -> dict:
    """List input actions (or a single action's events)."""
    cf, _ = _load(project_dir)
    if not cf.has_section("input"):
        if action is not None:
            raise RuntimeError(f"Input action {action!r} not found")
        return {"status": "ok", "actions": []}

    if action is not None:
        raw = cf.get("input", action, None)
        if raw is None:
            raise RuntimeError(f"Input action {action!r} not found")
        deadzone, events = _split_events(raw)
        return {
            "status": "ok",
            "action": action,
            "deadzone": deadzone,
            "events": [_classify_event(e) for e in events],
        }

    actions: list[dict] = []
    for name, raw in cf.section_items("input"):
        deadzone, events = _split_events(raw)
        actions.append({
            "action": name,
            "deadzone": deadzone,
            "event_count": len(events),
        })
    return {"status": "ok", "actions": actions}


# ─────────────────────────────────────────────────────────────────────────
# project groups ([global_group], Godot 4.3+)
# ─────────────────────────────────────────────────────────────────────────

def group_add(project_dir: str, name: str, description: str = "") -> dict:
    """Add (or update) a project-wide group in [global_group] (4.3+)."""
    literal = variant_fmt.quote_string(description or "")
    cf, cfg_path = _load(project_dir)
    old = cf.get("global_group", name, None)
    changed = old != literal
    if changed:
        cf.set("global_group", name, literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "name": name,
        "description": description or "",
    }


def group_remove(project_dir: str, name: str) -> dict:
    cf, cfg_path = _load(project_dir)
    removed = cf.unset("global_group", name)
    if removed:
        cf.save(cfg_path)
    return {"status": "ok", "changed": removed, "name": name}


def group_list(project_dir: str) -> dict:
    cf, _ = _load(project_dir)
    groups: list[dict] = []
    if cf.has_section("global_group"):
        for name, raw in cf.section_items("global_group"):
            desc = variant_fmt.parse_literal(raw)
            if not isinstance(desc, str):
                desc = raw.strip().strip('"')
            groups.append({"name": name, "description": desc})
    return {"status": "ok", "groups": groups}


# ─────────────────────────────────────────────────────────────────────────
# layer names ([layer_names])
# ─────────────────────────────────────────────────────────────────────────

_LAYER_SPACES = {
    "2d_physics", "3d_physics",
    "2d_render", "3d_render",
    "2d_navigation", "3d_navigation",
    "avoidance",
}


def layer_name(project_dir: str, space: str, layer_num: int, name: str) -> dict:
    """Set a cosmetic layer name, e.g. ``2d_physics/layer_1="world"``."""
    space = space.lower()
    if space not in _LAYER_SPACES:
        raise RuntimeError(
            f"Unknown layer space {space!r}; expected one of {sorted(_LAYER_SPACES)}"
        )
    if layer_num < 1 or layer_num > 32:
        raise RuntimeError(f"Layer number must be 1..32 (got {layer_num})")

    key = f"{space}/layer_{layer_num}"
    literal = variant_fmt.quote_string(name)
    cf, cfg_path = _load(project_dir)
    old = cf.get("layer_names", key, None)
    changed = old != literal
    if changed:
        cf.set("layer_names", key, literal)
        cf.save(cfg_path)
    return {
        "status": "ok",
        "changed": changed,
        "space": space,
        "layer": layer_num,
        "name": name,
    }
