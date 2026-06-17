"""End-to-end test for the LIVE editor bridge addon.

Launches a REAL Godot 4.3 editor (headless) on a scratch project that has the
``live_bridge`` addon enabled, connects with the Python ``LiveBridge`` client,
exercises the bridge ops, and verifies an added node is actually persisted into
the saved ``.tscn`` (the owner-set proof).

Requires GODOT_BIN (conftest back-fills it from the Windows User env) and the
``websocket-client`` package. There is NO graceful skip when the engine is
present — per HARNESS.md we use the real engine.

Run directly::

    python tests/test_live_bridge.py

or via pytest::

    pytest tests/test_live_bridge.py -s
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# conftest puts the harness root on sys.path for pytest; do the same standalone.
_HARNESS_ROOT = Path(__file__).resolve().parents[3]
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from cli_anything.godot.utils.godot_backend import find_godot_binary  # noqa: E402
from cli_anything.godot.utils.live_client import LiveBridge  # noqa: E402

SCRATCH = Path(r"D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\scratch\live_test")
ADDON_SRC = Path(__file__).resolve().parents[1] / "addons" / "live_bridge"
PORT = 8787


def _ensure_godot_bin():
    if os.environ.get("GODOT_BIN"):
        return
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, "GODOT_BIN")
                if val:
                    os.environ["GODOT_BIN"] = val
        except OSError:
            pass


def _sync_addon():
    """Copy the latest addon source into the scratch project's addons/."""
    dst = SCRATCH / "addons" / "live_bridge"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ADDON_SRC.iterdir():
        if f.suffix in (".gd",) or f.name == "plugin.cfg":
            shutil.copy2(f, dst / f.name)


def _launch_editor():
    binary = find_godot_binary()
    assert binary, "Godot binary not found (set GODOT_BIN)."
    # --editor loads the EditorPlugin; --headless keeps it off-screen.
    # Redirect to a log FILE (not a pipe): the Steam Godot build can stall if a
    # stdout pipe fills and nobody drains it.
    log = SCRATCH.parent / "editor_e2e.log"
    logf = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [binary, "--editor", "--headless", "--path", str(SCRATCH)],
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    proc._logf = logf  # keep handle alive
    return proc


def _wait_for_bridge(timeout=40.0) -> LiveBridge:
    """Poll until the in-editor server accepts a connection and replies to ping."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            b = LiveBridge(port=PORT, timeout=3.0).connect()
            b.ping()
            return b
        except Exception as e:  # connection refused while editor boots
            last = e
            time.sleep(1.0)
    raise RuntimeError(f"Bridge never became reachable within {timeout}s: {last}")


def run_e2e() -> dict:
    """Run the full flow, returning a results dict (also used by the test)."""
    _ensure_godot_bin()
    _sync_addon()
    # Reset the scratch scene so reruns start from a clean root (idempotent).
    (SCRATCH / "Main.tscn").write_text(
        '[gd_scene format=3 uid="uid://cp4ciohyvgv70"]\n\n'
        '[node name="Main" type="Node2D"]\n',
        encoding="utf-8",
    )

    results: dict = {"verified": [], "errors": []}
    proc = _launch_editor()
    bridge = None
    try:
        bridge = _wait_for_bridge()
        results["verified"].append("connect")

        pong = bridge.ping()
        assert pong["pong"] is True
        results["godot_version"] = pong["godot_version"]
        results["verified"].append("ping")

        info = bridge.info()
        assert info["edited_scene"].endswith("Main.tscn"), info
        results["verified"].append("editor.info")

        # node.add — add a Sprite2D under the root.
        added = bridge.add(".", "Sprite2D", name="Hero")
        assert added["name"] == "Hero", added
        results["added_path"] = added["path"]
        results["verified"].append("node.add")

        # node.set_prop — position via Vector2 tag.
        bridge.set_prop("Hero", "position", LiveBridge.vec2(100, 50))
        results["verified"].append("node.set_prop")

        # node.get_prop — read it back symmetrically.
        got = bridge.get_prop("Hero", "position")["value"]
        assert got.get("__type") == "Vector2", got
        assert abs(got["x"] - 100) < 0.01 and abs(got["y"] - 50) < 0.01, got
        results["roundtrip_position"] = got
        results["verified"].append("node.get_prop")

        # target-driven loose coercion: set modulate from an html string.
        bridge.set_prop("Hero", "modulate", "#ff8800")
        mod = bridge.get_prop("Hero", "modulate")["value"]
        assert mod["__type"] == "Color", mod
        results["verified"].append("node.set_prop(loose-color)")

        # node.get_tree
        tree = bridge.get_tree(".", -1)
        names = [c["name"] for c in tree["children"]]
        assert "Hero" in names, tree
        results["verified"].append("node.get_tree")

        # node.list_props
        props = bridge.list_props("Hero")["props"]
        assert any(p["name"] == "position" for p in props)
        results["verified"].append("node.list_props")

        # node.rename
        bridge.rename("Hero", "Player")
        results["verified"].append("node.rename")

        # node.duplicate (re-own subtree)
        dup = bridge.duplicate("Player", name="Player2")
        assert dup["path"], dup
        results["verified"].append("node.duplicate")

        # node.move
        bridge.move("Player2", 0)
        results["verified"].append("node.move")

        # signal.list
        sigs = bridge.list_signals("Player")
        assert isinstance(sigs["signals"], list)
        results["verified"].append("signal.list")

        # selection.set / get
        bridge.selection_set(["Player"])
        sel = bridge.selection_get()
        results["selection"] = sel
        results["verified"].append("selection.set/get")

        # scene.save — persist, then check the .tscn on disk.
        saved = bridge.save_scene()
        results["saved_path"] = saved["path"]
        results["verified"].append("scene.save")

        # node.delete (delete the duplicate) + save again
        bridge.delete("Player2")
        results["verified"].append("node.delete")
        bridge.save_scene()

        # play.status (headless: report value, don't assert play.run works)
        try:
            results["play_status"] = bridge.play_status()
            results["verified"].append("play.status")
        except Exception as e:
            results["errors"].append(f"play.status: {e}")

        # fs.scan
        try:
            bridge.fs_scan()
            results["verified"].append("fs.scan")
        except Exception as e:
            results["errors"].append(f"fs.scan: {e}")

    finally:
        if bridge is not None:
            bridge.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ---- on-disk proof: the saved Main.tscn must contain the Player node ----
    tscn_text = (SCRATCH / "Main.tscn").read_text(encoding="utf-8")
    results["tscn"] = tscn_text
    assert 'name="Player"' in tscn_text, "Added node was NOT saved (owner not set?)"
    assert 'type="Sprite2D"' in tscn_text, "Sprite2D type missing from saved scene"
    # Player2 was deleted before the final save.
    assert 'name="Player2"' not in tscn_text, "Deleted node still present in saved scene"
    results["verified"].append("on-disk-node-proof")

    return results


def test_live_bridge_e2e():
    """pytest entry point."""
    results = run_e2e()
    assert "on-disk-node-proof" in results["verified"]


if __name__ == "__main__":
    res = run_e2e()
    print("\n=== LIVE BRIDGE E2E RESULTS ===")
    print("godot_version:", res.get("godot_version"))
    print("verified ops:", ", ".join(res["verified"]))
    print("errors:", res["errors"])
    print("roundtrip_position:", res.get("roundtrip_position"))
    print("\n--- saved Main.tscn ---")
    print(res["tscn"])
