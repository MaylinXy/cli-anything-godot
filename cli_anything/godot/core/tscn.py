"""Parse and serialize Godot `.tscn` scenes and `.tres` resources (4.3 format=3).

This is the load-bearing file-format module. See SPEC-offline §B.2/§B.3 for the
exact format. Key invariants enforced by ``TscnFile.serialize``:

  - heading attributes are ``attr=val`` with NO spaces around ``=``.
  - property lines are ``key = value`` WITH spaces.
  - section order: header -> ext_resource -> sub_resource -> node -> connection
    -> editable.
  - ``load_steps = (#ext + #sub) + 1``; omitted entirely when zero.
  - the root node has NO ``parent`` attribute; children use ``parent="."`` or a
    relative path that EXCLUDES the root's own name; parents emitted before
    children.
  - an instanced node has ``instance=ExtResource("id")`` and NO ``type=``.
  - ext_resource ids and sub_resource ids are SEPARATE namespaces.

IDs are deterministic (no RNG/time): a 5-char base36 suffix derived from a
per-file counter mixed with a content hash, so re-runs are stable.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from cli_anything.godot.core.variant_fmt import quote_string


# ---------- deterministic id suffix ----------

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int, width: int = 5) -> str:
    n = abs(int(n))
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n % 36])
        n //= 36
    return "".join(reversed(out))


def _rand5(seed: str) -> str:
    """Deterministic 5-char lowercase-alnum suffix from a seed string.

    Uses a stable FNV-1a-style hash (NOT Python's salted hash) so output is
    reproducible across processes/runs.
    """
    h = 2166136261
    for ch in seed:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return _base36(h, 5)


# ---------- data classes ----------

class ExtResource:
    """An ``[ext_resource]`` entry."""

    def __init__(self, type: str, path: str, id: str, uid: str | None = None):
        self.type = type
        self.path = path
        self.id = id
        self.uid = uid

    def __repr__(self):
        return f"ExtResource(type={self.type!r}, path={self.path!r}, id={self.id!r})"


class SubResource:
    """A ``[sub_resource]`` entry. ``props`` holds raw literal values."""

    def __init__(self, type: str, id: str, props: "OrderedDict[str,str] | None" = None):
        self.type = type
        self.id = id
        self.props: "OrderedDict[str,str]" = OrderedDict(props or {})

    def __repr__(self):
        return f"SubResource(type={self.type!r}, id={self.id!r})"


class SceneNode:
    """A ``[node]`` entry.

    parent: None => root; '.' => direct child of root; else a path EXCLUDING the
    root's own name. ``props`` holds raw literal values (the text after ``=``).
    ``instance`` is the ExtResource id when the node is an instanced PackedScene
    (in which case ``type`` is None).
    """

    def __init__(self, name: str, type: str | None, parent: str | None,
                 instance: str | None = None, groups: list[str] | None = None,
                 index: int | None = None, props: "OrderedDict[str,str] | None" = None):
        self.name = name
        self.type = type
        self.parent = parent
        self.instance = instance
        self.groups = list(groups or [])
        self.index = index
        self.props: "OrderedDict[str,str]" = OrderedDict(props or {})

    def path(self) -> str:
        """Full scene path of this node EXCLUDING the root name.

        Root => '.'. Direct child 'X' (parent '.') => 'X'. Child 'Y' of 'X' => 'X/Y'.
        """
        if self.parent is None:
            return "."
        if self.parent in (".", ""):
            return self.name
        return f"{self.parent}/{self.name}"

    def __repr__(self):
        return f"SceneNode(name={self.name!r}, type={self.type!r}, parent={self.parent!r})"


class Connection:
    """A ``[connection]`` entry."""

    def __init__(self, signal: str, from_: str, to: str, method: str,
                 flags: int | None = None, unbinds: int | None = None,
                 binds: str | None = None):
        self.signal = signal
        self.from_ = from_
        self.to = to
        self.method = method
        self.flags = flags
        self.unbinds = unbinds
        self.binds = binds

    def __repr__(self):
        return (f"Connection(signal={self.signal!r}, from={self.from_!r}, "
                f"to={self.to!r}, method={self.method!r})")


# ---------- the main file ----------

class TscnFile:
    """In-memory model of a `.tscn` scene or `.tres` resource."""

    def __init__(self, kind: str = "scene"):
        self.kind = kind  # 'scene' or 'resource'
        self.fmt: int = 3
        self.uid: str | None = None
        self.resource_type: str | None = None  # for .tres header
        self.script_class: str | None = None    # for .tres header
        self.ext: list[ExtResource] = []
        self.sub: list[SubResource] = []
        self.nodes: list[SceneNode] = []
        self.connections: list[Connection] = []
        self.editables: list[str] = []
        self.resource_props: "OrderedDict[str,str]" = OrderedDict()
        # per-file monotonic counter for deterministic id generation
        self._counter: int = 0

    # ---------- factories ----------

    @classmethod
    def new_scene(cls, root_type: str, root_name: str | None = None) -> "TscnFile":
        f = cls(kind="scene")
        name = root_name or root_type
        f.nodes.append(SceneNode(name=name, type=root_type, parent=None))
        return f

    @classmethod
    def new_resource(cls, resource_type: str, script_path: str | None = None,
                     script_class: str | None = None) -> "TscnFile":
        f = cls(kind="resource")
        f.resource_type = resource_type
        if script_path:
            sid = f.add_ext_resource("Script", script_path)
            f.resource_props["script"] = f'ExtResource("{sid}")'
        if script_class:
            f.script_class = script_class
        return f

    # ---------- resource table ----------

    def add_ext_resource(self, type: str, path: str, uid: str | None = None) -> str:
        """Add (or reuse) an ext_resource; return its id like '2_ab3kd'.

        Dedup is by (type, path). ext ids are '<n>_<rand5>' where n is the
        1-based add order.
        """
        for e in self.ext:
            if e.type == type and e.path == path:
                if uid and not e.uid:
                    e.uid = uid
                return e.id
        n = len(self.ext) + 1
        self._counter += 1
        suffix = _rand5(f"ext:{type}:{path}:{self._counter}")
        eid = f"{n}_{suffix}"
        self.ext.append(ExtResource(type=type, path=path, id=eid, uid=uid))
        return eid

    def add_sub_resource(self, type: str, props: dict | None = None) -> str:
        """Add a sub_resource; return its id like 'RectangleShape2D_a1b2c'."""
        self._counter += 1
        seed = f"sub:{type}:{self._counter}:{sorted((props or {}).items())}"
        suffix = _rand5(seed)
        sid = f"{type}_{suffix}"
        self.sub.append(SubResource(type=type, id=sid, props=OrderedDict(props or {})))
        return sid

    def get_ext(self, id_: str) -> ExtResource | None:
        for e in self.ext:
            if e.id == id_:
                return e
        return None

    def get_sub(self, id_: str) -> SubResource | None:
        for s in self.sub:
            if s.id == id_:
                return s
        return None

    # ---------- node helpers ----------

    def root(self) -> SceneNode | None:
        for nd in self.nodes:
            if nd.parent is None:
                return nd
        return None

    def find(self, node_path: str) -> SceneNode | None:
        """Find a node by scene path. '.' or '' => root; else 'Panel/Btn'."""
        if node_path in (".", "", None):
            return self.root()
        target = node_path.strip("/")
        for nd in self.nodes:
            if nd.parent is None:
                continue
            if nd.path() == target:
                return nd
        return None

    def children_of(self, node_path: str) -> list[SceneNode]:
        """Return direct children of the node at ``node_path``."""
        node = self.find(node_path) if node_path not in (".", "", None) else self.root()
        if node is None:
            return []
        if node.parent is None:
            # root: children have parent '.'
            return [nd for nd in self.nodes if nd.parent == "."]
        node_p = node.path()
        return [nd for nd in self.nodes if nd.parent == node_p]

    def add_node(self, name: str, type: str | None, parent: str, *,
                 instance: str | None = None, groups: list[str] | None = None,
                 index: int | None = None) -> SceneNode:
        """Add a child node. ``parent`` is a scene path ('.' => root child)."""
        nd = SceneNode(name=name, type=type, parent=parent, instance=instance,
                       groups=groups, index=index)
        self.nodes.append(nd)
        return nd

    def remove_node(self, node_path: str) -> None:
        """Remove a node, its descendants, and any connections touching them."""
        node = self.find(node_path)
        if node is None:
            return
        if node.parent is None:
            raise RuntimeError("Cannot remove the root node")
        target = node.path()
        prefix = target + "/"
        removed_paths = set()
        survivors = []
        for nd in self.nodes:
            if nd.parent is None:
                survivors.append(nd)
                continue
            p = nd.path()
            if p == target or p.startswith(prefix):
                removed_paths.add(p)
            else:
                survivors.append(nd)
        self.nodes = survivors
        # drop connections referencing removed nodes
        self.connections = [
            c for c in self.connections
            if c.from_ not in removed_paths and c.to not in removed_paths
        ]
        # drop editables under the removed subtree
        self.editables = [
            e for e in self.editables
            if e != target and not e.startswith(prefix)
        ]

    # ---------- serialization ----------

    def _compute_load_steps(self) -> int | None:
        total = len(self.ext) + len(self.sub)
        return total + 1 if total > 0 else None

    def _header(self) -> str:
        attrs: list[str] = []
        load_steps = self._compute_load_steps()
        if self.kind == "resource":
            tag = "gd_resource"
            if self.resource_type:
                attrs.append(f'type="{self.resource_type}"')
            if self.script_class:
                attrs.append(f'script_class="{self.script_class}"')
        else:
            tag = "gd_scene"
        if load_steps is not None:
            attrs.append(f"load_steps={load_steps}")
        attrs.append(f"format={self.fmt}")
        if self.uid:
            attrs.append(f'uid="{self.uid}"')
        return f"[{tag} {' '.join(attrs)}]"

    def _ext_heading(self, e: ExtResource) -> str:
        parts = [f'type="{e.type}"']
        if e.uid:
            parts.append(f'uid="{e.uid}"')
        parts.append(f'path="{e.path}"')
        parts.append(f'id="{e.id}"')
        return f"[ext_resource {' '.join(parts)}]"

    def _sorted_nodes(self) -> list[SceneNode]:
        """Return nodes with the root first and every parent before its children."""
        root = self.root()
        ordered: list[SceneNode] = []
        if root is not None:
            ordered.append(root)
        # index non-root nodes by parent path
        children: dict[str, list[SceneNode]] = {}
        for nd in self.nodes:
            if nd.parent is None:
                continue
            children.setdefault(nd.parent, []).append(nd)

        def walk(parent_path: str):
            for nd in children.get(parent_path, []):
                ordered.append(nd)
                walk(nd.path())

        if root is not None:
            walk(".")
        # append any orphans (defensive) not yet emitted
        seen = set(id(n) for n in ordered)
        for nd in self.nodes:
            if id(nd) not in seen:
                ordered.append(nd)
        return ordered

    def serialize(self) -> str:
        lines: list[str] = [self._header(), ""]

        # ext_resources
        for e in self.ext:
            lines.append(self._ext_heading(e))
        if self.ext:
            lines.append("")

        # sub_resources
        for s in self.sub:
            lines.append(f'[sub_resource type="{s.type}" id="{s.id}"]')
            for key, value in s.props.items():
                lines.append(f"{key} = {value}")
            lines.append("")

        if self.kind == "resource":
            lines.append("[resource]")
            for key, value in self.resource_props.items():
                lines.append(f"{key} = {value}")
            lines.append("")
            return "\n".join(lines).rstrip("\n") + "\n"

        # scene nodes
        for nd in self._sorted_nodes():
            heading = self._node_heading(nd)
            lines.append(heading)
            for key, value in nd.props.items():
                lines.append(f"{key} = {value}")
            lines.append("")

        # connections
        for c in self.connections:
            lines.append(self._connection_heading(c))
        if self.connections:
            lines.append("")

        # editables
        for path in self.editables:
            lines.append(f'[editable path="{path}"]')
        if self.editables:
            lines.append("")

        return "\n".join(lines).rstrip("\n") + "\n"

    def _node_heading(self, nd: SceneNode) -> str:
        parts = [f'name="{nd.name}"']
        if nd.instance is None and nd.type is not None:
            parts.append(f'type="{nd.type}"')
        if nd.parent is not None:
            parts.append(f'parent="{nd.parent}"')
        if nd.instance is not None:
            parts.append(f'instance=ExtResource("{nd.instance}")')
        if nd.groups:
            grp = ", ".join(quote_string(g) for g in nd.groups)
            parts.append(f"groups=[{grp}]")
        if nd.index is not None:
            parts.append(f'index="{nd.index}"')
        return f"[node {' '.join(parts)}]"

    def _connection_heading(self, c: Connection) -> str:
        parts = [
            f'signal="{c.signal}"',
            f'from="{c.from_}"',
            f'to="{c.to}"',
            f'method="{c.method}"',
        ]
        if c.flags is not None:
            parts.append(f"flags={c.flags}")
        if c.unbinds is not None and c.unbinds > 0:
            parts.append(f"unbinds={c.unbinds}")
        if c.binds is not None:
            parts.append(f"binds={c.binds}")
        return f"[connection {' '.join(parts)}]"

    # ---------- parsing ----------

    @classmethod
    def parse(cls, text: str) -> "TscnFile":
        sections = _split_sections(text)
        if not sections:
            raise RuntimeError("Empty or invalid scene/resource file")

        head_tag, head_attrs, _ = sections[0]
        if head_tag == "gd_resource":
            f = cls(kind="resource")
            f.resource_type = head_attrs.get("type")
            f.script_class = head_attrs.get("script_class")
        elif head_tag == "gd_scene":
            f = cls(kind="scene")
        else:
            raise RuntimeError(f"Unknown file header: [{head_tag}]")

        f.uid = head_attrs.get("uid")
        if head_attrs.get("format"):
            try:
                f.fmt = int(head_attrs["format"])
            except (ValueError, TypeError):
                pass

        for tag, attrs, props in sections[1:]:
            if tag == "ext_resource":
                f.ext.append(ExtResource(
                    type=attrs.get("type", ""),
                    path=attrs.get("path", ""),
                    id=attrs.get("id", ""),
                    uid=attrs.get("uid"),
                ))
            elif tag == "sub_resource":
                f.sub.append(SubResource(
                    type=attrs.get("type", ""),
                    id=attrs.get("id", ""),
                    props=props,
                ))
            elif tag == "node":
                f.nodes.append(_node_from_section(attrs, props))
            elif tag == "connection":
                f.connections.append(_connection_from_attrs(attrs))
            elif tag == "editable":
                if "path" in attrs:
                    f.editables.append(attrs["path"])
            elif tag == "resource":
                f.resource_props = props
        # keep counter ahead of existing ids to avoid collisions on new adds
        f._counter = len(f.ext) + len(f.sub)
        return f


# ---------- parsing helpers ----------

def _node_from_section(attrs: dict, props: "OrderedDict[str,str]") -> SceneNode:
    instance = None
    inst_raw = attrs.get("instance")
    if inst_raw:
        m = re.search(r'ExtResource\("?([^")]+)"?\)', inst_raw)
        instance = m.group(1) if m else inst_raw
    groups = []
    if attrs.get("groups"):
        groups = [g for g in re.findall(r'"([^"]*)"', attrs["groups"])]
    index = None
    if attrs.get("index") is not None:
        try:
            index = int(str(attrs["index"]).strip('"'))
        except ValueError:
            index = None
    return SceneNode(
        name=attrs.get("name", ""),
        type=attrs.get("type"),
        parent=attrs.get("parent"),  # None => root
        instance=instance,
        groups=groups,
        index=index,
        props=props,
    )


def _connection_from_attrs(attrs: dict) -> Connection:
    def _int(key):
        v = attrs.get(key)
        if v is None:
            return None
        try:
            return int(str(v).strip('"'))
        except ValueError:
            return None
    return Connection(
        signal=attrs.get("signal", ""),
        from_=attrs.get("from", ""),
        to=attrs.get("to", ""),
        method=attrs.get("method", ""),
        flags=_int("flags"),
        unbinds=_int("unbinds"),
        binds=attrs.get("binds"),
    )


_HEADING_RE = re.compile(r"^\[([A-Za-z_][A-Za-z0-9_]*)(.*)\]\s*$")


def _split_sections(text: str):
    """Split a tscn/tres into a list of (tag, attrs_dict, props_OrderedDict).

    Handles multi-line property values (Dictionaries / multi-line arrays).
    """
    sections = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    cur = None  # (tag, attrs, props)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == "" or stripped.startswith(";"):
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m and not _looks_like_value_line(stripped):
            if cur is not None:
                sections.append(cur)
            tag = m.group(1)
            attrs = _parse_heading_attrs(m.group(2).strip())
            cur = (tag, attrs, OrderedDict())
            i += 1
            continue

        if "=" in stripped and cur is not None:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            # consume continuation lines for multi-line values
            while not _value_balanced(value) and i + 1 < n:
                i += 1
                value = value + "\n" + lines[i].rstrip()
            cur[2][key] = value
        i += 1

    if cur is not None:
        sections.append(cur)
    return sections


def _looks_like_value_line(stripped: str) -> bool:
    """A heading is '[tag ...]' on its own; a value line like 'x = [1,2]' is not.

    Property value lines never start with '[' (the '[' would be after '=').
    """
    return not stripped.startswith("[")


def _parse_heading_attrs(s: str) -> "OrderedDict[str,str]":
    """Parse 'name="Foo" type="Node2D" load_steps=3 instance=ExtResource("1")'.

    Returns raw string values (quotes stripped for simple quoted strings; raw
    text kept for things like ExtResource("1") and bracketed groups).
    """
    attrs: "OrderedDict[str,str]" = OrderedDict()
    i = 0
    n = len(s)
    while i < n:
        # skip whitespace
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        # read key
        kstart = i
        while i < n and (s[i].isalnum() or s[i] in "_/"):
            i += 1
        key = s[kstart:i]
        # skip to '='
        while i < n and s[i].isspace():
            i += 1
        if i >= n or s[i] != "=":
            break
        i += 1  # skip '='
        while i < n and s[i].isspace():
            i += 1
        # read value: quoted string, bracketed/paren group, or bare token
        if i < n and s[i] == '"':
            j = i + 1
            while j < n and s[j] != '"':
                if s[j] == "\\":
                    j += 1
                j += 1
            value = s[i + 1:j]
            i = j + 1
        else:
            depth = 0
            in_str = False
            vstart = i
            while i < n:
                ch = s[i]
                if in_str:
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch.isspace() and depth == 0:
                    break
                i += 1
            value = s[vstart:i]
        attrs[key] = value
    return attrs


def _value_balanced(s: str) -> bool:
    depth = 0
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        i += 1
    return depth <= 0 and not in_str
