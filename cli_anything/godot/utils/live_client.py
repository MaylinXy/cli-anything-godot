"""Python client for the Godot LIVE editor bridge (WebSocket, JSON ops).

Talks to the in-editor ``addons/live_bridge`` server (TCPServer + WebSocketPeer,
127.0.0.1). One request -> one response, correlated by ``id``. See
SPEC-live-bridge.md §B / §E.

Requires the third-party ``websocket-client`` package (tiny, widely available)::

    pip install websocket-client
"""

from __future__ import annotations

import itertools
import json
from typing import Any

try:
    import websocket  # provided by the `websocket-client` package
except ImportError:  # pragma: no cover
    websocket = None


class LiveBridgeError(RuntimeError):
    """A non-OK response from the bridge ({"ok": false, "error": {...}})."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.bridge_message = message


class LiveBridge:
    """Synchronous client for the Godot live editor bridge."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        token: str | None = None,
        timeout: float = 10.0,
    ):
        if websocket is None:
            raise RuntimeError(
                "The 'websocket-client' package is required for live editor control. "
                "Install it with: pip install websocket-client"
            )
        self.host = host
        self.port = port
        self.url = f"ws://{host}:{port}"
        self.token = token
        self.timeout = timeout
        self._ws = None
        self._ids = itertools.count(1)

    # ---- connection ----
    def connect(self) -> "LiveBridge":
        try:
            self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        except Exception as e:  # ConnectionRefused, timeout, etc.
            raise RuntimeError(
                f"Could not connect to the Godot live bridge at {self.url}. "
                f"Is the editor open with the live_bridge addon enabled? ({e})"
            ) from e
        if self.token:
            self._call("auth", {"token": self.token})
        return self

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- core request/response ----
    def _call(self, op: str, args: dict | None = None) -> Any:
        if self._ws is None:
            raise RuntimeError("LiveBridge is not connected; call connect() first.")
        req_id = f"c-{next(self._ids)}"
        self._ws.send(json.dumps({"id": req_id, "op": op, "args": args or {}}))
        # one-request/one-response; loop to skip any stale frames with a mismatched id
        while True:
            raw = self._ws.recv()
            resp = json.loads(raw)
            if resp.get("id") != req_id:
                continue
            if not resp.get("ok"):
                err = resp.get("error", {}) or {}
                raise LiveBridgeError(err.get("code", "ERR"), err.get("message", ""))
            return resp.get("result")

    # Public escape hatch for arbitrary ops.
    def call(self, op: str, args: dict | None = None) -> Any:
        return self._call(op, args)

    # ---- typed value helpers (build tagged JSON for the coercion bridge) ----
    @staticmethod
    def vec2(x, y):
        return {"__type": "Vector2", "x": x, "y": y}

    @staticmethod
    def vec2i(x, y):
        return {"__type": "Vector2i", "x": x, "y": y}

    @staticmethod
    def vec3(x, y, z):
        return {"__type": "Vector3", "x": x, "y": y, "z": z}

    @staticmethod
    def color(r, g, b, a=1.0):
        return {"__type": "Color", "r": r, "g": g, "b": b, "a": a}

    @staticmethod
    def color_html(html):
        return {"__type": "Color", "html": html}

    @staticmethod
    def res(path):
        return {"__type": "Resource", "path": path}

    @staticmethod
    def node_path(p):
        return {"__type": "NodePath", "path": p}

    @staticmethod
    def gds(text):
        return {"__type": "GDS", "text": text}

    # ---- op wrappers (map 1:1 onto the table in §B.4) ----
    def ping(self):
        return self._call("ping")

    def info(self):
        return self._call("editor.info")

    def get_tree(self, frm=".", depth=-1, props=None):
        args = {"from": frm, "depth": depth}
        if props:
            args["props"] = props
        return self._call("node.get_tree", args)

    def add(self, parent, type, name=None, props=None):
        return self._call(
            "node.add",
            {"parent": parent, "type": type, "name": name, "props": props or {}},
        )

    def delete(self, path):
        return self._call("node.delete", {"path": path})

    def move(self, path, to_index):
        return self._call("node.move", {"path": path, "to_index": to_index})

    def reparent(self, path, new_parent):
        return self._call("node.reparent", {"path": path, "new_parent": new_parent})

    def rename(self, path, name):
        return self._call("node.rename", {"path": path, "name": name})

    def duplicate(self, path, **kw):
        return self._call("node.duplicate", {"path": path, **kw})

    def set_prop(self, path, prop, value):
        return self._call("node.set_prop", {"path": path, "prop": prop, "value": value})

    def get_prop(self, path, prop):
        return self._call("node.get_prop", {"path": path, "prop": prop})

    def call_method(self, path, method, args=None):
        return self._call("node.call", {"path": path, "method": method, "args": args or []})

    def list_props(self, path, usage=None):
        return self._call("node.list_props", {"path": path, "usage": usage or ""})

    def connect_signal(self, frm, signal, to, method, flags=None):
        args = {"from": frm, "signal": signal, "to": to, "method": method}
        if flags is not None:
            args["flags"] = flags
        return self._call("signal.connect", args)

    def disconnect_signal(self, frm, signal, to, method):
        return self._call(
            "signal.disconnect",
            {"from": frm, "signal": signal, "to": to, "method": method},
        )

    def list_signals(self, path):
        return self._call("signal.list", {"path": path})

    def instance(self, parent, scene, name=None):
        return self._call("scene.instance", {"parent": parent, "scene": scene, "name": name})

    def open_scene(self, path):
        return self._call("scene.open", {"path": path})

    def save_scene(self, path=None):
        return self._call("scene.save", {"path": path} if path else {})

    def new_scene(self, root_type, name=None):
        return self._call("scene.new", {"root_type": root_type, "name": name})

    def selection_get(self):
        return self._call("selection.get")

    def selection_set(self, paths):
        return self._call("selection.set", {"paths": paths})

    def play(self, scene=None):
        return self._call("play.run", {"scene": scene} if scene else {})

    def stop(self):
        return self._call("play.stop")

    def play_status(self):
        return self._call("play.status")

    def undo(self, count=1):
        return self._call("undo", {"count": count})

    def redo(self, count=1):
        return self._call("redo", {"count": count})

    def resource_load(self, path):
        return self._call("resource.load", {"path": path})

    def fs_scan(self):
        return self._call("fs.scan")
