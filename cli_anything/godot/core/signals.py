"""Offline signal connections as direct `.tscn` `[connection]` edits (Godot 4.3).

A ``[connection]`` line is::

    [connection signal="pressed" from="Btn" to="." method="_on_pressed"]
    [connection signal="timeout" from="Timer" to="." method="_on_timeout" flags=3 unbinds=1 binds=[42]]

Rules (SPEC-offline §B.2):
  - ``flags`` is only written when it differs from the default PERSIST(2).
  - ``unbinds`` is only written when > 0.
  - ``binds`` is a Godot array literal of extra bound args.
"""

from __future__ import annotations

from pathlib import Path

from cli_anything.godot.core.tscn import TscnFile, Connection


PERSIST = 2  # default connection flag


def _resolve(project_path: str, scene_path: str) -> Path:
    if scene_path.startswith("res://"):
        scene_path = scene_path[len("res://"):]
    p = Path(scene_path)
    if not p.is_absolute():
        p = Path(project_path) / scene_path
    return p


def _load(project_path: str, scene_path: str) -> tuple[Path, TscnFile]:
    full = _resolve(project_path, scene_path)
    if not full.exists():
        raise RuntimeError(f"Scene not found: {scene_path}")
    return full, TscnFile.parse(full.read_text(encoding="utf-8"))


def _save(full: Path, f: TscnFile) -> None:
    full.write_text(f.serialize(), encoding="utf-8")


def _conn_dict(c: Connection) -> dict:
    return {"signal": c.signal, "from": c.from_, "to": c.to, "method": c.method,
            "flags": c.flags, "unbinds": c.unbinds, "binds": c.binds}


def connect(project_path: str, scene_path: str, signal: str, from_: str, to: str,
            method: str, *, flags: int | None = None, unbinds: int | None = None,
            binds: str | None = None) -> dict:
    """Add a ``[connection]``. Idempotent on (signal, from, to, method).

    ``flags`` is stored only when != PERSIST(2); ``unbinds`` only when > 0;
    ``binds`` is a raw Godot array literal (e.g. ``"[42]"``).
    """
    full, f = _load(project_path, scene_path)
    if f.find(from_) is None:
        raise RuntimeError(f"Emitter node not found: {from_!r}")
    if f.find(to) is None:
        raise RuntimeError(f"Receiver node not found: {to!r}")

    # normalize flags/unbinds per the "write only when non-default" rule
    store_flags = flags if (flags is not None and flags != PERSIST) else None
    store_unbinds = unbinds if (unbinds is not None and unbinds > 0) else None
    store_binds = binds if binds else None

    for c in f.connections:
        if (c.signal == signal and c.from_ == from_ and c.to == to
                and c.method == method):
            # already present; update flags/binds if they differ
            changed = (c.flags != store_flags or c.unbinds != store_unbinds
                       or c.binds != store_binds)
            c.flags, c.unbinds, c.binds = store_flags, store_unbinds, store_binds
            if changed:
                _save(full, f)
            return {"status": "ok", "changed": changed, "connection": _conn_dict(c)}

    c = Connection(signal=signal, from_=from_, to=to, method=method,
                   flags=store_flags, unbinds=store_unbinds, binds=store_binds)
    f.connections.append(c)
    _save(full, f)
    return {"status": "ok", "changed": True, "connection": _conn_dict(c)}


def disconnect(project_path: str, scene_path: str, *, signal: str | None = None,
               from_: str | None = None, to: str | None = None,
               method: str | None = None, index: int | None = None) -> dict:
    """Remove a connection by field match or by ``index``."""
    full, f = _load(project_path, scene_path)

    if index is not None:
        if index < 0 or index >= len(f.connections):
            raise RuntimeError(f"Connection index out of range: {index}")
        removed = f.connections.pop(index)
        _save(full, f)
        return {"status": "ok", "changed": True, "removed": _conn_dict(removed)}

    matches = []
    for c in f.connections:
        if signal is not None and c.signal != signal:
            continue
        if from_ is not None and c.from_ != from_:
            continue
        if to is not None and c.to != to:
            continue
        if method is not None and c.method != method:
            continue
        matches.append(c)

    if not matches:
        return {"status": "ok", "changed": False, "removed": []}
    f.connections = [c for c in f.connections if c not in matches]
    _save(full, f)
    return {"status": "ok", "changed": True,
            "removed": [_conn_dict(c) for c in matches]}


def list_signals(project_path: str, scene_path: str,
                 from_: str | None = None) -> dict:
    """List outgoing connections (optionally filtered by emitter ``from_``)."""
    _full, f = _load(project_path, scene_path)
    conns = f.connections
    if from_ is not None:
        conns = [c for c in conns if c.from_ == from_]
    return {
        "status": "ok",
        "changed": False,
        "scene_path": scene_path,
        "connections": [
            dict(index=i, **_conn_dict(c))
            for i, c in enumerate(f.connections)
            if from_ is None or c.from_ == from_
        ],
    }
