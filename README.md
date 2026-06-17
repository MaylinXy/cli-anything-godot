# cli-anything-godot

**Agent-native CLI for the [Godot](https://godotengine.org/) 4.x game engine.**
Let an AI agent (or your scripts) operate Godot through plain commands — both
**offline** (editing `.tscn` / `.tres` / `project.godot` directly and running the
engine headlessly) and **live** (controlling a *running* editor in real time over a
WebSocket bridge).

> Built on the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) methodology
> ("Making ALL Software Agent-Native"). Apache-2.0. See [NOTICE](NOTICE).

> 中文简介:这是一套让 AI 直接操作 Godot 引擎的命令行工具。两种用法:**离线**——直接读写
> 场景/资源/项目文件、跑无头引擎;**实时**——通过编辑器插件桥接,实时操控你正开着的 Godot
> 编辑器(增删节点、改属性、连信号、实例化、运行/撤销)。装好后让 AI 用大白话帮你做游戏。

---

## Why

GUIs were built for human eyes and hands. An AI agent can't click around the Godot
editor. This CLI turns Godot operations into structured, scriptable, `--json`-friendly
commands, so an agent can build scenes, wire signals, configure projects, write/validate
GDScript, set up 2D gameplay, export builds — and even mutate a live editor session.

## Two layers

| Layer | What it does | Needs the editor open? |
|-------|--------------|------------------------|
| **Offline** | Reads/writes `.tscn` / `.tres` / `project.godot` as text, and runs `godot --headless` for engine-backed work (import, export, resource baking, script run/validate). | No |
| **Live** | A Godot `EditorPlugin` (`addons/live_bridge`) hosts a JSON-over-WebSocket server (`127.0.0.1:8787`); the CLI drives the **running** editor: add/move/reparent nodes, set any Inspector property, connect signals, instance scenes, save, play, undo/redo. | Yes |

## Requirements

- **Godot 4.x** (developed and tested against **4.3 stable**). Either on your `PATH`
  as `godot`/`godot4`, or point `GODOT_BIN` at the executable.
- **Python 3.10+**
- Optional: `websocket-client` (for the `live` layer), `gdtoolkit` (for
  `script format` / `script lint`).

## Install

```bash
git clone https://github.com/<you>/cli-anything-godot.git
cd cli-anything-godot
pip install -e .            # core
pip install -e ".[all]"    # core + live (websocket-client) + format (gdtoolkit)
```

Tell it where Godot is (skip if `godot` is already on your `PATH`):

```bash
# Linux/macOS
export GODOT_BIN="/path/to/Godot"
# Windows (PowerShell)
$env:GODOT_BIN = "C:\path\to\Godot.exe"
# Windows, persist for future shells:
setx GODOT_BIN "C:\path\to\Godot.exe"
```

Verify:

```bash
cli-anything-godot engine version
cli-anything-godot --help
```

## Quick start — offline

```bash
# Create a project and a scene
cli-anything-godot project create ./MyGame --name "My Game"
cli-anything-godot -p ./MyGame scene create scenes/Main.tscn --root-type Node2D --root-name Main

# Build a node tree, set properties, wire a signal
cli-anything-godot -p ./MyGame node add scenes/Main.tscn --name Player --type CharacterBody2D --parent .
cli-anything-godot -p ./MyGame 2d add-sprite scenes/Main.tscn --name Sprite --parent Player --texture res://icon.svg
cli-anything-godot -p ./MyGame node set-prop scenes/Main.tscn --path Player --prop position --value "Vector2(100, 50)"

# Project config, input map, autoload
cli-anything-godot -p ./MyGame settings set application/config/name "My Game" --type string
cli-anything-godot -p ./MyGame input add jump --physical-key SPACE
cli-anything-godot -p ./MyGame autoload add GameState res://globals/game_state.gd

# Scripts and export
cli-anything-godot -p ./MyGame script new src/player.gd --extends CharacterBody2D --class-name Player
cli-anything-godot -p ./MyGame script validate src/player.gd
cli-anything-godot -p ./MyGame export build-all
```

Add `--json` to any command for machine-readable output.

## Quick start — live (control a running editor)

```bash
# 1. Install the bridge addon into your project (do this with the editor CLOSED)
cli-anything-godot -p ./MyGame live install

# 2. Launch the editor — the bridge starts listening on 127.0.0.1:8787
"$GODOT_BIN" --editor --path ./MyGame      # (or open the project in Godot normally)

# 3. Drive the live editor
cli-anything-godot live status
cli-anything-godot live add Sprite2D --parent . --name Hero
cli-anything-godot live set Hero position --vec2 120 80
cli-anything-godot live get Hero position
cli-anything-godot live save
cli-anything-godot live undo
```

Connection config: `--host` / `--port` / `--token` flags, or
`GODOT_LIVE_HOST` / `GODOT_LIVE_PORT` / `GODOT_LIVE_TOKEN` env vars
(defaults `127.0.0.1:8787`, no token). The bridge binds to localhost only.

## Command groups

`project` · `scene` · `node` · `signal` · `settings` · `autoload` · `input` ·
`group` · `layer` · `resource` · `script` · `2d` · `export` · `engine` · `live`

See **[USAGE.md](cli_anything/godot/USAGE.md)** for a full command cheat-sheet and
**[SKILL.md](cli_anything/godot/skills/SKILL.md)** for the agent-facing skill definition.

## How it works

- **Offline** parses and serializes Godot's text formats (`.tscn`/`.tres` scene &
  resource files, `project.godot`/`export_presets.cfg` ConfigFiles) and, where text
  editing is unsafe (typed resources, baking, instanced-tree repacking, UID/`.import`
  generation), generates a temporary headless GDScript and runs it through the engine.
- **Live** ships a `@tool EditorPlugin` that runs a `TCPServer` + `WebSocketPeer`
  inside the editor, polled on the main thread; commands are dispatched through
  `EditorInterface` + `EditorUndoRedoManager` (so edits are undoable and saved
  correctly — node `owner` is set to the edited scene root).

Design details: [docs/SPEC-offline-layer.md](docs/SPEC-offline-layer.md) and
[docs/SPEC-live-bridge.md](docs/SPEC-live-bridge.md).

## Running the tests

The suite validates against a **real** Godot install (no mocking).

```bash
export GODOT_BIN="/path/to/Godot"      # required for engine-backed tests
export PYTHONPATH="$PWD"               # or: pip install -e ".[dev]"
python -m pytest cli_anything/godot/tests/ -q
# -> 167 passed
```

## Status & roadmap

- ✅ Offline layer: scenes, nodes, signals, settings, autoload, input map, groups,
  layers, resources, GDScript tooling, 2D (sprites/bodies/collision/tilemaps/animation),
  export. Live layer: full node/property/signal/scene/play/undo control.
- 🔭 The live bridge supports more ops than the CLI currently surfaces
  (`node.move/rename/call`, `scene.open/new`, `resource.*`, `fs.scan`, …) — easy to
  wire up as more `live` subcommands. 3D conveniences are not yet built (the generic
  `node`/`resource` commands still work for 3D).

## License

[Apache License 2.0](LICENSE). Built on and crediting
[HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything) — see [NOTICE](NOTICE).
