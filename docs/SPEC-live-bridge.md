# SPEC — LIVE Bridge: Real-Time Editor Control for Godot 4.x

Status: DESIGN / RESEARCH (skeletons only — not a full implementation)
Target: Godot 4.2 / 4.3 / 4.4 / 4.5 (notes on per-version differences inline)
Pattern: "CLI-Anything" agent-native CLI, MCP-backend-style. The Godot editor hosts a
small JSON-over-WebSocket server inside an `EditorPlugin`; an external Python client
(driven by the CLI / an AI agent) sends ops and receives JSON replies.

This document specifies the **LIVE layer only** — the channel that controls a *running*
editor in real time. It is complementary to the existing headless/subprocess backend
(`cli_anything/godot/utils/godot_backend.py`), which drives Godot via `--headless --script`
for offline batch work. The LIVE layer is for interactive, stateful editing of an
already-open editor session.

---

## 0. Why an in-editor server (not `--headless --script`)

The existing `godot_backend.py` spawns a fresh `godot --headless --script ...` process per
command. That is perfect for *offline* operations (export, import, batch scene generation)
but it **cannot** observe or mutate the editor a developer already has open: a new process
has its own `SceneTree`, no `EditorInterface`, no live selection, and no shared undo history.

To control a *running* editor you must execute code **inside that editor's process**. The
only supported way to run persistent code inside the editor is an `EditorPlugin` (`@tool`
addon). So the LIVE layer is:

```
AI agent / CLISTM  ──JSON over WebSocket──►  EditorPlugin (runs INSIDE the editor)
   (Python client)  ◄──JSON reply──────────   ├─ TCPServer + WebSocketPeer (127.0.0.1)
                                               ├─ command dispatcher (the switch)
                                               ├─ EditorInterface (live scene/selection)
                                               └─ EditorUndoRedoManager (undoable edits)
```

---

## A. Addon architecture

### A.1 Directory layout

```
<godot_project>/
├── project.godot
└── addons/
    └── live_bridge/
        ├── plugin.cfg          # addon manifest (required)
        ├── plugin.gd           # EditorPlugin entry point (@tool)
        ├── server.gd           # TCPServer + WebSocketPeer transport + frame buffering
        ├── dispatch.gd         # op → handler switch; the command table
        ├── variants.gd         # to_json_variant / from_json_variant coercion bridge
        └── nodeutil.gd         # add-with-owner, reparent, duplicate, instance helpers
```

All scripts are `@tool` so they execute in the editor. `plugin.gd` extends
`EditorPlugin`; the rest are plain `RefCounted`/`Node` helpers it owns.

### A.2 `plugin.cfg`

```ini
[plugin]
name="Live Bridge"
description="JSON/WebSocket bridge for real-time external control of the running editor."
author="cli-anything"
version="0.1.0"
script="plugin.gd"
```

### A.3 Enabling the addon — `project.godot`

Enabling a plugin in the editor UI (Project → Project Settings → Plugins → Enable) writes:

```ini
[editor_plugins]

enabled=PackedStringArray("res://addons/live_bridge/plugin.gd")
```

The CLI can enable it **without** the GUI by writing that key into `project.godot`
(idempotent: read, append the path if missing, write back). This is the
"install + auto-enable" path the CLI's `live install` command uses. Note: a plugin newly
written to disk while the editor is open generally requires the editor to (re)load the
plugin; the safest UX is "install while editor is closed, then launch editor", or use
`EditorInterface.set_plugin_enabled()` if already scripting inside the editor.

### A.4 EditorPlugin lifecycle

- `_enter_tree()` — called when the plugin is enabled / the editor starts with it enabled.
  Here we: construct the server, read host/port/token from `EditorSettings` (or env via
  `OS.get_environment`), and `server.start()`. We also add a `Timer`/use `set_process(true)`
  so `_process` runs.
- `_process(delta)` — called every editor frame (the editor *is* a `SceneTree`, so the
  plugin's `_process` ticks). We `server.poll()` here. **This is the only place we touch
  the socket** — see concurrency note (§3).
- `_exit_tree()` — called when disabled / editor quits. We `server.stop()` and free helpers.

The plugin is the single owner of the server lifetime; the server never outlives the plugin.

---

## B. JSON wire protocol

### B.1 Transport framing

WebSocket text frames, one JSON object per frame (`WebSocketPeer.send_text` /
`get_packet().get_string_from_utf8()`). One request → one response, correlated by `id`.
(If raw TCP is used instead — see §3.2 — frame as newline-delimited JSON, `\n` terminator.)

### B.2 Request envelope

```json
{ "id": "c-42", "op": "node.add", "args": { "...": "..." } }
```

| Field | Type   | Required | Meaning |
|-------|--------|----------|---------|
| `id`  | string | yes      | Client-chosen correlation id, echoed in the response. |
| `op`  | string | yes      | Operation name (see B.4). |
| `args`| object | no       | Op-specific arguments. Defaults to `{}`. |

### B.3 Response envelope

Success:
```json
{ "id": "c-42", "ok": true, "result": { "...": "..." } }
```
Error:
```json
{ "id": "c-42", "ok": false, "error": { "code": "ERR_NO_SCENE", "message": "No scene is open." } }
```

| Field    | Type    | When        | Meaning |
|----------|---------|-------------|---------|
| `id`     | string  | always      | Echoes the request id (or `null` if the request was unparseable). |
| `ok`     | bool    | always      | Success flag. |
| `result` | any     | `ok=true`   | Op result payload (Variant-serialized via §C). |
| `error`  | object  | `ok=false`  | `{ code, message }`. `code` is a stable machine string. |

Standard error codes: `ERR_PARSE`, `ERR_UNKNOWN_OP`, `ERR_BAD_ARGS`, `ERR_NO_SCENE`,
`ERR_NODE_NOT_FOUND`, `ERR_PROP_NOT_FOUND`, `ERR_COERCE` (value couldn't be turned into the
target Variant), `ERR_LOAD` (resource/scene load failed), `ERR_SIGNAL`, `ERR_IO`,
`ERR_AUTH`, `ERR_INTERNAL`.

### B.4 Supported ops

NodePaths below are **relative to the edited scene root** unless the op says otherwise.
Root is addressed as `"."`.

| op | args | returns | one-line |
|----|------|---------|----------|
| `ping` | — | `{pong, godot_version}` | Liveness + version probe. |
| `editor.info` | — | `{version, edited_scene, open_scenes[], main_screen, playing}` | Editor/session status snapshot. |
| `node.get_tree` | `{from?: NodePath, depth?: int, props?: [str]}` | tree dict | Live scene tree from a subtree, optional shallow prop dump. |
| `node.add` | `{parent: NodePath, type: str, name?: str, props?: {}}` | `{path, name}` | Create a node of `type` (ClassDB), add under `parent`, set owner, optional props. |
| `node.delete` | `{path: NodePath}` | `{deleted: path}` | Remove + free a node (undoable). |
| `node.move` | `{path: NodePath, to_index: int}` | `{path, index}` | Reorder a node among its siblings. |
| `node.reparent` | `{path: NodePath, new_parent: NodePath, to_index?: int, keep_global_transform?: bool}` | `{path}` | Reparent, re-own to scene root recursively. |
| `node.rename` | `{path: NodePath, name: str}` | `{path}` | Rename a node. |
| `node.duplicate` | `{path: NodePath, new_parent?: NodePath, name?: str}` | `{path}` | Duplicate subtree, add, re-own recursively. |
| `node.set_prop` | `{path: NodePath, prop: str, value: <JSON-Variant>}` | `{path, prop}` | Set ANY inspector property (incl. sub-paths `a:b:c`), coerced to target type. |
| `node.get_prop` | `{path: NodePath, prop: str}` | `{value}` | Read a property, serialized to JSON. |
| `node.call` | `{path: NodePath, method: str, args?: []}` | `{value}` | Call a method on a node (escape hatch). |
| `node.list_props` | `{path: NodePath, usage?: str}` | `{props: [...]}` | `get_property_list()` filtered (e.g. editor/storage usage). |
| `signal.connect` | `{from: NodePath, signal: str, to: NodePath, method: str, flags?: int, binds?: []}` | `{ok}` | Connect a signal; persisted into the scene (CONNECT_PERSIST). |
| `signal.disconnect` | `{from: NodePath, signal: str, to: NodePath, method: str}` | `{ok}` | Disconnect a persisted signal. |
| `signal.list` | `{path: NodePath}` | `{signals: [...], connections: [...]}` | List a node's signals + outgoing connections. |
| `scene.instance` | `{parent: NodePath, scene: ResPath, name?: str}` | `{path}` | Instance a PackedScene, add under `parent`, re-own. |
| `scene.open` | `{path: ResPath}` | `{root}` | Open a scene file into a new edited tab. |
| `scene.save` | `{path?: ResPath}` | `{path}` | Save current scene (Save As if `path` given). |
| `scene.new` | `{root_type: str, name?: str}` | `{root}` | Create a new empty scene with a fresh root and open it. |
| `scene.close` | `{path?: ResPath}` | `{ok}` | Close (the current) scene tab. |
| `selection.get` | — | `{paths: [...]}` | Current editor selection. |
| `selection.set` | `{paths: [NodePath]}` | `{paths}` | Set the editor selection (+ scroll inspector to first). |
| `play.run` | `{scene?: ResPath}` | `{playing: true}` | Play current scene, or main scene, or a custom scene path. |
| `play.stop` | — | `{playing: false}` | Stop the running game. |
| `play.status` | — | `{playing, scene}` | Is a scene playing, and which. |
| `undo` | `{count?: int}` | `{undone}` | Undo last N editor actions. |
| `redo` | `{count?: int}` | `{redone}` | Redo last N editor actions. |
| `resource.load` | `{path: ResPath}` | `{ref}` | Load a resource, return a `{__type:"Resource"...}` handle. |
| `resource.save` | `{path: ResPath, props?: {}}` | `{path}` | Create/modify and `ResourceSaver.save` a `.tres`/`.res`. |
| `fs.scan` | — | `{ok}` | Trigger `EditorFileSystem.scan()` so new files import/appear. |

Design rule: **all mutating ops go through `EditorUndoRedoManager`** so they are undoable and
mark the scene dirty (see §D.5). `node.call`, `resource.*`, and `play.*` are the exceptions
(side-effects that are not modeled as scene edits).

---

## C. Variant type-coercion design (the crux)

JSON has only objects/arrays/strings/numbers/bools/null. Godot has ~38 Variant types. The
bridge defines a **tagged-object** convention: any JSON object carrying a `"__type"` key is a
typed Variant. Untagged JSON maps 1:1 (number→float/int, string→String, array→Array,
object→Dictionary, bool→bool, null→null). This is fully symmetric: `get_prop` serializes the
inverse so a round-trip is value-preserving.

### C.1 JSON shape → Godot Variant (deserialize, `from_json_variant`)

| Godot type | JSON shape |
|------------|-----------|
| bool / int / float / String | native JSON `true`, `7`, `1.5`, `"hi"` |
| `Vector2` | `{"__type":"Vector2","x":1,"y":2}` |
| `Vector2i` | `{"__type":"Vector2i","x":1,"y":2}` |
| `Vector3` | `{"__type":"Vector3","x":1,"y":2,"z":3}` |
| `Vector3i` | `{"__type":"Vector3i","x":1,"y":2,"z":3}` |
| `Vector4` | `{"__type":"Vector4","x":..,"y":..,"z":..,"w":..}` |
| `Rect2` / `Rect2i` | `{"__type":"Rect2","x":..,"y":..,"w":..,"h":..}` |
| `Color` | `{"__type":"Color","r":..,"g":..,"b":..,"a":..}` or `{"__type":"Color","html":"#ff8800ff"}` |
| `Quaternion` | `{"__type":"Quaternion","x":..,"y":..,"z":..,"w":..}` |
| `Basis` | `{"__type":"Basis","rows":[[..],[..],[..]]}` |
| `Transform2D` | `{"__type":"Transform2D","x":[..],"y":[..],"origin":[..]}` |
| `Transform3D` | `{"__type":"Transform3D","basis":{...},"origin":{...}}` |
| `Plane` | `{"__type":"Plane","normal":{...},"d":..}` |
| `AABB` | `{"__type":"AABB","position":{...},"size":{...}}` |
| `NodePath` | `{"__type":"NodePath","path":"Player/Sprite"}` |
| `StringName` | `{"__type":"StringName","name":"foo"}` |
| `RID` | not transferable — error `ERR_COERCE` (RIDs are process-local). |
| Resource ref | `{"__type":"Resource","path":"res://x.tres"}` (loaded via `load()`); sub-resource: `{"__type":"Resource","inline":{"class":"...","props":{...}}}` (constructed in place) |
| `PackedScene` | same as Resource with a `.tscn`/`.scn` path. |
| enum value | plain int (Godot enums are ints). For convenience also accept `{"__type":"Enum","class":"Light2D","name":"SHADOW_FILTER_PCF5"}` resolved via `ClassDB`/constants. |
| typed Packed arrays | `{"__type":"PackedFloat32Array","data":[..]}`, `PackedInt32Array`, `PackedVector2Array` (array of `{x,y}` or `[x,y]`), `PackedStringArray`, `PackedByteArray` (base64 string in `"b64"`), etc. |
| `Array` / `Dictionary` | native JSON array/object (elements recursively coerced). |
| fallback | `{"__type":"GDS","text":"<var_to_str output>"}` → `str_to_var()` (covers anything else). |

Two coercion modes, used together:

1. **Tag-driven** — if the JSON value carries `__type`, build that exact Variant.
2. **Target-driven** — for `node.set_prop`, we *also* know the destination's declared type
   from `get_property_list()` (`prop.type`). When the incoming JSON is *untagged* (e.g. an
   AI sent `[1,2]` or `"#ff0000"` for a property the engine declares as `Vector2`/`Color`),
   we coerce the loose JSON into the declared type. This is what makes the bridge forgiving
   for agents that don't emit tags. Tag-driven always wins if a tag is present.

The `var_to_str`/`str_to_var` fallback (`__type:"GDS"`) is the universal escape hatch: it can
represent ANY Variant Godot can construct, at the cost of being opaque to non-Godot tooling.
Prefer explicit tags; use GDS only when no structured tag fits.

### C.2 Godot Variant → JSON (serialize, `to_json_variant`)

`get_prop`, `node.get_tree`, and every `result` payload run the inverse. Dispatch on
`typeof(value)`:

- primitives → native JSON.
- math types → their tagged form above.
- `Object`/`Resource` → `{"__type":"Resource","path": res.resource_path}` if it has a path,
  else `{"__type":"Object","class": obj.get_class(), "id": instance_id_or_null}` (cannot be
  fully serialized; a handle only).
- `Node` references → `{"__type":"NodePath","path": <path-from-scene-root>}`.
- `Array`/`Dictionary` → recurse.
- anything exotic / cyclic → `{"__type":"GDS","text": var_to_str(value)}`.

**Cycle / depth guard:** serialization carries a max depth and a visited-object set; on
overflow it emits `{"__type":"GDS","text": var_to_str(value)}` or `{"__truncated":true}`
rather than recursing forever (scene trees and Resources can contain back-references).

---

## D. Concrete GDScript skeletons

> These are realistic, mostly-complete skeletons. An implementer fills the remaining op
> handlers following the established patterns. ~Several hundred lines total.

### D.1 `plugin.gd` — EditorPlugin entry point

```gdscript
@tool
extends EditorPlugin

const Server := preload("res://addons/live_bridge/server.gd")
const Dispatch := preload("res://addons/live_bridge/dispatch.gd")

var _server: Server
var _dispatch: Dispatch

func _enter_tree() -> void:
    var host := _setting("live_bridge/host", "127.0.0.1")   # 127.0.0.1 ONLY by default
    var port := int(_setting("live_bridge/port", 8787))
    var token := str(_setting("live_bridge/token", OS.get_environment("LIVE_BRIDGE_TOKEN")))

    _dispatch = Dispatch.new()
    _dispatch.editor = get_editor_interface()
    _dispatch.undo_redo = get_undo_redo()       # EditorUndoRedoManager (4.x)

    _server = Server.new()
    _server.token = token
    # The server hands each parsed request to this Callable and expects a reply dict back.
    _server.on_request = func(req: Dictionary) -> Dictionary:
        return _dispatch.handle(req)
    var err := _server.start(host, port)
    if err != OK:
        push_error("[live_bridge] server start failed on %s:%d (err %d)" % [host, port, err])
    else:
        print("[live_bridge] listening on %s:%d" % [host, port])
    set_process(true)

func _process(_delta: float) -> void:
    if _server != null:
        _server.poll()        # the ONLY place the socket is touched (editor is single-threaded)

func _exit_tree() -> void:
    set_process(false)
    if _server != null:
        _server.stop()
        _server = null
    _dispatch = null

func _setting(key: String, default_value):
    var full := "live_bridge/%s" % key if not key.begins_with("live_bridge/") else key
    if ProjectSettings.has_setting(full):
        return ProjectSettings.get_setting(full)
    return default_value
```

### D.2 `server.gd` — TCPServer + WebSocketPeer transport

```gdscript
@tool
extends RefCounted
# A single-connection-at-a-time (small N) localhost WebSocket server polled from _process.

var token: String = ""
var on_request: Callable             # func(req: Dictionary) -> Dictionary

var _tcp := TCPServer.new()
var _peers: Array[WebSocketPeer] = []
var _authed := {}                    # WebSocketPeer -> bool

func start(host: String, port: int) -> int:
    return _tcp.listen(port, host)   # bind to 127.0.0.1 by default (see plugin.gd)

func stop() -> void:
    for p in _peers:
        p.close()
    _peers.clear()
    _authed.clear()
    _tcp.stop()

func poll() -> void:
    # 1) Accept new TCP connections, wrap each in a WebSocketPeer handshake.
    while _tcp.is_connection_available():
        var conn := _tcp.take_connection()
        var ws := WebSocketPeer.new()
        if ws.accept_stream(conn) == OK:
            _peers.append(ws)
            _authed[ws] = (token == "")   # if no token configured, auto-authed

    # 2) Pump every peer's state machine and drain inbound frames.
    var still_open: Array[WebSocketPeer] = []
    for ws in _peers:
        ws.poll()
        var state := ws.get_ready_state()
        if state == WebSocketPeer.STATE_OPEN:
            while ws.get_available_packet_count() > 0:
                var text := ws.get_packet().get_string_from_utf8()
                _handle_text(ws, text)
            still_open.append(ws)
        elif state == WebSocketPeer.STATE_CONNECTING:
            still_open.append(ws)
        else: # CLOSING / CLOSED
            _authed.erase(ws)
    _peers = still_open

func _handle_text(ws: WebSocketPeer, text: String) -> void:
    var parsed = JSON.parse_string(text)
    if typeof(parsed) != TYPE_DICTIONARY:
        _reply(ws, _err(null, "ERR_PARSE", "Request must be a JSON object."))
        return
    var req: Dictionary = parsed
    var id = req.get("id", null)

    # --- auth gate: first frame may be {"op":"auth","args":{"token":...}} ---
    if not _authed.get(ws, false):
        if req.get("op", "") == "auth" and str(req.get("args", {}).get("token", "")) == token:
            _authed[ws] = true
            _reply(ws, {"id": id, "ok": true, "result": {"authed": true}})
        else:
            _reply(ws, _err(id, "ERR_AUTH", "Authentication required."))
        return

    # --- dispatch (runs synchronously on the editor thread) ---
    var resp: Dictionary
    if on_request.is_valid():
        resp = on_request.call(req)
    else:
        resp = _err(id, "ERR_INTERNAL", "No handler bound.")
    _reply(ws, resp)

func _reply(ws: WebSocketPeer, resp: Dictionary) -> void:
    ws.send_text(JSON.stringify(resp))

static func _err(id, code: String, message: String) -> Dictionary:
    return {"id": id, "ok": false, "error": {"code": code, "message": message}}
```

### D.3 `dispatch.gd` — command switch, owner-setting, undo/redo

```gdscript
@tool
extends RefCounted

const Variants := preload("res://addons/live_bridge/variants.gd")
const NodeUtil := preload("res://addons/live_bridge/nodeutil.gd")

var editor: EditorInterface
var undo_redo: EditorUndoRedoManager

# ---- entry point ----
func handle(req: Dictionary) -> Dictionary:
    var id = req.get("id", null)
    var op := str(req.get("op", ""))
    var args: Dictionary = req.get("args", {}) if typeof(req.get("args")) == TYPE_DICTIONARY else {}
    # Everything below runs on the editor (main) thread because poll() is called from _process.
    match op:
        "ping":            return _ok(id, {"pong": true, "godot_version": Engine.get_version_info()})
        "editor.info":     return _ok(id, _editor_info())
        "node.get_tree":   return _wrap(id, func(): return _get_tree(args))
        "node.add":        return _wrap(id, func(): return _node_add(args))
        "node.delete":     return _wrap(id, func(): return _node_delete(args))
        "node.set_prop":   return _wrap(id, func(): return _node_set_prop(args))
        "node.get_prop":   return _wrap(id, func(): return _node_get_prop(args))
        "node.reparent":   return _wrap(id, func(): return _node_reparent(args))
        "node.duplicate":  return _wrap(id, func(): return _node_duplicate(args))
        "node.move":       return _wrap(id, func(): return _node_move(args))
        "signal.connect":  return _wrap(id, func(): return _signal_connect(args))
        "scene.instance":  return _wrap(id, func(): return _scene_instance(args))
        "scene.open":      return _wrap(id, func(): return _scene_open(args))
        "scene.save":      return _wrap(id, func(): return _scene_save(args))
        "selection.get":   return _ok(id, {"paths": _selection_paths()})
        "selection.set":   return _wrap(id, func(): return _selection_set(args))
        "play.run":        return _wrap(id, func(): return _play_run(args))
        "play.stop":       editor.stop_playing_scene(); return _ok(id, {"playing": false})
        "undo":            return _wrap(id, func(): return _undo(args))
        "redo":            return _wrap(id, func(): return _redo(args))
        _:                 return _err(id, "ERR_UNKNOWN_OP", "Unknown op: %s" % op)

# ---- helpers: scene root + path resolution ----
func _root() -> Node:
    var r := editor.get_edited_scene_root()
    if r == null:
        push_error("ERR_NO_SCENE")    # surfaced as exception caught by _wrap
        assert(false, "ERR_NO_SCENE")
    return r

func _resolve(path_str: String) -> Node:
    var root := _root()
    if path_str == "." or path_str == "":
        return root
    var n := root.get_node_or_null(NodePath(path_str))
    if n == null:
        assert(false, "ERR_NODE_NOT_FOUND:%s" % path_str)
    return n

# ---- node.add (owner-setting is CRITICAL) ----
func _node_add(args: Dictionary) -> Dictionary:
    var root := _root()
    var parent := _resolve(str(args.get("parent", ".")))
    var type := str(args.get("type", ""))
    if not ClassDB.class_exists(type) or not ClassDB.can_instantiate(type):
        assert(false, "ERR_BAD_ARGS:type")
    var node: Node = ClassDB.instantiate(type)
    if args.has("name"):
        node.name = str(args["name"])

    undo_redo.create_action("LiveBridge: add %s" % type)
    undo_redo.add_do_method(parent, "add_child", node)
    # OWNER MUST be the edited scene root, or the node is NOT saved into the .tscn.
    undo_redo.add_do_method(node, "set_owner", root)
    undo_redo.add_do_reference(node)              # keep the new node alive across undo
    undo_redo.add_undo_method(parent, "remove_child", node)
    undo_redo.commit_action()                      # commit executes the do-methods now

    # apply optional initial properties (separate, also undoable)
    if args.has("props") and typeof(args["props"]) == TYPE_DICTIONARY:
        for k in args["props"]:
            _set_one_prop(node, str(k), args["props"][k])

    editor.mark_scene_as_unsaved()
    return {"path": str(root.get_path_to(node)), "name": node.name}

# ---- node.set_prop (handles sub-resource paths a:b:c, target-driven coercion) ----
func _node_set_prop(args: Dictionary) -> Dictionary:
    var node := _resolve(str(args["path"]))
    var prop := str(args["prop"])
    _set_one_prop(node, prop, args.get("value"))
    editor.mark_scene_as_unsaved()
    return {"path": str(_root().get_path_to(node)), "prop": prop}

func _set_one_prop(obj: Object, prop: String, json_value) -> void:
    var hint_type := Variants.declared_type(obj, prop)   # from get_property_list(), or -1
    var value = Variants.from_json_variant(json_value, hint_type, editor)
    var old_value = obj.get_indexed(prop)                # supports "a:b:c" sub-paths
    undo_redo.create_action("LiveBridge: set %s.%s" % [obj, prop])
    undo_redo.add_do_property(obj, prop, value)          # works with indexed paths
    undo_redo.add_undo_property(obj, prop, old_value)
    undo_redo.commit_action()

func _node_get_prop(args: Dictionary) -> Dictionary:
    var node := _resolve(str(args["path"]))
    var value = node.get_indexed(str(args["prop"]))
    return {"value": Variants.to_json_variant(value, _root())}

# ---- node.delete ----
func _node_delete(args: Dictionary) -> Dictionary:
    var node := _resolve(str(args["path"]))
    var parent := node.get_parent()
    var idx := node.get_index()
    undo_redo.create_action("LiveBridge: delete %s" % node.name)
    undo_redo.add_do_method(parent, "remove_child", node)
    undo_redo.add_do_method(node, "queue_free")          # do NOT free on undo
    undo_redo.add_undo_method(parent, "add_child", node)
    undo_redo.add_undo_method(node, "set_owner", _root())
    undo_redo.add_undo_method(parent, "move_child", node, idx)
    undo_redo.add_undo_reference(node)                    # keep alive for redo
    undo_redo.commit_action()
    editor.mark_scene_as_unsaved()
    return {"deleted": str(args["path"])}

# ---- node.reparent (re-own subtree) ----
func _node_reparent(args: Dictionary) -> Dictionary:
    var node := _resolve(str(args["path"]))
    var new_parent := _resolve(str(args["new_parent"]))
    var old_parent := node.get_parent()
    undo_redo.create_action("LiveBridge: reparent %s" % node.name)
    undo_redo.add_do_method(old_parent, "remove_child", node)
    undo_redo.add_do_method(new_parent, "add_child", node)
    undo_redo.add_do_method(NodeUtil, "own_recursive", node, _root())
    undo_redo.add_undo_method(new_parent, "remove_child", node)
    undo_redo.add_undo_method(old_parent, "add_child", node)
    undo_redo.add_undo_method(NodeUtil, "own_recursive", node, _root())
    undo_redo.commit_action()
    editor.mark_scene_as_unsaved()
    return {"path": str(_root().get_path_to(node))}

# ---- node.duplicate ----
func _node_duplicate(args: Dictionary) -> Dictionary:
    var src := _resolve(str(args["path"]))
    var root := _root()
    var parent := _resolve(str(args.get("new_parent", str(root.get_path_to(src.get_parent())))))
    var dup := src.duplicate()
    if args.has("name"): dup.name = str(args["name"])
    undo_redo.create_action("LiveBridge: duplicate %s" % src.name)
    undo_redo.add_do_method(parent, "add_child", dup)
    undo_redo.add_do_method(NodeUtil, "own_recursive", dup, root)
    undo_redo.add_do_reference(dup)
    undo_redo.add_undo_method(parent, "remove_child", dup)
    undo_redo.commit_action()
    editor.mark_scene_as_unsaved()
    return {"path": str(root.get_path_to(dup))}

# ---- signal.connect (persisted into the scene) ----
func _signal_connect(args: Dictionary) -> Dictionary:
    var from := _resolve(str(args["from"]))
    var to := _resolve(str(args["to"]))
    var sig := str(args["signal"])
    var method := str(args["method"])
    # CONNECT_PERSIST makes the editor serialize the connection into the .tscn.
    var flags := int(args.get("flags", Object.CONNECT_PERSIST))
    var callable := Callable(to, method)
    undo_redo.create_action("LiveBridge: connect %s.%s" % [from.name, sig])
    undo_redo.add_do_method(from, "connect", sig, callable, flags)
    undo_redo.add_undo_method(from, "disconnect", sig, callable)
    undo_redo.commit_action()
    editor.mark_scene_as_unsaved()
    return {"ok": true}

# ---- scene.instance (PackedScene) ----
func _scene_instance(args: Dictionary) -> Dictionary:
    var root := _root()
    var parent := _resolve(str(args.get("parent", ".")))
    var packed := load(str(args["scene"])) as PackedScene
    if packed == null:
        assert(false, "ERR_LOAD:%s" % args["scene"])
    var inst := packed.instantiate()
    if args.has("name"): inst.name = str(args["name"])
    undo_redo.create_action("LiveBridge: instance %s" % args["scene"])
    undo_redo.add_do_method(parent, "add_child", inst)
    # For an instanced scene, only the ROOT of the instance gets owner = edited root;
    # its internal children keep their own scene as owner (do NOT own_recursive here).
    undo_redo.add_do_method(inst, "set_owner", root)
    undo_redo.add_do_reference(inst)
    undo_redo.add_undo_method(parent, "remove_child", inst)
    undo_redo.commit_action()
    editor.mark_scene_as_unsaved()
    return {"path": str(root.get_path_to(inst))}

# ---- scene open/save/play ----
func _scene_open(args: Dictionary) -> Dictionary:
    editor.open_scene_from_path(str(args["path"]))
    return {"root": null}    # async; client should poll editor.info / node.get_tree

func _scene_save(args: Dictionary) -> Dictionary:
    var err: int
    if args.has("path"):
        editor.save_scene_as(str(args["path"]))     # returns void
        return {"path": str(args["path"])}
    err = editor.save_scene()                        # returns Error
    if err != OK: assert(false, "ERR_IO:%d" % err)
    var r := editor.get_edited_scene_root()
    return {"path": r.scene_file_path if r else ""}

func _play_run(args: Dictionary) -> Dictionary:
    if args.has("scene"):
        editor.play_custom_scene(str(args["scene"]))
    else:
        editor.play_current_scene()
    return {"playing": true}

func _undo(args: Dictionary) -> Dictionary:
    var n := int(args.get("count", 1))
    var hist := undo_redo.get_history_undo_redo(undo_redo.get_object_history_id(_root()))
    var done := 0
    for i in n:
        if hist.has_undo(): hist.undo(); done += 1
    return {"undone": done}

func _redo(args: Dictionary) -> Dictionary:
    var n := int(args.get("count", 1))
    var hist := undo_redo.get_history_undo_redo(undo_redo.get_object_history_id(_root()))
    var done := 0
    for i in n:
        if hist.has_redo(): hist.redo(); done += 1
    return {"redone": done}

# ---- introspection ----
func _editor_info() -> Dictionary:
    var r := editor.get_edited_scene_root()
    return {
        "version": Engine.get_version_info(),
        "edited_scene": r.scene_file_path if r else null,
        "open_scenes": Array(editor.get_open_scenes()),
        "playing": editor.is_playing_scene(),
    }

func _get_tree(args: Dictionary) -> Dictionary:
    var root := _root()
    var from := _resolve(str(args.get("from", ".")))
    var depth := int(args.get("depth", -1))
    return _node_to_dict(from, root, depth)

func _node_to_dict(node: Node, root: Node, depth: int) -> Dictionary:
    var d := {
        "name": node.name,
        "type": node.get_class(),
        "path": str(root.get_path_to(node)),
        "scene_file_path": node.scene_file_path,   # non-empty => instanced scene
        "children": [],
    }
    if depth != 0:
        for c in node.get_children():
            d["children"].append(_node_to_dict(c, root, depth - 1))
    return d

func _selection_paths() -> Array:
    var root := editor.get_edited_scene_root()
    var out := []
    if root == null: return out
    for n in editor.get_selection().get_selected_nodes():
        out.append(str(root.get_path_to(n)))
    return out

func _selection_set(args: Dictionary) -> Dictionary:
    var sel := editor.get_selection()
    sel.clear()
    var first: Node = null
    for p in args.get("paths", []):
        var n := _resolve(str(p))
        sel.add_node(n)
        if first == null: first = n
    if first: editor.edit_node(first)
    return {"paths": _selection_paths()}

# ---- envelope helpers ----
func _ok(id, result) -> Dictionary: return {"id": id, "ok": true, "result": result}
func _err(id, code, msg) -> Dictionary: return {"id": id, "ok": false, "error": {"code": code, "message": msg}}

# Run a handler, turning thrown assert/push_error("CODE:detail") into a JSON error envelope.
func _wrap(id, fn: Callable) -> Dictionary:
    # NOTE: GDScript has no try/except. In practice, validate inputs and return _err()
    # explicitly from each handler; this wrapper centralizes the success path. The
    # assert(false,"CODE") calls above are placeholders the implementer should replace
    # with explicit `return _err(...)` returns (or use a Result dict from each handler).
    var result = fn.call()
    if typeof(result) == TYPE_DICTIONARY and result.has("__error"):
        return _err(id, result["__error"], result.get("__message", ""))
    return _ok(id, result)
```

> Implementation note on errors: GDScript lacks exceptions, so `assert(false,"CODE")` above
> is shorthand. In the real addon, each handler should **validate and early-return** a
> sentinel `{"__error":"ERR_NODE_NOT_FOUND","__message":"..."}` which `_wrap` converts to the
> error envelope. Do not ship `assert` for control flow (asserts are stripped in release and
> halt in debug). This is the one place the skeleton deliberately leaves work for the
> implementer.

### D.4 `nodeutil.gd` — owner helpers

```gdscript
@tool
extends RefCounted

# Set owner on a node AND all descendants that belong to the edited scene
# (skip nodes that are part of an instanced sub-scene: those keep their own owner).
static func own_recursive(node: Node, scene_root: Node) -> void:
    if node != scene_root:
        node.owner = scene_root
    for c in node.get_children():
        # A child that is the root of an instanced scene has a non-empty scene_file_path;
        # its internal nodes must NOT be re-owned to our root.
        if c.scene_file_path != "":
            c.owner = scene_root        # the instance ROOT is owned by us...
            # ...but we stop here; don't descend into the instance's internals.
        else:
            own_recursive(c, scene_root)
```

### D.5 Variant coercion — `variants.gd`

```gdscript
@tool
extends RefCounted

# Return the declared Variant type of `prop` on `obj` (from get_property_list), or -1.
static func declared_type(obj: Object, prop: String) -> int:
    var head := prop.split(":")[0]   # for "a:b:c" we only know the top-level type cheaply
    for p in obj.get_property_list():
        if p.name == head:
            return p.type
    return -1

# ---------------- JSON -> Variant ----------------
static func from_json_variant(v, hint_type: int = -1, editor: EditorInterface = null):
    # 1) tagged object always wins
    if typeof(v) == TYPE_DICTIONARY and v.has("__type"):
        return _from_tagged(v, editor)
    # 2) plain containers recurse
    if typeof(v) == TYPE_DICTIONARY:
        var d := {}
        for k in v: d[k] = from_json_variant(v[k], -1, editor)
        return d
    if typeof(v) == TYPE_ARRAY:
        # If the target wants a math type, allow loose arrays: [x,y] -> Vector2 etc.
        var coerced := _loose_array_to_type(v, hint_type)
        if coerced != null: return coerced
        var arr := []
        for e in v: arr.append(from_json_variant(e, -1, editor))
        return arr
    # 3) target-driven coercion of loose scalars (e.g. "#ff0000" -> Color)
    if hint_type != -1:
        var c = _loose_scalar_to_type(v, hint_type)
        if c != null: return c
    # 4) primitives pass through; ints stay ints, floats stay floats
    return v

static func _from_tagged(v: Dictionary, editor: EditorInterface):
    match str(v["__type"]):
        "Vector2":  return Vector2(v.x, v.y)
        "Vector2i": return Vector2i(int(v.x), int(v.y))
        "Vector3":  return Vector3(v.x, v.y, v.z)
        "Vector3i": return Vector3i(int(v.x), int(v.y), int(v.z))
        "Vector4":  return Vector4(v.x, v.y, v.z, v.w)
        "Color":
            if v.has("html"): return Color.html(str(v["html"]))
            return Color(v.get("r",0), v.get("g",0), v.get("b",0), v.get("a",1))
        "Rect2":    return Rect2(v.x, v.y, v.w, v.h)
        "Rect2i":   return Rect2i(int(v.x), int(v.y), int(v.w), int(v.h))
        "Quaternion": return Quaternion(v.x, v.y, v.z, v.w)
        "AABB":     return AABB(from_json_variant(v.position), from_json_variant(v.size))
        "Plane":    return Plane(from_json_variant(v.normal), float(v.d))
        "NodePath": return NodePath(str(v["path"]))
        "StringName": return StringName(str(v["name"]))
        "PackedByteArray": return Marshalls.base64_to_raw(str(v["b64"]))
        "PackedFloat32Array": return PackedFloat32Array(v["data"])
        "PackedInt32Array":   return PackedInt32Array(v["data"])
        "PackedStringArray":  return PackedStringArray(v["data"])
        "PackedVector2Array":
            var pv := PackedVector2Array()
            for e in v["data"]: pv.append(_xy(e))
            return pv
        "Resource":
            if v.has("path"): return load(str(v["path"]))
            if v.has("inline"): return _build_inline_resource(v["inline"], editor)
            return null
        "Enum":
            return ClassDB.class_get_integer_constant(str(v["class"]), str(v["name"]))
        "GDS":  return str_to_var(str(v["text"]))   # universal fallback
        _:      return null

static func _build_inline_resource(spec: Dictionary, editor: EditorInterface) -> Resource:
    var cls := str(spec.get("class", "Resource"))
    var res: Resource = ClassDB.instantiate(cls)
    for k in spec.get("props", {}):
        res.set_indexed(k, from_json_variant(spec["props"][k], -1, editor))
    return res

static func _xy(e):
    if typeof(e) == TYPE_ARRAY: return Vector2(e[0], e[1])
    return Vector2(e.get("x",0), e.get("y",0))

static func _loose_scalar_to_type(v, t: int):
    match t:
        TYPE_COLOR: if typeof(v)==TYPE_STRING: return Color.html(v)
        TYPE_INT:   return int(v)
        TYPE_FLOAT: return float(v)
        TYPE_STRING_NAME: return StringName(str(v))
        TYPE_NODE_PATH:   return NodePath(str(v))
    return null

static func _loose_array_to_type(v: Array, t: int):
    match t:
        TYPE_VECTOR2: return Vector2(v[0], v[1])
        TYPE_VECTOR3: return Vector3(v[0], v[1], v[2])
        TYPE_VECTOR2I: return Vector2i(int(v[0]), int(v[1]))
        TYPE_COLOR:    return Color(v[0], v[1], v[2], v[3] if v.size()>3 else 1.0)
    return null

# ---------------- Variant -> JSON ----------------
static func to_json_variant(value, scene_root: Node = null, depth: int = 8):
    if depth <= 0: return {"__type": "GDS", "text": var_to_str(value)}
    match typeof(value):
        TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
            return value
        TYPE_STRING_NAME: return {"__type":"StringName","name": str(value)}
        TYPE_VECTOR2:  return {"__type":"Vector2","x":value.x,"y":value.y}
        TYPE_VECTOR2I: return {"__type":"Vector2i","x":value.x,"y":value.y}
        TYPE_VECTOR3:  return {"__type":"Vector3","x":value.x,"y":value.y,"z":value.z}
        TYPE_VECTOR4:  return {"__type":"Vector4","x":value.x,"y":value.y,"z":value.z,"w":value.w}
        TYPE_COLOR:    return {"__type":"Color","r":value.r,"g":value.g,"b":value.b,"a":value.a}
        TYPE_RECT2:    return {"__type":"Rect2","x":value.position.x,"y":value.position.y,"w":value.size.x,"h":value.size.y}
        TYPE_QUATERNION: return {"__type":"Quaternion","x":value.x,"y":value.y,"z":value.z,"w":value.w}
        TYPE_NODE_PATH: return {"__type":"NodePath","path": str(value)}
        TYPE_DICTIONARY:
            var d := {}
            for k in value: d[str(k)] = to_json_variant(value[k], scene_root, depth-1)
            return d
        TYPE_ARRAY:
            var a := []
            for e in value: a.append(to_json_variant(e, scene_root, depth-1))
            return a
        TYPE_PACKED_BYTE_ARRAY:
            return {"__type":"PackedByteArray","b64": Marshalls.raw_to_base64(value)}
        TYPE_OBJECT:
            if value == null: return null
            if value is Node and scene_root != null:
                return {"__type":"NodePath","path": str(scene_root.get_path_to(value))}
            if value is Resource:
                if value.resource_path != "":
                    return {"__type":"Resource","path": value.resource_path}
                return {"__type":"Resource","class": value.get_class(), "inline": true}
            return {"__type":"Object","class": value.get_class()}
        _:
            return {"__type":"GDS","text": var_to_str(value)}
```

---

## E. Python client skeleton

Stdlib-only WebSocket is *not* available (`websockets`/`websocket-client` are 3rd-party).
Two options, both shown. **Recommended: depend on `websocket-client`** (tiny, widely
available) for robustness; provide the raw-TCP fallback only if zero deps is a hard
requirement (then the addon should use the raw-TCP newline framing of §3.2 instead).

```python
# cli_anything/godot/utils/live_client.py
"""Python client for the Godot LIVE editor bridge (WebSocket, JSON ops)."""
import json
import itertools
from typing import Any

try:
    import websocket  # pip install websocket-client
except ImportError:  # pragma: no cover
    websocket = None


class LiveBridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class LiveBridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 8787,
                 token: str | None = None, timeout: float = 10.0):
        if websocket is None:
            raise RuntimeError("Install 'websocket-client': pip install websocket-client")
        self.url = f"ws://{host}:{port}"
        self.token = token
        self.timeout = timeout
        self._ws: "websocket.WebSocket | None" = None
        self._ids = itertools.count(1)

    # ---- connection ----
    def connect(self) -> None:
        self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        if self.token:
            self._call("auth", {"token": self.token})

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def __enter__(self): self.connect(); return self
    def __exit__(self, *exc): self.close()

    # ---- core request/response ----
    def _call(self, op: str, args: dict | None = None) -> Any:
        assert self._ws is not None, "not connected"
        req_id = f"c-{next(self._ids)}"
        self._ws.send(json.dumps({"id": req_id, "op": op, "args": args or {}}))
        # one-request/one-response; loop to skip any stale frames with mismatched id
        while True:
            resp = json.loads(self._ws.recv())
            if resp.get("id") != req_id:
                continue
            if not resp.get("ok"):
                err = resp.get("error", {})
                raise LiveBridgeError(err.get("code", "ERR"), err.get("message", ""))
            return resp.get("result")

    # ---- typed value helpers (build tagged JSON for the coercion bridge) ----
    @staticmethod
    def vec2(x, y):  return {"__type": "Vector2", "x": x, "y": y}
    @staticmethod
    def vec3(x, y, z): return {"__type": "Vector3", "x": x, "y": y, "z": z}
    @staticmethod
    def color(r, g, b, a=1.0): return {"__type": "Color", "r": r, "g": g, "b": b, "a": a}
    @staticmethod
    def res(path): return {"__type": "Resource", "path": path}
    @staticmethod
    def node_path(p): return {"__type": "NodePath", "path": p}

    # ---- op wrappers (map 1:1 onto the table in §B.4) ----
    def ping(self):                       return self._call("ping")
    def info(self):                       return self._call("editor.info")
    def get_tree(self, frm=".", depth=-1):return self._call("node.get_tree", {"from": frm, "depth": depth})
    def add(self, parent, type, name=None, props=None):
        return self._call("node.add", {"parent": parent, "type": type, "name": name, "props": props or {}})
    def delete(self, path):               return self._call("node.delete", {"path": path})
    def move(self, path, to_index):       return self._call("node.move", {"path": path, "to_index": to_index})
    def reparent(self, path, new_parent): return self._call("node.reparent", {"path": path, "new_parent": new_parent})
    def set_prop(self, path, prop, value):return self._call("node.set_prop", {"path": path, "prop": prop, "value": value})
    def get_prop(self, path, prop):       return self._call("node.get_prop", {"path": path, "prop": prop})
    def duplicate(self, path, **kw):      return self._call("node.duplicate", {"path": path, **kw})
    def connect_signal(self, frm, signal, to, method):
        return self._call("signal.connect", {"from": frm, "signal": signal, "to": to, "method": method})
    def instance(self, parent, scene, name=None):
        return self._call("scene.instance", {"parent": parent, "scene": scene, "name": name})
    def open_scene(self, path):           return self._call("scene.open", {"path": path})
    def save_scene(self, path=None):      return self._call("scene.save", {"path": path} if path else {})
    def selection_get(self):              return self._call("selection.get")
    def selection_set(self, paths):       return self._call("selection.set", {"paths": paths})
    def play(self, scene=None):           return self._call("play.run", {"scene": scene} if scene else {})
    def stop(self):                       return self._call("play.stop")
    def undo(self, count=1):              return self._call("undo", {"count": count})
    def redo(self, count=1):              return self._call("redo", {"count": count})
```

### E.1 How `--live` CLI commands map onto ops

The CLI grows a `live` command group. Each subcommand opens a `LiveBridge`, calls one op,
prints the JSON result (and supports `--json`):

```
cli-anything-godot live status                         -> editor.info
cli-anything-godot live tree [--from .] [--depth N]    -> node.get_tree
cli-anything-godot live add Sprite2D --parent . --name Hero
                                                       -> node.add
cli-anything-godot live set Hero position --vec2 100 50 -> node.set_prop (value=vec2 tag)
cli-anything-godot live set Hero texture --res res://hero.png
                                                       -> node.set_prop (value=Resource tag)
cli-anything-godot live get Hero position              -> node.get_prop
cli-anything-godot live connect Button pressed Hero _on_pressed
                                                       -> signal.connect
cli-anything-godot live instance res://Enemy.tscn --parent .
                                                       -> scene.instance
cli-anything-godot live select Hero                    -> selection.set
cli-anything-godot live save [path]                    -> scene.save
cli-anything-godot live play [scene] / live stop       -> play.run / play.stop
cli-anything-godot live undo [N] / live redo [N]       -> undo / redo
```

Connection config: `--host/--port/--token` flags, or `GODOT_LIVE_HOST/PORT/TOKEN` env, or
defaults `127.0.0.1:8787`. A `live install` command writes the addon and enables it in
`project.godot`; `live status` doubles as the "is the bridge reachable" probe.

---

## 3. Transport choice + concurrency

**Choice: TCPServer + WebSocketPeer.accept_stream(), bound to 127.0.0.1, polled in
`_process`.** Rationale:

1. **Zero external deps in the editor** — `WebSocketPeer`/`TCPServer` are built into Godot 4.
2. **Editor is single-threaded.** All `EditorInterface`/scene/`EditorUndoRedoManager` calls
   MUST happen on the main thread. Polling the socket in `_process` (which runs on the main
   thread every editor frame) means dispatch runs on the main thread *for free* — no locks,
   no marshaling. This is the decisive reason to poll rather than run a background thread.
3. **WebSocket framing** gives us message boundaries (one JSON object per text frame) without
   writing a length-prefix/line parser, and lets the Python side use the well-trodden
   `websocket-client`.

### 3.2 Raw-TCP fallback

If a zero-dependency Python client is mandatory, drop WebSocket and use `TCPServer` +
`StreamPeerTCP` directly with **newline-delimited JSON** (read available bytes into a
per-peer buffer in `poll()`, split on `\n`, dispatch each complete line, reply with
`json + "\n"`). The Python side is then just `socket` + `makefile`. Trade-off: you
hand-roll buffering/partial-frame handling that WebSocket gives you for free. Recommendation:
use WebSocket + `websocket-client` unless the harness's "stdlib-only" rule forbids it.

### 3.3 Concurrency model

- Single editor thread; one op processed fully (synchronously) before the next.
- Long ops (e.g. `play.run`, `scene.open`) are fire-and-forget at the API level — they kick
  off an editor action and return immediately; the client polls `editor.info`/`play.status`
  to observe completion. Do NOT block inside a handler waiting on editor async work.
- Multiple clients are allowed (array of peers) but share one serial execution lane.

---

## F. Risks & gotchas

1. **Owner not set ⇒ the node is never saved.** This is the single most important rule.
   A node added with `add_child()` but without `owner = get_edited_scene_root()` shows up in
   the running tree but is silently dropped from the `.tscn` on save. For reparent/duplicate
   you must re-own the **whole subtree** (`own_recursive`), but for an **instanced** sub-scene
   you set owner only on the instance *root* — never re-own the instance's internal children
   (doing so corrupts the instance / leaks its internals into the host scene). Also: a node's
   `owner` can only be set *after* it has been added to the tree under the (eventual) owner.

2. **Editor is single-threaded — never touch the editor off the main thread.** All scene,
   `EditorInterface`, and `EditorUndoRedoManager` access must be on the main thread. The
   design enforces this by only ever dispatching from `_process`. If you add worker threads,
   you must `call_deferred` back to the main thread and you lose the simple request/response
   timing. Don't. Also: don't call blocking/`OS.delay` in a handler — it freezes the editor.

3. **Value coercion failures.** JSON↔Variant is lossy and ambiguous: an AI may send `[1,2]`
   for a `Color`, a float where an enum int is wanted, a `res://` path to a resource that
   fails to load, or an inline sub-resource of a class that can't be instantiated. Mitigate
   with: target-driven coercion (use `get_property_list()` `prop.type`), explicit `__type`
   tags, a `var_to_str/str_to_var` (`__type:"GDS"`) escape hatch, and a clear `ERR_COERCE`
   error that names the property, the received JSON, and the expected type so the agent can
   self-correct. `RID` and live `Object` handles are **not** transferable and must error.

4. **Version differences across 4.2 / 4.3 / 4.4 / 4.5.**
   - `EditorUndoRedoManager` (returned by `EditorPlugin.get_undo_redo()`) replaced the old
     `UndoRedo` in **4.0**; this skeleton is 4.x-only. Calls like `add_do_method`/
     `add_do_property`/`commit_action`/`add_do_reference` are stable across 4.2–4.5.
   - **Undo-history routing changed in 4.3.** The pairing of
     `get_history_undo_redo(get_object_history_id(node))` and which history a committed action
     lands in shifted between 4.2 and 4.3 (reports of actions landing in history id 1 vs the
     global/scene history and not applying). Treat the `undo`/`redo` handlers as the most
     version-fragile code: prefer triggering the editor's own undo (e.g. via the menu/action
     or `EditorInterface`) and test per target version. For *committing* edits, relying on the
     default history (omit `custom_context`) is the most robust path; `custom_context` has had
     "UndoRedo history mismatch" bugs.
   - `EditorInterface` became a **global singleton** accessible as `EditorInterface` (static)
     in 4.2; in 4.0/4.1 you obtained it via `get_editor_interface()`. The skeleton uses the
     `get_editor_interface()` form passed in by the plugin, which works across all 4.x.
   - `mark_scene_as_unsaved()` exists in 4.x but has had bugs (e.g. C# binding in 4.x, and a
     4.5-era "Reload Saved Scene" ignoring the flag). Treat the dirty flag as best-effort;
     committing through `EditorUndoRedoManager` is the more reliable way to mark a scene dirty.
   - `get_edited_scene_root()` always refers to the **currently active** scene tab only; there
     is no per-scene targeting. Ops that name a node operate on the active scene; switch tabs
     with `scene.open` first. (No multi-edited-scene API exists in 4.2–4.5.)
   - `play_*` / `stop_playing_scene` / `is_playing_scene` signatures are stable across 4.2–4.5.

5. **Security — localhost only, optional token.** Bind to `127.0.0.1` exclusively (never
   `0.0.0.0`); the bridge can execute arbitrary node creation, property sets, `node.call`
   (method invocation), and play/stop — it is effectively remote code execution on the dev
   machine. Add an optional shared-token gate (`auth` as the mandatory first frame when a
   token is configured; reject all other ops until authed). Keep the token out of logs.
   Consider an allowlist of ops and a kill-switch (disable plugin) for untrusted agents.
   Because `_process` polls continuously while enabled, document that the bridge is "on"
   whenever the addon is enabled — provide a `live install`/uninstall toggle.

Other practical gotchas: newly written-to-disk resources need `EditorFileSystem.scan()`
(`fs.scan`) before they're loadable/visible; `scene.open` is async so don't read the tree in
the same op; `queue_free` (not `free`) when deleting tree nodes; keep new/duplicated nodes
alive across undo with `add_do_reference`/`add_undo_reference` or they get collected.

---

## Cross-references

- Headless/offline counterpart: `cli_anything/godot/utils/godot_backend.py` (subprocess,
  `--headless --script`). The LIVE layer does NOT replace it; they cover disjoint use cases
  (offline batch vs. interactive live editing).
- Methodology: `cli-anything-plugin/HARNESS.md`, MCP backend: `guides/mcp-backend.md`.
