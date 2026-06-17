<!--
  HOW TO USE THIS TEMPLATE (this comment is ignored by Claude / readers):
  Copy this file into your Godot project root and rename it to CLAUDE.md.
  Then replace <YOUR GAME NAME> and the GODOT_BIN path with your real values,
  and fill in the "Project notes" section. A Claude Code window opened in that
  folder auto-loads CLAUDE.md and will drive the project via cli-anything-godot.
-->
# Project: <YOUR GAME NAME> (Godot 4.x)

You (Claude) can operate this Godot project directly with the **`cli-anything-godot`**
CLI (installed globally). When the user asks anything about "using Godot / the godot cli
/ building the game here", use `cli-anything-godot` — do NOT hunt for a raw `godot`/`godot4`
binary on PATH.

## Engine location (important)
`cli-anything-godot` finds the engine via the `GODOT_BIN` env var:
`<PATH TO YOUR Godot executable>`

If an engine-backed command reports **"Godot binary not found"** (can happen if this
window started before the env var was set), set it for the session first, then retry:
```powershell
$env:GODOT_BIN = "<PATH TO YOUR Godot executable>"
```
```bash
export GODOT_BIN="<PATH TO YOUR Godot executable>"
```

## First thing to run (self-check)
```
cli-anything-godot --json engine status      # expect: available=true, binary=<path>
```

## How to drive this project (run from this folder; `-p .` = this project)
Always pass `--json`. Verify changes by reading back (`scene tree` / `scene read` / `live tree`).

OFFLINE (edit .tscn/.tres/project.godot; engine usually not needed):
```
cli-anything-godot --json -p . scene tree scenes/Main.tscn
cli-anything-godot --json -p . node add scenes/Main.tscn --name X --type Node2D --parent .
cli-anything-godot --json -p . node set-prop scenes/Main.tscn --path Player --prop position --value "Vector2(0, 0)"
cli-anything-godot --json -p . input add jump --physical-key SPACE
cli-anything-godot --json -p . script validate src/player.gd
```
Command groups: `project scene node signal settings autoload input group layer resource script 2d export engine live`.
Discover any group with `cli-anything-godot <group> --help`.

LIVE (control a running editor in real time; needs the bridge installed once + editor open):
```
cli-anything-godot -p . live install      # once, with the editor CLOSED
# user opens the project in Godot, then:
cli-anything-godot --json live status
cli-anything-godot --json live add Sprite2D --parent . --name Hero
cli-anything-godot --json live save
```
If `live status` errors, the editor isn't open or the bridge isn't enabled -> use OFFLINE,
or ask the user to open the project in Godot.

Tool repo / full docs: https://github.com/MaylinXy/cli-anything-godot

## Project notes (fill in as the project grows)
- Main scene:
- Key autoloads / singletons:
- Coding conventions:
- TODO:
