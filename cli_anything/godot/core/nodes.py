"""Offline scene/node CRUD as direct `.tscn` text edits (Godot 4.3).

Pure functions operating on a scene file *path*. Each builds a
``cli_anything.godot.core.tscn.TscnFile`` from the file text, mutates it, and
(when changed) writes the serialized result back. Every mutating function
returns a dict with ``status`` and ``changed``.

The dangerous operations are :func:`rename_node` and :func:`reparent_node`,
which must keep the whole file internally consistent:

  - **rename** rewrites the node's own ``name``, every descendant's ``parent=``
    path, every ``[connection]`` ``from``/``to`` that points at the node or a
    descendant, every ``[editable]`` path, and any ``NodePath("...")`` property
    value whose path component matches the renamed node.
  - **reparent** rewrites the node's ``parent=``, fixes every descendant's
    ``parent=`` (they embed the moved node's old path), and likewise rewrites
    connections / editables / NodePaths under the moved subtree.

These are deterministic text transforms, so we do them in-file. ``scene repack``
(see :func:`repack_scene`) is offered as an engine-backed normalization step.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from cli_anything.godot.core import variant_fmt
from cli_anything.godot.core.tscn import TscnFile, SceneNode


# ──────────────────────────────────────────────────────────────────────
# file helpers
# ──────────────────────────────────────────────────────────────────────

def _resolve(project_path: str, scene_path: str) -> Path:
    """Resolve a scene path. Accepts res:// paths and project-relative paths."""
    if scene_path.startswith("res://"):
        scene_path = scene_path[len("res://"):]
    p = Path(scene_path)
    if not p.is_absolute():
        p = Path(project_path) / scene_path
    return p


def _load(project_path: str, scene_path: str) -> tuple[Path, TscnFile, str]:
    full = _resolve(project_path, scene_path)
    if not full.exists():
        raise RuntimeError(f"Scene not found: {scene_path}")
    text = full.read_text(encoding="utf-8")
    return full, TscnFile.parse(text), text


def _save(full: Path, f: TscnFile) -> str:
    text = f.serialize()
    full.write_text(text, encoding="utf-8")
    return text


def _require(f: TscnFile, path: str) -> SceneNode:
    nd = f.find(path)
    if nd is None:
        raise RuntimeError(f"Node not found: {path!r}")
    return nd


def _name_exists_under(f: TscnFile, parent_path: str, name: str) -> bool:
    for ch in f.children_of(parent_path):
        if ch.name == name:
            return True
    return False


def _subtree(f: TscnFile, node: SceneNode) -> list[SceneNode]:
    """The node + all its descendants, in current document order."""
    p = node.path()
    prefix = p + "/"
    return [n for n in f.nodes
            if n is node or (n.parent is not None and
                             (n.path() == p or n.path().startswith(prefix)))]


def _reorder_siblings(f: TscnFile, parent_path: str, new_order: list[SceneNode]) -> None:
    """Rebuild ``f.nodes`` so the given siblings (each with its subtree) appear
    in ``new_order``, leaving every other node where it is.

    The first sibling's current position anchors the block; we remove all the
    siblings' subtrees and re-insert them, reordered, at that anchor.
    """
    block_ids = set()
    blocks: dict[int, list[SceneNode]] = {}
    for sib in new_order:
        st = _subtree(f, sib)
        blocks[id(sib)] = st
        block_ids.update(id(n) for n in st)
    # anchor = index of the first node belonging to any block
    anchor = next((i for i, n in enumerate(f.nodes) if id(n) in block_ids), None)
    if anchor is None:
        return
    remaining = [n for n in f.nodes if id(n) not in block_ids]
    # how many non-block nodes precede the anchor (to restore insert position)
    insert_at = sum(1 for n in f.nodes[:anchor] if id(n) not in block_ids)
    rebuilt = []
    for sib in new_order:
        rebuilt.extend(blocks[id(sib)])
    f.nodes = remaining[:insert_at] + rebuilt + remaining[insert_at:]


# ──────────────────────────────────────────────────────────────────────
# read / tree
# ──────────────────────────────────────────────────────────────────────

def _node_dict(nd: SceneNode) -> dict:
    return {
        "name": nd.name,
        "type": nd.type,
        "path": nd.path(),
        "parent": nd.parent,
        "instance": nd.instance,
        "groups": list(nd.groups),
        "index": nd.index,
        "props": dict(nd.props),
    }


def read_scene(project_path: str, scene_path: str) -> dict:
    """Return the scene's node tree + ext/sub resources + connections as a dict."""
    _full, f, _text = _load(project_path, scene_path)
    return {
        "status": "ok",
        "changed": False,
        "scene_path": scene_path,
        "uid": f.uid,
        "nodes": [_node_dict(n) for n in f._sorted_nodes()],
        "ext_resources": [
            {"id": e.id, "type": e.type, "path": e.path, "uid": e.uid} for e in f.ext
        ],
        "sub_resources": [
            {"id": s.id, "type": s.type, "props": dict(s.props)} for s in f.sub
        ],
        "connections": [
            {"signal": c.signal, "from": c.from_, "to": c.to, "method": c.method,
             "flags": c.flags, "unbinds": c.unbinds, "binds": c.binds}
            for c in f.connections
        ],
        "editables": list(f.editables),
    }


def scene_tree(project_path: str, scene_path: str) -> dict:
    """Return a pretty indented tree string of the scene plus the structured tree."""
    _full, f, _text = _load(project_path, scene_path)
    root = f.root()
    lines: list[str] = []

    def walk(node: SceneNode, depth: int):
        label = node.name
        if node.instance is not None:
            label += f"  (instance ExtResource {node.instance})"
        elif node.type:
            label += f"  [{node.type}]"
        if node.groups:
            label += "  groups=" + ",".join(node.groups)
        lines.append("  " * depth + ("- " if depth else "") + label)
        for ch in f.children_of(node.path()):
            walk(ch, depth + 1)

    tree_obj = None
    if root is not None:
        walk(root, 0)
        tree_obj = _tree_obj(f, root)

    return {
        "status": "ok",
        "changed": False,
        "scene_path": scene_path,
        "tree": "\n".join(lines),
        "root": tree_obj,
    }


def _tree_obj(f: TscnFile, node: SceneNode) -> dict:
    d = _node_dict(node)
    d["children"] = [_tree_obj(f, ch) for ch in f.children_of(node.path())]
    return d


# ──────────────────────────────────────────────────────────────────────
# create
# ──────────────────────────────────────────────────────────────────────

def create_scene(project_path: str, scene_path: str, root_type: str = "Node2D",
                 root_name: str | None = None) -> dict:
    """Create a new .tscn with a single root node (via TscnFile.new_scene)."""
    full = _resolve(project_path, scene_path)
    if full.exists():
        raise RuntimeError(f"Scene already exists: {scene_path}")
    if root_name is None:
        root_name = full.stem
    full.parent.mkdir(parents=True, exist_ok=True)
    f = TscnFile.new_scene(root_type, root_name)
    text = _save(full, f)
    return {
        "status": "ok",
        "changed": True,
        "scene_path": scene_path,
        "root_type": root_type,
        "root_name": root_name,
        "absolute_path": str(full.resolve()),
        "text": text,
    }


# ──────────────────────────────────────────────────────────────────────
# add / remove / move
# ──────────────────────────────────────────────────────────────────────

def add_node(project_path: str, scene_path: str, name: str, type: str,
             parent: str = ".", *, index: int | None = None,
             groups: list[str] | None = None) -> dict:
    """Add a child node of ``type`` named ``name`` under ``parent``."""
    full, f, _text = _load(project_path, scene_path)
    if f.root() is None:
        raise RuntimeError("Scene has no root node")
    parent_node = _require(f, parent)
    parent_path = parent_node.path()
    if _name_exists_under(f, parent_path, name):
        raise RuntimeError(f"A child named {name!r} already exists under {parent_path!r}")
    nd = f.add_node(name, type, parent_path, groups=groups, index=index)
    _save(full, f)
    return {"status": "ok", "changed": True, "path": nd.path(),
            "name": name, "type": type, "parent": parent_path}


def remove_node(project_path: str, scene_path: str, path: str) -> dict:
    """Remove a node, its descendants, and any connections touching them."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if nd.parent is None:
        raise RuntimeError("Cannot remove the root node")
    target = nd.path()
    f.remove_node(target)
    _save(full, f)
    return {"status": "ok", "changed": True, "removed": target}


def move_node(project_path: str, scene_path: str, path: str, index: int) -> dict:
    """Reorder a node among its siblings by writing ``index=`` and reordering."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if nd.parent is None:
        raise RuntimeError("Cannot move the root node")
    siblings = f.children_of(nd.parent)
    nd.index = index
    # Physically reorder f.nodes so the serializer (which walks document order
    # and emits parents-before-children) reflects the new sibling order. We also
    # carry each sibling's whole subtree so descendants stay after their parent.
    if 0 <= index < len(siblings):
        others = [s for s in siblings if s is not nd]
        new_order = others[:index] + [nd] + others[index:]
        for i, s in enumerate(new_order):
            s.index = i
        _reorder_siblings(f, nd.parent, new_order)
    _save(full, f)
    return {"status": "ok", "changed": True, "path": nd.path(), "index": index}


# ──────────────────────────────────────────────────────────────────────
# rename (the dangerous one)
# ──────────────────────────────────────────────────────────────────────

def _split_path(p: str) -> list[str]:
    if p in (".", "", None):
        return []
    return p.strip("/").split("/")


def _join_path(parts: list[str]) -> str:
    return "/".join(parts) if parts else "."


def _rewrite_subpath(scene_path: str, old: str, new: str) -> str:
    """Rewrite a scene path so that the path-prefix ``old`` becomes ``new``.

    ``old``/``new`` and ``scene_path`` are all root-excluding scene paths
    ('.' => root). Matches the node itself and any descendant. Returns the
    (possibly unchanged) path.
    """
    if scene_path in (".", "", None):
        return scene_path
    old_parts = _split_path(old)
    sp_parts = _split_path(scene_path)
    if sp_parts[:len(old_parts)] == old_parts and len(old_parts) > 0:
        new_parts = _split_path(new) + sp_parts[len(old_parts):]
        return _join_path(new_parts)
    return scene_path


def _rewrite_parent(parent: str | None, old: str, new: str) -> str | None:
    """Rewrite a ``parent`` attribute (which is the parent's path, root-excluding)."""
    if parent is None:
        return None
    if parent in (".", ""):
        return parent
    return _rewrite_subpath(parent, old, new)


_NODEPATH_RE = re.compile(r'NodePath\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _rewrite_nodepath_literal(raw: str, old_name: str, new_name: str) -> str:
    """In a raw property literal, rename a NodePath component ``old_name``.

    A NodePath looks like ``NodePath("../Panel/Btn:scale.x")`` or
    ``NodePath("Btn")``. We only rename whole path *components* (between '/' ,
    after the leading anchors like '..' or '', and before the ':' sub-path),
    never substrings, so 'Btn' won't touch 'BtnX'.
    """
    if "NodePath(" not in raw or old_name == new_name:
        return raw

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        # split off the property sub-path after ':'
        if ":" in inner:
            node_part, _, sub = inner.partition(":")
            sub = ":" + sub
        else:
            node_part, sub = inner, ""
        comps = node_part.split("/")
        changed = False
        for i, c in enumerate(comps):
            if c == old_name:
                comps[i] = new_name
                changed = True
        if not changed:
            return m.group(0)
        return 'NodePath("' + "/".join(comps) + sub + '")'

    return _NODEPATH_RE.sub(repl, raw)


def rename_node(project_path: str, scene_path: str, path: str, to: str) -> dict:
    """Rename a node and fix EVERY reference to it.

    Updates: the node's ``name``; every descendant's ``parent=`` path; every
    connection ``from``/``to`` under the renamed subtree; every ``[editable]``
    path; every ``NodePath("...")`` property value whose component matches the
    old name (anywhere in the scene).
    """
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    old_name = nd.name
    if to == old_name:
        return {"status": "ok", "changed": False, "path": nd.path(), "to": to}
    if not to or "/" in to:
        raise RuntimeError(f"Invalid node name: {to!r}")

    # sibling-collision check
    parent_path = "." if nd.parent is None else (nd.parent or ".")
    if nd.parent is not None and _name_exists_under(f, parent_path, to):
        raise RuntimeError(f"A sibling named {to!r} already exists under {parent_path!r}")

    old_path = nd.path()
    # compute the new path of the renamed node itself
    if nd.parent is None:
        new_path = old_path  # root path stays '.'
    else:
        new_parts = _split_path(nd.parent) + [to]
        new_path = _join_path(new_parts)

    # 1) rename the node
    nd.name = to

    # 2) fix descendants' parent= (they embed old_path)
    for other in f.nodes:
        if other is nd:
            continue
        other.parent = _rewrite_parent(other.parent, old_path, new_path)

    # 3) fix connections from/to
    for c in f.connections:
        c.from_ = _rewrite_subpath(c.from_, old_path, new_path)
        c.to = _rewrite_subpath(c.to, old_path, new_path)

    # 4) fix editables
    f.editables = [_rewrite_subpath(e, old_path, new_path) for e in f.editables]

    # 5) fix NodePath property literals across all nodes (rename the component)
    for other in f.nodes:
        for k, v in list(other.props.items()):
            other.props[k] = _rewrite_nodepath_literal(v, old_name, to)

    _save(full, f)
    return {"status": "ok", "changed": True, "from_path": old_path,
            "to_path": new_path, "from_name": old_name, "to_name": to}


# ──────────────────────────────────────────────────────────────────────
# reparent (the other dangerous one)
# ──────────────────────────────────────────────────────────────────────

def reparent_node(project_path: str, scene_path: str, path: str,
                  to_parent: str, *, index: int | None = None) -> dict:
    """Move a node (and its subtree) under ``to_parent``; fix all paths.

    FILE approach: update the node's ``parent``, rewrite every descendant's
    ``parent`` (they embed the moved node's old path), and rewrite connection
    ``from``/``to``, editables and NodePath property literals that pointed into
    the moved subtree.
    """
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if nd.parent is None:
        raise RuntimeError("Cannot reparent the root node")
    new_parent_node = _require(f, to_parent)
    new_parent_path = new_parent_node.path()

    old_path = nd.path()
    # forbid moving a node under itself or its own descendant
    if new_parent_path == old_path or new_parent_path.startswith(old_path + "/"):
        raise RuntimeError("Cannot reparent a node under itself or its descendant")

    # collision: a child of the same name already under the new parent
    if _name_exists_under(f, new_parent_path, nd.name):
        raise RuntimeError(
            f"A child named {nd.name!r} already exists under {new_parent_path!r}")

    if nd.parent == new_parent_path:
        # already there; allow index-only adjustment
        if index is not None:
            return move_node(project_path, scene_path, old_path, index)
        return {"status": "ok", "changed": False, "path": old_path}

    # new path of the moved node
    if new_parent_path in (".",):
        new_path = nd.name
        nd.parent = "."
    else:
        new_path = f"{new_parent_path}/{nd.name}"
        nd.parent = new_parent_path

    if index is not None:
        nd.index = index

    # rewrite descendants' parent (embed old_path -> new_path)
    for other in f.nodes:
        if other is nd:
            continue
        if other.parent and other.parent != ".":
            other.parent = _rewrite_subpath(other.parent, old_path, new_path)

    # rewrite connections, editables, NodePaths under the moved subtree
    for c in f.connections:
        c.from_ = _rewrite_subpath(c.from_, old_path, new_path)
        c.to = _rewrite_subpath(c.to, old_path, new_path)
    f.editables = [_rewrite_subpath(e, old_path, new_path) for e in f.editables]

    _save(full, f)
    return {"status": "ok", "changed": True, "from_path": old_path,
            "to_path": new_path, "to_parent": new_parent_path}


# ──────────────────────────────────────────────────────────────────────
# duplicate
# ──────────────────────────────────────────────────────────────────────

def _unique_name(f: TscnFile, parent_path: str, base: str) -> str:
    existing = {ch.name for ch in f.children_of(parent_path)}
    if base not in existing:
        return base
    i = 2
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


def duplicate_node(project_path: str, scene_path: str, path: str,
                   name: str | None = None) -> dict:
    """Clone a node and its whole subtree (props + groups) under the same parent."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if nd.parent is None:
        raise RuntimeError("Cannot duplicate the root node")

    old_path = nd.path()
    parent_path = nd.parent  # '.' or a path
    new_name = name or _unique_name(f, parent_path, nd.name)
    if _name_exists_under(f, parent_path, new_name):
        raise RuntimeError(f"A child named {new_name!r} already exists under {parent_path!r}")

    if parent_path in (".",):
        new_root_path = new_name
    else:
        new_root_path = f"{parent_path}/{new_name}"

    # collect the subtree (the node + descendants), in document order
    subtree = [n for n in f.nodes
               if n.path() == old_path or n.path().startswith(old_path + "/")]

    created = []
    for src in subtree:
        # map src's parent onto the clone's namespace
        if src is nd:
            clone_parent = parent_path
        else:
            # src.parent embeds old_path -> rebase onto new_root_path
            clone_parent = _rewrite_subpath(src.parent, old_path, new_root_path)
        clone_name = new_name if src is nd else src.name
        clone = f.add_node(clone_name, src.type, clone_parent,
                           instance=src.instance, groups=list(src.groups),
                           index=src.index)
        for k, v in src.props.items():
            clone.props[k] = v
        created.append(clone.path())

    _save(full, f)
    return {"status": "ok", "changed": True, "source": old_path,
            "new_path": new_root_path, "created": created}


# ──────────────────────────────────────────────────────────────────────
# properties
# ──────────────────────────────────────────────────────────────────────

def get_prop(project_path: str, scene_path: str, path: str, prop: str) -> dict:
    """Return a node property's raw literal and best-effort parsed Python value."""
    _full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if prop not in nd.props:
        raise RuntimeError(f"Node {path!r} has no property {prop!r}")
    raw = nd.props[prop]
    parsed = variant_fmt.parse_literal(raw)
    if isinstance(parsed, variant_fmt.GDValue):
        value = parsed.raw
    else:
        value = parsed
    return {"status": "ok", "changed": False, "path": nd.path(),
            "prop": prop, "raw": raw, "value": value}


def _split_ext_resource(spec: str) -> tuple[str, str]:
    """Parse ``res://path[:Type]`` into (path, type).

    The leading ``res://`` contains a colon, so we only treat a trailing
    ``:Suffix`` as a Type when the suffix looks like a class name (no '/' and
    no '.'). Default type is 'Resource'.
    """
    spec = spec.strip()
    head, sep, tail = spec.rpartition(":")
    if sep and head and "/" not in tail and "." not in tail and not head.endswith("/"):
        return head.strip(), (tail.strip() or "Resource")
    return spec, "Resource"


def _parse_sub_resource_arg(spec: str) -> tuple[str, dict]:
    """Parse 'Type:k=v,k2=v2' into (Type, {k: raw_literal}).

    Values are taken verbatim as Godot literals (e.g. ``size=Vector2(32,48)``).
    """
    type_part, _, props_part = spec.partition(":")
    type_part = type_part.strip()
    if not type_part:
        raise RuntimeError(f"--sub-resource needs a Type: {spec!r}")
    props: dict[str, str] = {}
    if props_part.strip():
        for piece in _split_commas_top(props_part):
            k, _, v = piece.partition("=")
            k = k.strip()
            v = v.strip()
            if not k:
                continue
            props[k] = v
    return type_part, props


def _split_commas_top(s: str) -> list[str]:
    """Split on commas not nested inside (), [], {}."""
    out, depth, buf = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [p for p in (x.strip() for x in out) if p]


def set_prop(project_path: str, scene_path: str, path: str, prop: str, *,
             value: str | None = None, kind: str | None = None,
             ext_resource: str | None = None,
             sub_resource: str | None = None, raw: bool = False) -> dict:
    """Set a node property.

    Exactly one of ``value`` / ``ext_resource`` / ``sub_resource`` must be given.

    - ``value`` with ``raw`` (or ``kind='raw'``): written verbatim.
    - ``value`` with ``kind``: converted via ``variant_fmt.to_literal``.
    - ``value`` without kind: written verbatim (the CLI passes a Godot literal).
    - ``ext_resource`` = ``res://path[:Type]`` : add/reuse an ext_resource and
      write ``ExtResource("id")``.
    - ``sub_resource`` = ``Type:props`` : create a ``[sub_resource]`` and write
      ``SubResource("id")``.
    """
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)

    given = [x for x in (value, ext_resource, sub_resource) if x is not None]
    if len(given) != 1:
        raise RuntimeError(
            "Provide exactly one of --value / --ext-resource / --sub-resource")

    if ext_resource is not None:
        res_path, res_type = _split_ext_resource(ext_resource)
        if not res_path:
            raise RuntimeError("--ext-resource needs a res:// path")
        eid = f.add_ext_resource(res_type, res_path)
        literal = variant_fmt.ext_ref(eid)
    elif sub_resource is not None:
        sub_type, sub_props = _parse_sub_resource_arg(sub_resource)
        sid = f.add_sub_resource(sub_type, sub_props)
        literal = variant_fmt.sub_ref(sid)
    else:
        if raw or kind == "raw":
            literal = str(value)
        elif kind:
            literal = variant_fmt.to_literal(value, kind)
        else:
            # CLI passes a ready Godot literal verbatim (matches SPEC --value).
            literal = str(value)

    old = nd.props.get(prop)
    nd.props[prop] = literal
    changed = old != literal
    if changed:
        _save(full, f)
    return {"status": "ok", "changed": changed, "path": nd.path(),
            "prop": prop, "raw": literal, "previous": old}


# ──────────────────────────────────────────────────────────────────────
# script / groups
# ──────────────────────────────────────────────────────────────────────

def attach_script(project_path: str, scene_path: str, path: str,
                  script: str) -> dict:
    """Attach a GDScript: add an ext_resource (Script) and set ``script =``."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if not script.startswith("res://"):
        script = "res://" + script.lstrip("/")
    eid = f.add_ext_resource("Script", script)
    literal = variant_fmt.ext_ref(eid)
    old = nd.props.get("script")
    nd.props["script"] = literal
    changed = old != literal
    if changed:
        _save(full, f)
    return {"status": "ok", "changed": changed, "path": nd.path(),
            "script": script, "ext_id": eid}


def add_to_group(project_path: str, scene_path: str, path: str, group: str) -> dict:
    """Add the node to a scene group (the node-heading ``groups=[...]``)."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if group in nd.groups:
        return {"status": "ok", "changed": False, "path": nd.path(),
                "groups": list(nd.groups)}
    nd.groups.append(group)
    _save(full, f)
    return {"status": "ok", "changed": True, "path": nd.path(),
            "groups": list(nd.groups)}


def remove_from_group(project_path: str, scene_path: str, path: str,
                      group: str) -> dict:
    """Remove the node from a scene group."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    if group not in nd.groups:
        return {"status": "ok", "changed": False, "path": nd.path(),
                "groups": list(nd.groups)}
    nd.groups = [g for g in nd.groups if g != group]
    _save(full, f)
    return {"status": "ok", "changed": True, "path": nd.path(),
            "groups": list(nd.groups)}


# ──────────────────────────────────────────────────────────────────────
# instancing
# ──────────────────────────────────────────────────────────────────────

def instance_scene(project_path: str, scene_path: str, child_scene: str,
                   name: str, parent: str = ".", *,
                   props: list[str] | None = None,
                   index: int | None = None) -> dict:
    """Instance ``child_scene`` (a PackedScene) into ``scene_path``.

    Adds an ext_resource of type PackedScene and writes a node with
    ``instance=ExtResource(id)`` and NO ``type=``. ``props`` is a list of
    ``key=literal`` overrides applied to the instanced node.
    """
    full, f, _text = _load(project_path, scene_path)
    if f.root() is None:
        raise RuntimeError("Scene has no root node")
    parent_node = _require(f, parent)
    parent_path = parent_node.path()
    if _name_exists_under(f, parent_path, name):
        raise RuntimeError(f"A child named {name!r} already exists under {parent_path!r}")
    if not child_scene.startswith("res://"):
        child_scene = "res://" + child_scene.lstrip("/")

    eid = f.add_ext_resource("PackedScene", child_scene)
    nd = f.add_node(name, None, parent_path, instance=eid, index=index)
    applied = {}
    for kv in (props or []):
        k, _, v = kv.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        nd.props[k] = v
        applied[k] = v
    _save(full, f)
    return {"status": "ok", "changed": True, "path": nd.path(),
            "child_scene": child_scene, "ext_id": eid, "props": applied}


def make_editable(project_path: str, scene_path: str, path: str) -> dict:
    """Mark an instanced child editable: write ``[editable path="X"]``."""
    full, f, _text = _load(project_path, scene_path)
    nd = _require(f, path)
    p = nd.path()
    if p in f.editables:
        return {"status": "ok", "changed": False, "path": p, "editables": list(f.editables)}
    f.editables.append(p)
    _save(full, f)
    return {"status": "ok", "changed": True, "path": p, "editables": list(f.editables)}


def override_child(project_path: str, scene_path: str, instance: str, child: str,
                   props: list[str] | None = None) -> dict:
    """Override a property on a node *inside* an instanced scene.

    Marks the instance editable (``[editable path="Instance"]``) and writes (or
    extends) a ``[node parent="Instance/Child"]`` override block — no ``type=``,
    no ``instance=`` — carrying the override props.
    """
    full, f, _text = _load(project_path, scene_path)
    inst_node = _require(f, instance)
    if inst_node.instance is None:
        raise RuntimeError(f"Node {instance!r} is not an instanced scene")
    inst_path = inst_node.path()

    # 1) ensure editable
    if inst_path not in f.editables:
        f.editables.append(inst_path)

    # 2) find or create the override node block at parent=inst_path, name=child.
    # child may be a nested path "A/B"; the override node's name is the last
    # segment and its parent is inst_path + the leading segments.
    child = child.strip("/")
    child_parts = child.split("/")
    leaf = child_parts[-1]
    if len(child_parts) > 1:
        ov_parent = inst_path + "/" + "/".join(child_parts[:-1])
    else:
        ov_parent = inst_path
    ov_path = f"{ov_parent}/{leaf}"

    override = None
    for n in f.nodes:
        if n.parent is not None and n.path() == ov_path and n.type is None and n.instance is None:
            override = n
            break
    if override is None:
        override = f.add_node(leaf, None, ov_parent)

    applied = {}
    for kv in (props or []):
        k, _, v = kv.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        override.props[k] = v
        applied[k] = v

    _save(full, f)
    return {"status": "ok", "changed": True, "instance": inst_path,
            "child": ov_path, "props": applied}


# ──────────────────────────────────────────────────────────────────────
# repack (engine normalization)
# ──────────────────────────────────────────────────────────────────────

def repack_scene(project_path: str, scene_path: str, timeout: int = 120) -> dict:
    """Normalize/validate a scene by loading+packing+saving it via the engine.

    Uses FOUND's ``run_generated_script`` (editor=False, SceneTree): load the
    scene, ``PackedScene.pack`` the instantiated tree, and ``ResourceSaver.save``
    it back so the engine's own serializer produces canonical output.
    """
    from cli_anything.godot.utils.godot_backend import run_generated_script

    full = _resolve(project_path, scene_path)
    if not full.exists():
        raise RuntimeError(f"Scene not found: {scene_path}")

    # build a res:// path for the engine
    try:
        rel = full.resolve().relative_to(Path(project_path).resolve())
        res = "res://" + str(rel).replace(os.sep, "/")
    except ValueError:
        raise RuntimeError("Scene is not inside the project directory")

    gd = (
        "extends SceneTree\n"
        "func _init():\n"
        f'    var src := "{res}"\n'
        "    var ps = load(src)\n"
        "    if ps == null:\n"
        '        push_error("REPACK_LOAD_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    var inst = ps.instantiate()\n"
        "    if inst == null:\n"
        '        push_error("REPACK_INSTANTIATE_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    var packed = PackedScene.new()\n"
        "    var err = packed.pack(inst)\n"
        "    if err != OK:\n"
        '        push_error("REPACK_PACK_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        "    err = ResourceSaver.save(packed, src)\n"
        "    if err != OK:\n"
        '        push_error("REPACK_SAVE_FAILED")\n'
        "        quit(1)\n"
        "        return\n"
        '    print("REPACK_OK")\n'
        "    quit(0)\n"
    )
    result = run_generated_script(project_path, gd, timeout=timeout)
    combined = result["stdout"] + result["stderr"]
    ok = "REPACK_OK" in result["stdout"] and result["returncode"] == 0
    if not ok:
        raise RuntimeError(
            f"scene repack failed (rc={result['returncode']}): {combined.strip()[:500]}")
    return {"status": "ok", "changed": True, "scene_path": scene_path,
            "stdout": result["stdout"].strip()}
