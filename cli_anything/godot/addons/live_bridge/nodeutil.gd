@tool
extends RefCounted
## Live Bridge node helpers — ownership, add/reparent/duplicate/instance.
##
## OWNERSHIP IS CRITICAL: a node added to the tree without
## `owner = get_edited_scene_root()` shows up live but is silently dropped from
## the .tscn on save. See SPEC-live-bridge.md §F.1.


## Set owner on `node` AND all descendants that belong to the edited scene.
## A child that is the ROOT of an instanced sub-scene (non-empty
## scene_file_path) is owned by us, but we do NOT descend into its internals —
## those keep their own scene as owner.
static func own_recursive(node: Node, scene_root: Node) -> void:
	if node == null or scene_root == null:
		return
	if node != scene_root:
		node.owner = scene_root
	for c in node.get_children():
		if c.scene_file_path != "":
			# Instance root: own it, but stop — do not re-own instance internals.
			c.owner = scene_root
		else:
			own_recursive(c, scene_root)


## Own only the root of an instanced sub-scene (never its internals).
static func own_instance(inst: Node, scene_root: Node) -> void:
	if inst != null and scene_root != null and inst != scene_root:
		inst.owner = scene_root
