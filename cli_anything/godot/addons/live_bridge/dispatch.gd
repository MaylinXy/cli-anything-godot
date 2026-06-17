@tool
extends RefCounted
## Live Bridge command dispatcher. See SPEC-live-bridge.md §B.4 / §D.3.
##
## Each op handler runs synchronously on the editor main thread (poll() is driven
## from EditorPlugin._process). GDScript has no exceptions, so handlers validate
## inputs and EARLY-RETURN a sentinel error dict
## {"__error":"ERR_*","__message":"..."} which `_wrap` converts to the JSON error
## envelope. Mutating ops go through EditorUndoRedoManager so they're undoable and
## mark the scene dirty (§B note). play.*/node.call/resource.* are the exceptions.

const Variants := preload("res://addons/live_bridge/variants.gd")
const NodeUtil := preload("res://addons/live_bridge/nodeutil.gd")

var editor: EditorInterface
var undo_redo: EditorUndoRedoManager


# ============================ entry point ============================

func handle(req: Dictionary) -> Dictionary:
	var id = req.get("id", null)
	var op := str(req.get("op", ""))
	var args: Dictionary = req.get("args", {}) if typeof(req.get("args")) == TYPE_DICTIONARY else {}
	match op:
		"ping":             return _ok(id, {"pong": true, "godot_version": Engine.get_version_info()})
		"auth":             return _ok(id, {"authed": true})   # already gated in server; idempotent
		"editor.info":      return _wrap(id, func(): return _editor_info())
		"node.get_tree":    return _wrap(id, func(): return _get_tree(args))
		"node.add":         return _wrap(id, func(): return _node_add(args))
		"node.delete":      return _wrap(id, func(): return _node_delete(args))
		"node.move":        return _wrap(id, func(): return _node_move(args))
		"node.reparent":    return _wrap(id, func(): return _node_reparent(args))
		"node.rename":      return _wrap(id, func(): return _node_rename(args))
		"node.duplicate":   return _wrap(id, func(): return _node_duplicate(args))
		"node.set_prop":    return _wrap(id, func(): return _node_set_prop(args))
		"node.get_prop":    return _wrap(id, func(): return _node_get_prop(args))
		"node.call":        return _wrap(id, func(): return _node_call(args))
		"node.list_props":  return _wrap(id, func(): return _node_list_props(args))
		"signal.connect":   return _wrap(id, func(): return _signal_connect(args))
		"signal.disconnect": return _wrap(id, func(): return _signal_disconnect(args))
		"signal.list":      return _wrap(id, func(): return _signal_list(args))
		"scene.instance":   return _wrap(id, func(): return _scene_instance(args))
		"scene.open":       return _wrap(id, func(): return _scene_open(args))
		"scene.save":       return _wrap(id, func(): return _scene_save(args))
		"scene.new":        return _wrap(id, func(): return _scene_new(args))
		"scene.close":      return _wrap(id, func(): return _scene_close(args))
		"selection.get":    return _wrap(id, func(): return {"paths": _selection_paths()})
		"selection.set":    return _wrap(id, func(): return _selection_set(args))
		"play.run":         return _wrap(id, func(): return _play_run(args))
		"play.stop":        return _wrap(id, func(): return _play_stop())
		"play.status":      return _wrap(id, func(): return _play_status())
		"undo":             return _wrap(id, func(): return _undo(args))
		"redo":             return _wrap(id, func(): return _redo(args))
		"resource.load":    return _wrap(id, func(): return _resource_load(args))
		"resource.save":    return _wrap(id, func(): return _resource_save(args))
		"fs.scan":          return _wrap(id, func(): return _fs_scan())
		_:                  return _err(id, "ERR_UNKNOWN_OP", "Unknown op: %s" % op)


# ============================ resolution helpers ============================

## Sentinel-error constructor (handlers early-return these; _wrap unwraps).
static func _e(code: String, message: String) -> Dictionary:
	return {"__error": code, "__message": message}

static func _is_err(v) -> bool:
	return typeof(v) == TYPE_DICTIONARY and v.has("__error")


## Returns the edited scene root, or a sentinel ERR_NO_SCENE.
func _root_or_err():
	var r := editor.get_edited_scene_root()
	if r == null:
		return _e("ERR_NO_SCENE", "No scene is open in the editor.")
	return r


## Resolve a NodePath (relative to the scene root; "." or "" is the root).
## Returns the Node or a sentinel ERR_NODE_NOT_FOUND / ERR_NO_SCENE.
func _resolve(path_str: String):
	var root = _root_or_err()
	if _is_err(root):
		return root
	if path_str == "." or path_str == "":
		return root
	var n := (root as Node).get_node_or_null(NodePath(path_str))
	if n == null:
		return _e("ERR_NODE_NOT_FOUND", "Node not found: %s" % path_str)
	return n


# ============================ node CRUD ============================

func _node_add(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var parent = _resolve(str(args.get("parent", ".")))
	if _is_err(parent): return parent
	var type := str(args.get("type", ""))
	if type == "":
		return _e("ERR_BAD_ARGS", "Missing 'type'.")
	if not ClassDB.class_exists(type) or not ClassDB.can_instantiate(type):
		return _e("ERR_BAD_ARGS", "Type does not exist or cannot be instantiated: %s" % type)
	var node: Node = ClassDB.instantiate(type)
	if node == null:
		return _e("ERR_BAD_ARGS", "Failed to instantiate type: %s" % type)
	if args.has("name") and str(args["name"]) != "":
		node.name = str(args["name"])

	undo_redo.create_action("LiveBridge: add %s" % type)
	undo_redo.add_do_method(parent, "add_child", node)
	# OWNER MUST be the edited scene root, or the node is NOT saved into the .tscn.
	undo_redo.add_do_method(node, "set_owner", root)
	undo_redo.add_do_reference(node)                  # keep new node alive across undo
	undo_redo.add_undo_method(parent, "remove_child", node)
	undo_redo.commit_action()                         # commit executes the do-methods now

	# apply optional initial properties (separate, also undoable)
	if args.has("props") and typeof(args["props"]) == TYPE_DICTIONARY:
		for k in args["props"]:
			var r = _set_one_prop(node, str(k), args["props"][k])
			if _is_err(r): return r

	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(node)), "name": node.name}


func _node_delete(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	if node == root:
		return _e("ERR_BAD_ARGS", "Cannot delete the scene root.")
	var parent := (node as Node).get_parent()
	var idx := (node as Node).get_index()
	undo_redo.create_action("LiveBridge: delete %s" % node.name)
	undo_redo.add_do_method(parent, "remove_child", node)
	undo_redo.add_do_method(node, "queue_free")       # free on do, NOT on undo
	undo_redo.add_undo_method(parent, "add_child", node)
	undo_redo.add_undo_method(node, "set_owner", root)
	undo_redo.add_undo_method(parent, "move_child", node, idx)
	undo_redo.add_undo_reference(node)                # keep alive for redo
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"deleted": str(args.get("path", ""))}


func _node_move(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var parent := (node as Node).get_parent()
	if parent == null:
		return _e("ERR_BAD_ARGS", "Cannot move the scene root.")
	var to_index := int(args.get("to_index", 0))
	var old_index := (node as Node).get_index()
	undo_redo.create_action("LiveBridge: move %s" % node.name)
	undo_redo.add_do_method(parent, "move_child", node, to_index)
	undo_redo.add_undo_method(parent, "move_child", node, old_index)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(node)), "index": to_index}


func _node_reparent(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var new_parent = _resolve(str(args.get("new_parent", "")))
	if _is_err(new_parent): return new_parent
	var old_parent := (node as Node).get_parent()
	if old_parent == null:
		return _e("ERR_BAD_ARGS", "Cannot reparent the scene root.")
	undo_redo.create_action("LiveBridge: reparent %s" % node.name)
	undo_redo.add_do_method(old_parent, "remove_child", node)
	undo_redo.add_do_method(new_parent, "add_child", node)
	undo_redo.add_do_method(NodeUtil, "own_recursive", node, root)
	undo_redo.add_undo_method(new_parent, "remove_child", node)
	undo_redo.add_undo_method(old_parent, "add_child", node)
	undo_redo.add_undo_method(NodeUtil, "own_recursive", node, root)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(node))}


func _node_rename(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var new_name := str(args.get("name", ""))
	if new_name == "":
		return _e("ERR_BAD_ARGS", "Missing 'name'.")
	var old_name := (node as Node).name
	undo_redo.create_action("LiveBridge: rename %s" % old_name)
	undo_redo.add_do_property(node, "name", new_name)
	undo_redo.add_undo_property(node, "name", old_name)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(node))}


func _node_duplicate(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var src = _resolve(str(args.get("path", "")))
	if _is_err(src): return src
	if src == root:
		return _e("ERR_BAD_ARGS", "Cannot duplicate the scene root.")
	var parent_path := str(args.get("new_parent", str((root as Node).get_path_to((src as Node).get_parent()))))
	var parent = _resolve(parent_path)
	if _is_err(parent): return parent
	var dup := (src as Node).duplicate()
	if args.has("name") and str(args["name"]) != "":
		dup.name = str(args["name"])
	undo_redo.create_action("LiveBridge: duplicate %s" % src.name)
	undo_redo.add_do_method(parent, "add_child", dup)
	undo_redo.add_do_method(NodeUtil, "own_recursive", dup, root)
	undo_redo.add_do_reference(dup)
	undo_redo.add_undo_method(parent, "remove_child", dup)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(dup))}


# ============================ properties ============================

func _node_set_prop(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	if not args.has("path") or not args.has("prop"):
		return _e("ERR_BAD_ARGS", "Missing 'path' or 'prop'.")
	var node = _resolve(str(args["path"]))
	if _is_err(node): return node
	var prop := str(args["prop"])
	var r = _set_one_prop(node, prop, args.get("value"))
	if _is_err(r): return r
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(node)), "prop": prop}


## Coerce json_value into the node's declared property type and set it, undoably.
## Returns null on success or a sentinel error dict.
func _set_one_prop(obj: Object, prop: String, json_value):
	var hint_type := Variants.declared_type(obj, prop)
	var value = Variants.from_json_variant(json_value, hint_type, editor)
	if _is_err(value):
		return value
	var old_value = obj.get_indexed(NodePath(prop))    # supports "a:b:c" sub-paths
	undo_redo.create_action("LiveBridge: set %s.%s" % [obj, prop])
	undo_redo.add_do_property(obj, prop, value)
	undo_redo.add_undo_property(obj, prop, old_value)
	undo_redo.commit_action()
	return null


func _node_get_prop(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	if not args.has("path") or not args.has("prop"):
		return _e("ERR_BAD_ARGS", "Missing 'path' or 'prop'.")
	var node = _resolve(str(args["path"]))
	if _is_err(node): return node
	var prop := str(args["prop"])
	# Validate the (top-level) property exists to give a clean error.
	if Variants.declared_type(node, prop) == -1 and not (node as Object).has_method("get_" + prop):
		# Fall back to attempting get_indexed; only error if truly absent.
		var found := false
		var head := prop.split(":")[0]
		for p in (node as Object).get_property_list():
			if p.name == head:
				found = true
				break
		if not found:
			return _e("ERR_PROP_NOT_FOUND", "Property not found: %s" % prop)
	var value = (node as Object).get_indexed(NodePath(prop))
	return {"value": Variants.to_json_variant(value, root)}


func _node_call(args: Dictionary):
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var method := str(args.get("method", ""))
	if method == "":
		return _e("ERR_BAD_ARGS", "Missing 'method'.")
	if not (node as Object).has_method(method):
		return _e("ERR_BAD_ARGS", "Node has no method: %s" % method)
	var call_args := []
	for a in args.get("args", []):
		call_args.append(Variants.from_json_variant(a, -1, editor))
	var value = (node as Object).callv(method, call_args)
	return {"value": Variants.to_json_variant(value, editor.get_edited_scene_root())}


func _node_list_props(args: Dictionary):
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var usage := str(args.get("usage", ""))
	var out := []
	for p in (node as Object).get_property_list():
		if usage == "storage" and (int(p.usage) & PROPERTY_USAGE_STORAGE) == 0:
			continue
		if usage == "editor" and (int(p.usage) & PROPERTY_USAGE_EDITOR) == 0:
			continue
		out.append({
			"name": p.name,
			"type": int(p.type),
			"type_name": type_string(int(p.type)),
			"usage": int(p.usage),
			"class_name": str(p.get("class_name", "")),
			"hint_string": str(p.get("hint_string", "")),
		})
	return {"props": out}


# ============================ signals ============================

func _signal_connect(args: Dictionary):
	var from = _resolve(str(args.get("from", "")))
	if _is_err(from): return from
	var to = _resolve(str(args.get("to", "")))
	if _is_err(to): return to
	var sig := str(args.get("signal", ""))
	var method := str(args.get("method", ""))
	if sig == "" or method == "":
		return _e("ERR_BAD_ARGS", "Missing 'signal' or 'method'.")
	if not (from as Object).has_signal(sig):
		return _e("ERR_SIGNAL", "Source node has no signal: %s" % sig)
	# CONNECT_PERSIST makes the editor serialize the connection into the .tscn.
	var flags := int(args.get("flags", Object.CONNECT_PERSIST))
	var callable := Callable(to, method)
	if (from as Object).is_connected(sig, callable):
		return _e("ERR_SIGNAL", "Already connected: %s -> %s.%s" % [sig, to.name, method])
	undo_redo.create_action("LiveBridge: connect %s.%s" % [from.name, sig])
	undo_redo.add_do_method(from, "connect", sig, callable, flags)
	undo_redo.add_undo_method(from, "disconnect", sig, callable)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"ok": true}


func _signal_disconnect(args: Dictionary):
	var from = _resolve(str(args.get("from", "")))
	if _is_err(from): return from
	var to = _resolve(str(args.get("to", "")))
	if _is_err(to): return to
	var sig := str(args.get("signal", ""))
	var method := str(args.get("method", ""))
	if sig == "" or method == "":
		return _e("ERR_BAD_ARGS", "Missing 'signal' or 'method'.")
	var callable := Callable(to, method)
	if not (from as Object).is_connected(sig, callable):
		return _e("ERR_SIGNAL", "Not connected: %s -> %s.%s" % [sig, to.name, method])
	undo_redo.create_action("LiveBridge: disconnect %s.%s" % [from.name, sig])
	undo_redo.add_do_method(from, "disconnect", sig, callable)
	undo_redo.add_undo_method(from, "connect", sig, callable, Object.CONNECT_PERSIST)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"ok": true}


func _signal_list(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var node = _resolve(str(args.get("path", "")))
	if _is_err(node): return node
	var sigs := []
	for s in (node as Object).get_signal_list():
		sigs.append(str(s.name))
	var conns := []
	for s in (node as Object).get_signal_list():
		for c in (node as Object).get_signal_connection_list(s.name):
			var target = c.get("callable")
			var target_obj = target.get_object() if target is Callable else null
			var target_path = ""
			if target_obj is Node:
				target_path = str((root as Node).get_path_to(target_obj))
			conns.append({
				"signal": str(s.name),
				"to": target_path,
				"method": str(target.get_method()) if target is Callable else "",
				"flags": int(c.get("flags", 0)),
			})
	return {"signals": sigs, "connections": conns}


# ============================ scenes ============================

func _scene_instance(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var parent = _resolve(str(args.get("parent", ".")))
	if _is_err(parent): return parent
	if not args.has("scene"):
		return _e("ERR_BAD_ARGS", "Missing 'scene'.")
	var packed = load(str(args["scene"])) as PackedScene
	if packed == null:
		return _e("ERR_LOAD", "Failed to load PackedScene: %s" % args["scene"])
	var inst := packed.instantiate()
	if inst == null:
		return _e("ERR_LOAD", "Failed to instantiate scene: %s" % args["scene"])
	inst.scene_file_path = str(args["scene"])
	if args.has("name") and str(args["name"]) != "":
		inst.name = str(args["name"])
	undo_redo.create_action("LiveBridge: instance %s" % args["scene"])
	undo_redo.add_do_method(parent, "add_child", inst)
	# Instanced scene: only the ROOT gets owner = edited root; internals keep
	# their own scene as owner (do NOT own_recursive). See §F.1.
	undo_redo.add_do_method(NodeUtil, "own_instance", inst, root)
	undo_redo.add_do_reference(inst)
	undo_redo.add_undo_method(parent, "remove_child", inst)
	undo_redo.commit_action()
	editor.mark_scene_as_unsaved()
	return {"path": str((root as Node).get_path_to(inst))}


func _scene_open(args: Dictionary):
	if not args.has("path"):
		return _e("ERR_BAD_ARGS", "Missing 'path'.")
	if not FileAccess.file_exists(str(args["path"])):
		return _e("ERR_LOAD", "Scene file does not exist: %s" % args["path"])
	editor.open_scene_from_path(str(args["path"]))
	# async; client should poll editor.info / node.get_tree
	return {"root": null, "opening": str(args["path"])}


func _scene_save(args: Dictionary):
	var r := editor.get_edited_scene_root()
	if r == null:
		return _e("ERR_NO_SCENE", "No scene is open to save.")
	if args.has("path") and str(args["path"]) != "":
		editor.save_scene_as(str(args["path"]))      # returns void
		return {"path": str(args["path"])}
	var err := editor.save_scene()                    # returns Error
	if err != OK:
		return _e("ERR_IO", "save_scene failed (err %d). Scene may need a path; use save with a path." % err)
	return {"path": r.scene_file_path}


func _scene_new(args: Dictionary):
	var root_type := str(args.get("root_type", "Node"))
	if not ClassDB.class_exists(root_type) or not ClassDB.can_instantiate(root_type):
		return _e("ERR_BAD_ARGS", "Invalid root_type: %s" % root_type)
	# EditorInterface has no direct "new empty scene" API across 4.x; build a
	# PackedScene on disk and open it. Use a temp path under res://.
	var name := str(args.get("name", root_type))
	var node: Node = ClassDB.instantiate(root_type)
	node.name = name
	var packed := PackedScene.new()
	if packed.pack(node) != OK:
		return _e("ERR_INTERNAL", "Failed to pack new scene root.")
	var tmp_path := "res://__live_new_%d.tscn" % Time.get_ticks_msec()
	var err := ResourceSaver.save(packed, tmp_path)
	if err != OK:
		return _e("ERR_IO", "Failed to save new scene (err %d)." % err)
	editor.get_resource_filesystem().scan()
	editor.open_scene_from_path(tmp_path)
	return {"root": tmp_path}


func _scene_close(_args: Dictionary):
	# No stable public "close current tab" API across 4.2-4.5; report limitation.
	return _e("ERR_UNKNOWN_OP", "scene.close is not supported by the 4.3 EditorInterface API.")


# ============================ selection ============================

func _selection_paths() -> Array:
	var root := editor.get_edited_scene_root()
	var out := []
	if root == null:
		return out
	for n in editor.get_selection().get_selected_nodes():
		out.append(str(root.get_path_to(n)))
	return out


func _selection_set(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var sel := editor.get_selection()
	sel.clear()
	var first: Node = null
	for p in args.get("paths", []):
		var n = _resolve(str(p))
		if _is_err(n): return n
		sel.add_node(n)
		if first == null:
			first = n
	if first != null:
		editor.edit_node(first)
	return {"paths": _selection_paths()}


# ============================ play ============================

func _play_run(args: Dictionary):
	if args.has("scene") and str(args["scene"]) != "":
		editor.play_custom_scene(str(args["scene"]))
	else:
		editor.play_current_scene()
	return {"playing": true}


func _play_stop():
	editor.stop_playing_scene()
	return {"playing": false}


func _play_status():
	var scene := ""
	var r := editor.get_edited_scene_root()
	if r != null:
		scene = r.scene_file_path
	return {"playing": editor.is_playing_scene(), "scene": scene}


# ============================ undo / redo ============================
# 4.3 NOTE: undo-history routing changed in 4.3. A default-history commit_action()
# lands the action in the SCENE's object history (the history of the edited scene
# root), NOT the GLOBAL history. So to undo bridge edits we must drive the scene
# root's object history. We try that first, then fall back to GLOBAL. See §F.4.
# Limitation: undo operates on the active scene's history, matching where our
# edits land; cross-scene global undo is intentionally not attempted.

func _scene_history():
	var root := editor.get_edited_scene_root()
	if root != null:
		var hid := undo_redo.get_object_history_id(root)
		var h := undo_redo.get_history_undo_redo(hid)
		if h != null:
			return h
	return undo_redo.get_history_undo_redo(EditorUndoRedoManager.GLOBAL_HISTORY)


func _undo(args: Dictionary):
	var n := int(args.get("count", 1))
	var hist = _scene_history()
	var done := 0
	for i in n:
		if hist != null and hist.has_undo():
			hist.undo()
			done += 1
		else:
			break
	return {"undone": done}


func _redo(args: Dictionary):
	var n := int(args.get("count", 1))
	var hist = _scene_history()
	var done := 0
	for i in n:
		if hist != null and hist.has_redo():
			hist.redo()
			done += 1
		else:
			break
	return {"redone": done}


# ============================ resources ============================

func _resource_load(args: Dictionary):
	if not args.has("path"):
		return _e("ERR_BAD_ARGS", "Missing 'path'.")
	var res = load(str(args["path"]))
	if res == null:
		return _e("ERR_LOAD", "Failed to load resource: %s" % args["path"])
	return {"ref": Variants.to_json_variant(res, editor.get_edited_scene_root())}


func _resource_save(args: Dictionary):
	if not args.has("path"):
		return _e("ERR_BAD_ARGS", "Missing 'path'.")
	var path := str(args["path"])
	var res: Resource = null
	if FileAccess.file_exists(path):
		res = load(path)
	if res == null:
		# Build a new inline resource from props' class, or a plain Resource.
		var cls := str(args.get("class", "Resource"))
		if not ClassDB.class_exists(cls) or not ClassDB.can_instantiate(cls):
			return _e("ERR_BAD_ARGS", "Invalid resource class: %s" % cls)
		res = ClassDB.instantiate(cls)
	if args.has("props") and typeof(args["props"]) == TYPE_DICTIONARY:
		for k in args["props"]:
			var v = Variants.from_json_variant(args["props"][k], -1, editor)
			if _is_err(v): return v
			res.set_indexed(NodePath(str(k)), v)
	var err := ResourceSaver.save(res, path)
	if err != OK:
		return _e("ERR_IO", "ResourceSaver.save failed (err %d)." % err)
	editor.get_resource_filesystem().scan()
	return {"path": path}


func _fs_scan():
	editor.get_resource_filesystem().scan()
	return {"ok": true}


# ============================ introspection ============================

func _editor_info():
	var r := editor.get_edited_scene_root()
	return {
		"version": Engine.get_version_info(),
		"edited_scene": r.scene_file_path if r else null,
		"open_scenes": Array(editor.get_open_scenes()),
		"main_screen": "",   # no stable getter across 4.x; left blank
		"playing": editor.is_playing_scene(),
	}


func _get_tree(args: Dictionary):
	var root = _root_or_err()
	if _is_err(root): return root
	var from = _resolve(str(args.get("from", ".")))
	if _is_err(from): return from
	var depth := int(args.get("depth", -1))
	var props := []
	if args.has("props") and typeof(args["props"]) == TYPE_ARRAY:
		props = args["props"]
	return _node_to_dict(from, root, depth, props)


func _node_to_dict(node: Node, root: Node, depth: int, props: Array) -> Dictionary:
	var d := {
		"name": node.name,
		"type": node.get_class(),
		"path": str(root.get_path_to(node)),
		"scene_file_path": node.scene_file_path,   # non-empty => instanced scene
		"children": [],
	}
	if not props.is_empty():
		var pd := {}
		for p in props:
			var pname := str(p)
			if Variants.declared_type(node, pname) != -1:
				pd[pname] = Variants.to_json_variant(node.get_indexed(NodePath(pname)), root)
		d["props"] = pd
	if depth != 0:
		for c in node.get_children():
			d["children"].append(_node_to_dict(c, root, depth - 1, props))
	return d


# ============================ envelope helpers ============================

func _ok(id, result) -> Dictionary:
	return {"id": id, "ok": true, "result": result}


func _err(id, code, msg) -> Dictionary:
	return {"id": id, "ok": false, "error": {"code": code, "message": msg}}


## Run a handler. Handlers validate inputs and early-return a sentinel
## {"__error":..., "__message":...} on failure (GDScript has no exceptions);
## this converts that to the JSON error envelope, else wraps the result in _ok.
func _wrap(id, fn: Callable) -> Dictionary:
	var result = fn.call()
	if typeof(result) == TYPE_DICTIONARY and result.has("__error"):
		return _err(id, result["__error"], result.get("__message", ""))
	return _ok(id, result)
