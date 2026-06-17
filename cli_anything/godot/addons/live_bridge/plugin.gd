@tool
extends EditorPlugin
## Live Bridge — EditorPlugin entry point.
##
## Hosts a JSON-over-WebSocket server INSIDE the running editor so an external
## Python client / AI agent can drive live scene editing. The server is polled
## from _process (editor main thread) so every op runs on the main thread for
## free — no locks, no marshaling. See SPEC-live-bridge.md §A / §3.

const Server := preload("res://addons/live_bridge/server.gd")
const Dispatch := preload("res://addons/live_bridge/dispatch.gd")

var _server
var _dispatch


func _enter_tree() -> void:
	var host := str(_setting("host", "127.0.0.1"))          # 127.0.0.1 ONLY by default
	var port := int(_setting("port", 8787))
	# token: ProjectSetting overrides env var LIVE_BRIDGE_TOKEN; empty => no auth.
	var token := str(_setting("token", OS.get_environment("LIVE_BRIDGE_TOKEN")))

	_dispatch = Dispatch.new()
	_dispatch.editor = get_editor_interface()
	_dispatch.undo_redo = get_undo_redo()                    # EditorUndoRedoManager (4.x)

	_server = Server.new()
	_server.token = token
	# The server hands each parsed request to this Callable and expects a reply dict back.
	_server.on_request = func(req: Dictionary) -> Dictionary:
		return _dispatch.handle(req)
	var err: int = _server.start(host, port)
	if err != OK:
		push_error("[live_bridge] server start failed on %s:%d (err %d)" % [host, port, err])
	else:
		print("[live_bridge] listening on %s:%d (auth=%s)" % [host, port, "on" if token != "" else "off"])
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


## Read a `live_bridge/<key>` ProjectSetting, falling back to default_value.
func _setting(key: String, default_value):
	var full := key if key.begins_with("live_bridge/") else "live_bridge/%s" % key
	if ProjectSettings.has_setting(full):
		var v = ProjectSettings.get_setting(full)
		if v != null and str(v) != "":
			return v
	return default_value
