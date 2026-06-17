---
name: "cli-anything-godot"
description: >-
  Agent-native hybrid CLI for the Godot 4.3 game engine. Two layers: an OFFLINE
  layer that reads and edits Godot files directly (.tscn scenes, .tres resources,
  project.godot) plus runs the engine headlessly for tasks that need it (Curve/
  Gradient/TileSet/Animation builders, validate, export, import); and a LIVE layer
  that controls an already-running editor in real time over a JSON/WebSocket bridge
  addon (add/move/set nodes, connect signals, instance scenes, save/play, undo/redo
  on the active scene tab). Every command supports --json for machine parsing.
---

# cli-anything-godot

Agent-native CLI for **Godot 4.3** (works across 4.2-4.4). It has two
complementary layers:

- **OFFLINE** — deterministic text edits of Godot's file formats (`.tscn`,
  `.tres`, `project.godot`, `export_presets.cfg`) plus headless engine runs for
  things that are unsafe to hand-edit. No running editor needed; most ops do not
  even need the engine binary.
- **LIVE** — drives a *running* editor through the `live_bridge` addon (a
  JSON-over-WebSocket server inside an `EditorPlugin`). Ops act on the editor's
  **currently active scene tab** and are undoable.

The two layers are disjoint: offline = batch/file work, live = interactive
mutation of an open session. They are not interchangeable.

## Prerequisites

- **Godot 4.x** (tested on 4.3) on PATH as `godot`/`godot4`, or set `GODOT_BIN` to
  the engine executable (e.g. `C:\path\to\Godot.exe` or `/path/to/Godot`).
- Python 3.10+.
- Optional pip deps (features degrade gracefully without them):
  - `websocket-client` — required for the **LIVE** layer (`live …` commands).
  - `gdtoolkit` — provides `gdformat`/`gdlint` for `script format` / `script lint`.

Engine requirement per op: **pure-file** ops (node/scene/signal/settings/etc.)
need no engine; **engine-backed** ops (marked below) shell out to Godot via
`--headless --script` or a native flag. LIVE ops need a running editor with the
bridge installed and enabled.

## Invocation

```bash
cli-anything-godot [--json] [-p|--project DIR] <group> <subcommand> [opts]
# module form (used in tests/CI):
python -m cli_anything.godot.godot_cli [--json] [-p DIR] <group> <subcommand> [opts]
```

Global flags (before the group):
- `--json` — emit JSON for machine parsing. **Agents: always pass `--json`.**
- `-p, --project DIR` — Godot project directory (must contain `project.godot`).

Running with no group starts an interactive REPL: `cli-anything-godot session`.

---

# OFFLINE layer

Pure-file unless tagged **[engine]** (runs the Godot binary).

## `project` — manage projects (5 subcommands)
- `create PATH [--name]` — create a new project at PATH.
- `info` — show metadata from `project.godot`.
- `scenes` / `scripts` / `resources` — list `.tscn`/`.scn`, `.gd`, `.tres`/`.res` files.
- `reimport` — **[engine]** force re-import of all resources (`--import`).

```bash
cli-anything-godot project create ./game --name "RPG"
cli-anything-godot --json -p ./game project info
cli-anything-godot --json -p ./game project scenes
```

## `scene` — scene CRUD (8 subcommands)
- `create SCENE [--root-type] [--root-name]` — new `.tscn`.
- `read SCENE` — node tree + ext/sub resources + connections (JSON-friendly).
- `tree SCENE` — pretty indented node tree.
- `instance SCENE --child-scene res://X.tscn --name N [--parent] [--prop k=v] [--index]` — instance a PackedScene.
- `make-editable SCENE --path INSTANCE` — write `[editable path=...]`.
- `override-child SCENE …` — override a property on a node inside an instanced scene.
- `repack SCENE` — **[engine]** load+pack+save to normalize/validate.
- `add-node` — *deprecated alias for `node add`; prefer `node add`.*

```bash
cli-anything-godot -p ./game scene create scenes/Main.tscn --root-type Node2D
cli-anything-godot --json -p ./game scene read scenes/Main.tscn
cli-anything-godot -p ./game scene instance scenes/Main.tscn --child-scene res://Enemy.tscn --name E1
```

## `node` — node CRUD (11 subcommands)
- `add SCENE --name --type [--parent] [--index] [--groups a,b]` — add a child node.
- `remove SCENE --path` — remove node + descendants + their connections.
- `move SCENE --path --index` — reorder among siblings.
- `reparent SCENE --path --to-parent` — move subtree, fix descendant paths.
- `rename SCENE --path --to` — rename + update references.
- `duplicate SCENE --path --name` — clone subtree.
- `get-prop SCENE --path --prop` — read a property (raw + parsed).
- `set-prop SCENE --path --prop (--value [--type]|--raw|--ext-resource|--sub-resource)` — set a property/value/ref.
- `attach-script SCENE --path --script res://x.gd` — ext_resource + `script =`.
- `add-to-group` / `remove-from-group SCENE --path --group` — scene group membership.

```bash
cli-anything-godot -p ./game node add scenes/Main.tscn --name Player --type CharacterBody2D --parent .
cli-anything-godot -p ./game node set-prop scenes/Main.tscn --path Player --prop position --value "Vector2(10,20)"
cli-anything-godot -p ./game node set-prop scenes/Main.tscn --path Spr --prop texture --ext-resource res://hero.png:Texture2D
```

## `signal` — connections (3 subcommands)
- `connect SCENE --signal --from --to --method [--flags] [--unbinds] [--binds]`
- `disconnect SCENE (--signal --from --to --method | --index N)`
- `list SCENE` — list outgoing connections.

```bash
cli-anything-godot -p ./game signal connect ui/Main.tscn --signal pressed --from Ok --to . --method _on_ok
```

## `settings` — project.godot settings (4 subcommands)
- `get KEY` · `set KEY VALUE [--type string|int|float|bool|color|vector2|raw]` · `unset KEY` · `list [--section]`

```bash
cli-anything-godot -p ./game settings set display/window/size/viewport_width 1280 --type int
cli-anything-godot --json -p ./game settings list --section input
```

## `autoload` — singletons (5 subcommands)
- `add NAME PATH [--disabled]` · `remove NAME` · `enable NAME` · `disable NAME` · `list`

```bash
cli-anything-godot -p ./game autoload add GameState res://globals/gs.gd
```

## `input` — input map (4 subcommands)
- `add ACTION (--physical-key|--key|--mouse|--joy-button|--joy-axis) [--deadzone]`
- `add-event ACTION …` (append another event) · `remove ACTION [--event-index N]` · `list [ACTION]`

```bash
cli-anything-godot -p ./game input add jump --physical-key SPACE
cli-anything-godot -p ./game input add-event jump --joy-button a
```

## `group` — project-wide groups, `[global_group]` 4.3+ (3 subcommands)
- `add NAME [--description]` · `remove NAME` · `list`

```bash
cli-anything-godot -p ./game group add enemies --description "Hostiles"
```

## `layer` — collision/render/navigation layer names (1 subcommand)
- `name --space {2d_physics,...} --layer N NAME`

```bash
cli-anything-godot -p ./game layer name --space 2d_physics --layer 1 world
```

## `resource` — .tres resources (5 subcommands)
- `create TRES_PATH [--type] [--script] [--class-name] [--prop k=v ...]`
- `edit TRES …` · `read TRES` (parsed props)
- `create-curve TRES --point t,v ...` — **[engine]** Curve builder.
- `create-gradient TRES --stop offset,Color(...) ...` — **[engine]** Gradient builder.

```bash
cli-anything-godot -p ./game resource create data/sword.tres --script res://item.gd --prop price=100
cli-anything-godot -p ./game resource create-curve data/ramp.tres --point 0,0 --point 1,1
```

## `script` — GDScript tooling (10 subcommands)
- `new PATH [--extends] [--class-name] [--tool]` — skeleton script (file).
- `run SCRIPT` — **[engine]** execute headless (must `extends SceneTree`/`MainLoop`).
- `inline CODE` — **[engine]** run inline code wrapped in `SceneTree._init`.
- `validate SCRIPT` / `validate-all` — **[engine]** syntax check (`--check-only` + stderr scan).
- `format` / `lint` — needs **gdtoolkit** (`gdformat`/`gdlint`); degrades with a message.
- `docs --out DIR [--path]` — **[engine]** generate docs from `##` comments.
- `test [--dir] [--pattern] [--timeout]` — **[engine]** run a generated headless test harness over `test_*.gd`.

```bash
cli-anything-godot -p ./game script new src/player.gd --extends CharacterBody2D --class-name Player
cli-anything-godot -p ./game script validate src/player.gd
cli-anything-godot -p ./game script inline 'print(2+2)'
```

## `2d` — 2D conveniences (8 subcommands; some nested)
- `add-sprite SCENE --name --texture [--parent] [--region]`
- `add-camera SCENE …` · `add-body SCENE --name --type {CharacterBody2D,RigidBody2D,...}`
- `add-collision SCENE --parent --shape 'rectangle:size=Vector2(32,48)|circle:radius=16|capsule:radius=8;height=32'`
- `set-physics-layer SCENE …` · `add-animationplayer SCENE …`
- `anim create|add-track` — **[engine]** Animation/AnimationLibrary authoring.
- `tilemap add|paint` — TileMapLayer (4.3); `paint` is **[engine]**.
- `tileset create TRES --texture --tile-size 'w,h' [--tiles]` — **[engine]** TileSet authoring.

```bash
cli-anything-godot -p ./game 2d add-sprite scenes/Main.tscn --name Hero --texture res://hero.png
cli-anything-godot -p ./game 2d add-collision scenes/Main.tscn --parent Player --shape rectangle:size=Vector2(16,24)
cli-anything-godot -p ./game 2d anim create scenes/Main.tscn --player Anim --name walk --length 0.6 --loop
```

## `export` — platform export, all **[engine]** (3 subcommands)
- `presets` — list configured presets (reads `export_presets.cfg`).
- `build [--preset] [--output] [--debug]` — export one named preset, or all runnable presets if `--preset` omitted.
- `build-all [--debug]` — export every runnable preset (one `--export-release`/`--export-debug` per preset; replaces the non-existent `--export-all`).

```bash
cli-anything-godot --json -p ./game export presets
cli-anything-godot -p ./game export build --preset "Windows Desktop" --output build/game.exe
cli-anything-godot -p ./game export build-all
```

## `engine` — engine info (2 subcommands)
- `status` — is the Godot binary available. · `version` — engine version.

```bash
cli-anything-godot --json engine status
```

---

# LIVE layer

Real-time control of a **running** editor via the `live_bridge` addon. Requires
`pip install websocket-client`. All `live` ops act on the editor's **active
scene tab** (no per-tab targeting); switch tabs in the editor first if needed.

Connection config on every `live` op: `--host` / `--port` / `--token`, or env
`GODOT_LIVE_HOST` / `GODOT_LIVE_PORT` / `GODOT_LIVE_TOKEN`. Defaults
`127.0.0.1:8787`, no token. The bridge binds localhost only and can execute
arbitrary node/method ops — treat it as RCE on the dev machine.

## `live` (16 subcommands)
- `install [--force]` — copy the `live_bridge` addon into the project and enable it in `project.godot`. **Run with the editor CLOSED, then launch the editor** so the plugin loads and the bridge starts listening on `127.0.0.1:8787`.
- `status` — probe the bridge; show editor/session info (`editor.info`). Use as the "is it reachable" check.
- `tree [--from] [--depth]` — dump the live scene tree.
- `add TYPE [--parent] [--name]` — add a node (owner auto-set to scene root).
- `delete PATH` · `select PATHS` — delete a node / set editor selection.
- `set PATH PROP (--value|--vec2 X Y|--vec3 X Y Z|--color '#rrggbb'|--res res://…)` — set a property.
- `get PATH PROP` — read a property.
- `connect FROM_PATH SIGNAL TO_PATH METHOD` — connect a signal (persisted).
- `instance res://X.tscn [--parent] [--name]` — instance a PackedScene.
- `save [PATH]` — save current scene (Save As if PATH given).
- `play [SCENE]` · `stop` — play current/given scene / stop.
- `undo [COUNT]` · `redo [COUNT]` — undo/redo editor actions.

### Typical live workflow
```bash
# 1. install addon (editor closed), then launch the editor manually/from CLI
cli-anything-godot -p ./game live install
#    (open the project in the Godot editor now)

# 2. confirm the bridge is up
cli-anything-godot --json -p ./game live status

# 3. drive the active scene
cli-anything-godot --json live add Sprite2D --parent . --name Hero
cli-anything-godot --json live set Hero position --vec2 100 50
cli-anything-godot --json live set Hero texture --res res://hero.png
cli-anything-godot --json live get Hero position
cli-anything-godot --json live connect Button pressed Hero _on_pressed
cli-anything-godot --json live instance res://Enemy.tscn --parent .
cli-anything-godot --json live save
cli-anything-godot --json live play
cli-anything-godot --json live undo
```

Owner/save semantics: nodes added live get `owner = edited scene root`, so they
are persisted on `save`; for instanced scenes only the instance root is owned by
the host scene. Edits go through the editor undo/redo manager and mark the scene
dirty, so `live undo`/`redo` reverse them.

---

## Agent guidance

1. **Always pass `--json`** for machine-parseable output.
2. **Pick the right layer.** Offline = edit files / batch headless (engine
   optional, no editor). Live = mutate a running editor (needs editor + bridge).
   They are not interchangeable.
3. **Engine vs pure-file.** Ops tagged **[engine]** above (and the whole
   `export` group, `*.run/inline/validate*/docs/test`, `repack`, `reimport`,
   Curve/Gradient/TileSet/Animation/tilemap-paint builders) require `GODOT_BIN`.
   Everything else is a deterministic file edit and runs without the engine.
4. **Live preconditions.** `live` needs `websocket-client` + a running editor
   with the bridge enabled. Call `live status` first; it is the cheap
   reachability probe. `live` ops act on the active scene tab only.
5. **Idempotency / errors.** Mutating commands report what changed; check the
   process exit code (0 = success) and parse stderr on failure.
6. **Use res:// paths** for child scenes, textures, scripts, and resources.

See `README.md` (architecture, install, tests) and `USAGE.md` (full cheat-sheet)
in the package. Design rationale lives in `SPEC-offline-layer.md` and
`SPEC-live-bridge.md`.
