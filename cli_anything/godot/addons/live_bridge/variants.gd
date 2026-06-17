@tool
extends RefCounted
## Live Bridge Variant <-> JSON coercion (the crux). See SPEC-live-bridge.md §C.
##
## JSON has ~6 types; Godot has ~38 Variants. Convention: any JSON object with a
## "__type" key is a typed Variant (tag-driven, always wins). Untagged JSON maps
## 1:1, with target-driven coercion (using a property's declared type) for loose
## scalars/arrays. `var_to_str`/`str_to_var` ({"__type":"GDS"}) is the escape hatch.
##
## Serialization (to_json_variant) is the symmetric inverse, with a depth/cycle
## guard so scene/Resource back-references can't recurse forever.


# ============================ declared type ============================

## Declared Variant type of `prop` on `obj` (from get_property_list), or -1.
## For sub-paths "a:b:c" we resolve the cheap top-level type only.
static func declared_type(obj: Object, prop: String) -> int:
	if obj == null:
		return -1
	var head := prop.split(":")[0]
	for p in obj.get_property_list():
		if p.name == head:
			return int(p.type)
	return -1


# ============================ JSON -> Variant ============================

static func from_json_variant(v, hint_type: int = -1, editor: EditorInterface = null):
	# 1) tagged object always wins
	if typeof(v) == TYPE_DICTIONARY and v.has("__type"):
		return _from_tagged(v, editor)
	# 2) plain dict recurses
	if typeof(v) == TYPE_DICTIONARY:
		var d := {}
		for k in v:
			d[k] = from_json_variant(v[k], -1, editor)
		return d
	# 3) array: allow loose [x,y] -> math type when the target wants one
	if typeof(v) == TYPE_ARRAY:
		var coerced = _loose_array_to_type(v, hint_type)
		if coerced != null:
			return coerced
		var arr := []
		for e in v:
			arr.append(from_json_variant(e, -1, editor))
		return arr
	# 4) target-driven coercion of loose scalars (e.g. "#ff0000" -> Color)
	if hint_type != -1:
		var c = _loose_scalar_to_type(v, hint_type)
		if c != null:
			return c
	# 5) primitives pass through (ints stay int, floats stay float)
	return v


static func _from_tagged(v: Dictionary, editor: EditorInterface):
	var t := str(v["__type"])
	match t:
		"Vector2":  return Vector2(_f(v, "x"), _f(v, "y"))
		"Vector2i": return Vector2i(int(_f(v, "x")), int(_f(v, "y")))
		"Vector3":  return Vector3(_f(v, "x"), _f(v, "y"), _f(v, "z"))
		"Vector3i": return Vector3i(int(_f(v, "x")), int(_f(v, "y")), int(_f(v, "z")))
		"Vector4":  return Vector4(_f(v, "x"), _f(v, "y"), _f(v, "z"), _f(v, "w"))
		"Vector4i": return Vector4i(int(_f(v, "x")), int(_f(v, "y")), int(_f(v, "z")), int(_f(v, "w")))
		"Color":
			if v.has("html"):
				return Color.html(str(v["html"]))
			return Color(_f(v, "r"), _f(v, "g"), _f(v, "b"), float(v.get("a", 1.0)))
		"Rect2":    return Rect2(_f(v, "x"), _f(v, "y"), _f(v, "w"), _f(v, "h"))
		"Rect2i":   return Rect2i(int(_f(v, "x")), int(_f(v, "y")), int(_f(v, "w")), int(_f(v, "h")))
		"Quaternion": return Quaternion(_f(v, "x"), _f(v, "y"), _f(v, "z"), _f(v, "w"))
		"Basis":
			if v.has("rows"):
				var r = v["rows"]
				return Basis(
					Vector3(r[0][0], r[0][1], r[0][2]),
					Vector3(r[1][0], r[1][1], r[1][2]),
					Vector3(r[2][0], r[2][1], r[2][2]))
			return Basis()
		"Transform2D":
			return Transform2D(_xy(v.get("x", [1, 0])), _xy(v.get("y", [0, 1])), _xy(v.get("origin", [0, 0])))
		"Transform3D":
			var b = from_json_variant(v.get("basis", {"__type": "Basis"}), -1, editor)
			var o = from_json_variant(v.get("origin", {"__type": "Vector3"}), -1, editor)
			return Transform3D(b, o)
		"Plane":    return Plane(from_json_variant(v["normal"], -1, editor), float(v.get("d", 0.0)))
		"AABB":     return AABB(from_json_variant(v["position"], -1, editor), from_json_variant(v["size"], -1, editor))
		"NodePath": return NodePath(str(v["path"]))
		"StringName": return StringName(str(v["name"]))
		"RID":      return {"__error": "ERR_COERCE", "__message": "RID is process-local and not transferable."}
		"PackedByteArray":   return Marshalls.base64_to_raw(str(v["b64"]))
		"PackedInt32Array":  return PackedInt32Array(v["data"])
		"PackedInt64Array":  return PackedInt64Array(v["data"])
		"PackedFloat32Array": return PackedFloat32Array(v["data"])
		"PackedFloat64Array": return PackedFloat64Array(v["data"])
		"PackedStringArray": return PackedStringArray(v["data"])
		"PackedColorArray":
			var pc := PackedColorArray()
			for e in v["data"]:
				pc.append(from_json_variant(e, TYPE_COLOR, editor))
			return pc
		"PackedVector2Array":
			var pv := PackedVector2Array()
			for e in v["data"]:
				pv.append(_xy(e))
			return pv
		"PackedVector3Array":
			var pv3 := PackedVector3Array()
			for e in v["data"]:
				pv3.append(_xyz(e))
			return pv3
		"Resource", "PackedScene":
			if v.has("path"):
				var res = load(str(v["path"]))
				if res == null:
					return {"__error": "ERR_LOAD", "__message": "Failed to load resource: %s" % v["path"]}
				return res
			if v.has("inline"):
				return _build_inline_resource(v["inline"], editor)
			return null
		"Enum":
			if not ClassDB.class_exists(str(v["class"])):
				return {"__error": "ERR_COERCE", "__message": "Unknown enum class: %s" % v["class"]}
			return ClassDB.class_get_integer_constant(str(v["class"]), str(v["name"]))
		"GDS":
			return str_to_var(str(v["text"]))   # universal fallback
		_:
			return {"__error": "ERR_COERCE", "__message": "Unknown __type: %s" % t}


static func _build_inline_resource(spec: Dictionary, editor: EditorInterface):
	var cls := str(spec.get("class", "Resource"))
	if not ClassDB.class_exists(cls) or not ClassDB.can_instantiate(cls):
		return {"__error": "ERR_COERCE", "__message": "Cannot instantiate resource class: %s" % cls}
	var res = ClassDB.instantiate(cls)
	if not (res is Resource):
		return {"__error": "ERR_COERCE", "__message": "%s is not a Resource." % cls}
	for k in spec.get("props", {}):
		res.set_indexed(NodePath(str(k)), from_json_variant(spec["props"][k], -1, editor))
	return res


static func _f(d: Dictionary, key: String) -> float:
	return float(d.get(key, 0.0))


static func _xy(e) -> Vector2:
	if typeof(e) == TYPE_ARRAY:
		return Vector2(e[0], e[1])
	if typeof(e) == TYPE_DICTIONARY:
		return Vector2(e.get("x", 0), e.get("y", 0))
	return Vector2()


static func _xyz(e) -> Vector3:
	if typeof(e) == TYPE_ARRAY:
		return Vector3(e[0], e[1], e[2])
	if typeof(e) == TYPE_DICTIONARY:
		return Vector3(e.get("x", 0), e.get("y", 0), e.get("z", 0))
	return Vector3()


static func _loose_scalar_to_type(v, t: int):
	match t:
		TYPE_COLOR:
			if typeof(v) == TYPE_STRING:
				return Color.html(v)
		TYPE_INT:
			return int(v)
		TYPE_FLOAT:
			return float(v)
		TYPE_STRING:
			return str(v)
		TYPE_STRING_NAME:
			return StringName(str(v))
		TYPE_NODE_PATH:
			return NodePath(str(v))
		TYPE_BOOL:
			return bool(v)
	return null


static func _loose_array_to_type(v: Array, t: int):
	match t:
		TYPE_VECTOR2:  return Vector2(v[0], v[1])
		TYPE_VECTOR2I: return Vector2i(int(v[0]), int(v[1]))
		TYPE_VECTOR3:  return Vector3(v[0], v[1], v[2])
		TYPE_VECTOR3I: return Vector3i(int(v[0]), int(v[1]), int(v[2]))
		TYPE_VECTOR4:  return Vector4(v[0], v[1], v[2], v[3])
		TYPE_COLOR:    return Color(v[0], v[1], v[2], v[3] if v.size() > 3 else 1.0)
	return null


# ============================ Variant -> JSON ============================

static func to_json_variant(value, scene_root: Node = null, depth: int = 8):
	if depth <= 0:
		return {"__type": "GDS", "text": var_to_str(value)}
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return value
		TYPE_STRING_NAME: return {"__type": "StringName", "name": str(value)}
		TYPE_VECTOR2:  return {"__type": "Vector2", "x": value.x, "y": value.y}
		TYPE_VECTOR2I: return {"__type": "Vector2i", "x": value.x, "y": value.y}
		TYPE_VECTOR3:  return {"__type": "Vector3", "x": value.x, "y": value.y, "z": value.z}
		TYPE_VECTOR3I: return {"__type": "Vector3i", "x": value.x, "y": value.y, "z": value.z}
		TYPE_VECTOR4:  return {"__type": "Vector4", "x": value.x, "y": value.y, "z": value.z, "w": value.w}
		TYPE_VECTOR4I: return {"__type": "Vector4i", "x": value.x, "y": value.y, "z": value.z, "w": value.w}
		TYPE_COLOR:    return {"__type": "Color", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
		TYPE_RECT2:    return {"__type": "Rect2", "x": value.position.x, "y": value.position.y, "w": value.size.x, "h": value.size.y}
		TYPE_RECT2I:   return {"__type": "Rect2i", "x": value.position.x, "y": value.position.y, "w": value.size.x, "h": value.size.y}
		TYPE_QUATERNION: return {"__type": "Quaternion", "x": value.x, "y": value.y, "z": value.z, "w": value.w}
		TYPE_PLANE:    return {"__type": "Plane", "normal": {"__type": "Vector3", "x": value.normal.x, "y": value.normal.y, "z": value.normal.z}, "d": value.d}
		TYPE_AABB:     return {"__type": "AABB", "position": to_json_variant(value.position), "size": to_json_variant(value.size)}
		TYPE_BASIS:    return {"__type": "Basis", "rows": [[value.x.x, value.y.x, value.z.x], [value.x.y, value.y.y, value.z.y], [value.x.z, value.y.z, value.z.z]]}
		TYPE_TRANSFORM2D: return {"__type": "Transform2D", "x": [value.x.x, value.x.y], "y": [value.y.x, value.y.y], "origin": [value.origin.x, value.origin.y]}
		TYPE_TRANSFORM3D: return {"__type": "Transform3D", "basis": to_json_variant(value.basis), "origin": to_json_variant(value.origin)}
		TYPE_NODE_PATH: return {"__type": "NodePath", "path": str(value)}
		TYPE_RID:       return {"__type": "GDS", "text": var_to_str(value)}
		TYPE_DICTIONARY:
			var d := {}
			for k in value:
				d[str(k)] = to_json_variant(value[k], scene_root, depth - 1)
			return d
		TYPE_ARRAY:
			var a := []
			for e in value:
				a.append(to_json_variant(e, scene_root, depth - 1))
			return a
		TYPE_PACKED_BYTE_ARRAY:
			return {"__type": "PackedByteArray", "b64": Marshalls.raw_to_base64(value)}
		TYPE_PACKED_INT32_ARRAY:   return {"__type": "PackedInt32Array", "data": Array(value)}
		TYPE_PACKED_INT64_ARRAY:   return {"__type": "PackedInt64Array", "data": Array(value)}
		TYPE_PACKED_FLOAT32_ARRAY: return {"__type": "PackedFloat32Array", "data": Array(value)}
		TYPE_PACKED_FLOAT64_ARRAY: return {"__type": "PackedFloat64Array", "data": Array(value)}
		TYPE_PACKED_STRING_ARRAY:  return {"__type": "PackedStringArray", "data": Array(value)}
		TYPE_PACKED_VECTOR2_ARRAY:
			var pv := []
			for e in value:
				pv.append({"x": e.x, "y": e.y})
			return {"__type": "PackedVector2Array", "data": pv}
		TYPE_PACKED_VECTOR3_ARRAY:
			var pv3 := []
			for e in value:
				pv3.append({"x": e.x, "y": e.y, "z": e.z})
			return {"__type": "PackedVector3Array", "data": pv3}
		TYPE_PACKED_COLOR_ARRAY:
			var pc := []
			for e in value:
				pc.append(to_json_variant(e))
			return {"__type": "PackedColorArray", "data": pc}
		TYPE_OBJECT:
			if value == null:
				return null
			if value is Node and scene_root != null:
				return {"__type": "NodePath", "path": str(scene_root.get_path_to(value))}
			if value is Resource:
				if value.resource_path != "":
					return {"__type": "Resource", "path": value.resource_path}
				return {"__type": "Resource", "class": value.get_class(), "inline": true}
			return {"__type": "Object", "class": value.get_class()}
		_:
			return {"__type": "GDS", "text": var_to_str(value)}
