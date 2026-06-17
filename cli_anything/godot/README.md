# cli-anything-godot

Agent-native **hybrid CLI** for the **Godot Engine** (targets 4.3; works across
4.2-4.4). It exposes structured, `--json`-capable commands for project, scene,
node, resource, GDScript, 2D, and export work — usable both by humans and by AI
agents.

It has **two layers**:

- **OFFLINE** — deterministic edits of Godot's text formats (`.tscn`, `.tres`,
  `project.godot`, `export_presets.cfg`) plus headless engine runs for tasks that
  can't be hand-edited safely (Curve/Gradient/TileSet/Animation builders,
  validate, import, export, scene repack). Most offline ops need no engine and no
  running editor at all.
- **LIVE** — real-time control of an *already-running* editor through the
  `live_bridge` addon: a JSON-over-WebSocket server hosted inside an
  `EditorPlugin`. Add/move/set nodes, connect signals, instance scenes, save,
  play, and undo/redo — all on the editor's active scene tab.

The two layers are complementary, not interchangeable: offline is for
batch/file work; live is for interactive mutation of an open session.

## Installation

```bash
# from the agent-harness directory
pip install -e .
```

Optional extras (features degrade gracefully if missing):

```bash
pip install websocket-client   # required for the LIVE layer (`live …`)
pip install gdtoolkit          # gdformat/gdlint for `script format` / `script lint`
```

## Prerequisites

- **Godot 4.3** on PATH, or set `GODOT_BIN` to the binary:
  ```bash
  # bash / Git Bash
  export GODOT_BIN="/path/to/Godot"
  ```
  ```powershell
  # PowerShell
  $env:GODOT_BIN = "C:\path\to\Godot.exe"
  ```
  Pure-file commands work without the engine; only **[engine]**-backed commands
  (export, script run/inline/validate/docs/test, repack, reimport, the
  Curve/Gradient/TileSet/Animation/tilemap-paint builders) require `GODOT_BIN`.
- Python 3.10+.

## Invocation

```bash
cli-anything-godot [--json] [-p|--project DIR] <group> <subcommand> [opts]
# or the module form (used in CI):
python -m cli_anything.godot.godot_cli [--json] [-p DIR] <group> <subcommand> [opts]
```

- `--json` — structured output for agents (recommended for any scripted use).
- `-p, --project DIR` — Godot project directory.
- No group → interactive REPL (`cli-anything-godot session`).

## Quick start — OFFLINE

```bash
# 1. create a project
cli-anything-godot project create ./game --name "RPG"

# 2. build a scene from files (no engine needed)
cli-anything-godot -p ./game scene create scenes/Main.tscn --root-type Node2D
cli-anything-godot -p ./game node add scenes/Main.tscn --name Player --type CharacterBody2D --parent .
cli-anything-godot -p ./game 2d add-sprite scenes/Main.tscn --name Hero --texture res://hero.png --parent Player
cli-anything-godot -p ./game 2d add-collision scenes/Main.tscn --parent Player --shape rectangle:size=Vector2(16,24)
cli-anything-godot -p ./game signal connect scenes/Main.tscn --signal pressed --from Player --to . --method _on_pressed

# 3. project config
cli-anything-godot -p ./game settings set application/run/main_scene res://scenes/Main.tscn
cli-anything-godot -p ./game autoload add GameState res://globals/gs.gd
cli-anything-godot -p ./game input add jump --physical-key SPACE

# 4. validate / inspect (engine + file)
cli-anything-godot --json -p ./game scene read scenes/Main.tscn
cli-anything-godot -p ./game script validate-all
cli-anything-godot --json -p ./game project info
```

## Quick start — LIVE

Requires `websocket-client`. The bridge addon ships under
`cli_anything/godot/addons/live_bridge` and is copied into the target project by
`live install`.

```bash
# 1. install + enable the addon (run with the editor CLOSED)
cli-anything-godot -p ./game live install
#    then open ./game in the Godot editor; the plugin starts a server on 127.0.0.1:8787

# 2. confirm the bridge is reachable
cli-anything-godot --json -p ./game live status

# 3. drive the running editor's active scene
cli-anything-godot --json live add Sprite2D --parent . --name Hero
cli-anything-godot --json live set Hero position --vec2 100 50
cli-anything-godot --json live set Hero texture --res res://hero.png
cli-anything-godot --json live instance res://Enemy.tscn --parent .
cli-anything-godot --json live save
cli-anything-godot --json live play
cli-anything-godot --json live undo
```

Connection: `--host/--port/--token` flags or `GODOT_LIVE_HOST/PORT/TOKEN` env
(defaults `127.0.0.1:8787`, no token). The bridge binds localhost only and can
run arbitrary editor ops — treat it as RCE on the dev machine.

See `USAGE.md` for the full per-command cheat-sheet and `skills/SKILL.md` for the
agent-facing skill definition.

## Command groups

| Group | Subs | Layer | Description |
|-------|-----|-------|-------------|
| `project` | 5 | offline | create / info / scenes / scripts / resources / reimport |
| `scene` | 8 | offline | create / read / tree / instance / make-editable / override-child / repack (+ deprecated add-node) |
| `node` | 11 | offline | add / remove / move / reparent / rename / duplicate / get-prop / set-prop / attach-script / add-to-group / remove-from-group |
| `signal` | 3 | offline | connect / disconnect / list |
| `settings` | 4 | offline | get / set / unset / list |
| `autoload` | 5 | offline | add / remove / enable / disable / list |
| `input` | 4 | offline | add / add-event / remove / list |
| `group` | 3 | offline | add / remove / list (`[global_group]`, 4.3+) |
| `layer` | 1 | offline | name |
| `resource` | 5 | offline | create / edit / read / create-curve / create-gradient |
| `script` | 10 | offline | new / run / inline / validate / validate-all / format / lint / docs / test |
| `2d` | 8 | offline | add-sprite / add-camera / add-body / add-collision / set-physics-layer / add-animationplayer / anim / tilemap / tileset |
| `export` | 3 | offline | presets / build / build-all |
| `engine` | 2 | offline | status / version |
| `live` | 16 | live | install / status / tree / add / delete / select / set / get / connect / instance / save / play / stop / undo / redo |

Plus `session` (interactive REPL, no subcommands).

## Running the tests

The full suite (167 tests) requires `PYTHONPATH` set to the agent-harness dir and
`GODOT_BIN` pointing at the engine (engine-backed and live tests exercise the
real binary):

```powershell
# PowerShell (from the repo root)
$env:PYTHONPATH = "$PWD"
$env:GODOT_BIN  = "C:\path\to\Godot.exe"
python -m pytest cli_anything/godot/tests -q
# -> 167 passed
```

```bash
# bash / Git Bash (from the repo root)
export PYTHONPATH="$PWD"
export GODOT_BIN="/path/to/Godot"
python -m pytest cli_anything/godot/tests -q
```

## Architecture

- **Offline layer** — file/headless. Pure-text edits of Godot formats for
  deterministic, hermetic operations (`commands/scene_nodes.py`,
  `commands/config.py`, `commands/resources.py`, `commands/gdscript.py`,
  `commands/twod.py`, and the project/export/engine groups in `godot_cli.py`),
  falling back to a generated headless `--script` run (`utils/godot_backend.py`)
  only when text editing is unsafe (typed resources, baking, repack, UID/import).
- **Live layer** — an `EditorPlugin` WebSocket bridge
  (`addons/live_bridge/`) hosted inside the running editor, driven by a Python
  client (`commands/live.py`). One JSON request → one JSON reply, dispatched on
  the editor's main thread; all mutations go through the editor undo/redo manager.

Design rationale and the full contracts are in the specs:
`SPEC-offline-layer.md` (file formats, flag reference, command tree) and
`SPEC-live-bridge.md` (addon architecture, JSON wire protocol, Variant coercion).

## Security note

- `script inline` / `script run` execute GDScript on the host via a Godot
  subprocess — only use with trusted input.
- The live bridge can create nodes, set properties, call methods, and play
  scenes; it is effectively remote code execution on the dev machine. It binds
  `127.0.0.1` only and supports an optional shared token. Disable the addon when
  not in use.
