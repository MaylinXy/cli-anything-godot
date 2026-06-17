@tool
extends RefCounted
## Live Bridge transport — TCPServer + WebSocketPeer, polled from _process.
##
## A small-N, localhost-only WebSocket server. One JSON object per text frame,
## one request -> one response (correlated by `id`). All work happens on the
## editor main thread because poll() is driven from EditorPlugin._process.
## See SPEC-live-bridge.md §B.1 / §3.

var token: String = ""
var on_request: Callable             # func(req: Dictionary) -> Dictionary

var _tcp := TCPServer.new()
var _peers: Array[WebSocketPeer] = []
var _authed := {}                    # WebSocketPeer -> bool


func start(host: String, port: int) -> int:
	return _tcp.listen(port, host)   # bind to 127.0.0.1 (passed by plugin.gd)


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

	# --- auth gate: first frame must be {"op":"auth","args":{"token":...}} ---
	if not bool(_authed.get(ws, false)):
		var auth_args = req.get("args", {})
		var supplied := ""
		if typeof(auth_args) == TYPE_DICTIONARY:
			supplied = str(auth_args.get("token", ""))
		if str(req.get("op", "")) == "auth" and supplied == token:
			_authed[ws] = true
			_reply(ws, {"id": id, "ok": true, "result": {"authed": true}})
		else:
			_reply(ws, _err(id, "ERR_AUTH", "Authentication required."))
		return

	# --- dispatch (runs synchronously on the editor main thread) ---
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
