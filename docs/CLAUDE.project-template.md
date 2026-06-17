# Using this template

Copy this file into the **root of your Godot project** and rename it to `CLAUDE.md`.
Any Claude Code window opened in that folder will auto-load it and instantly know how
to drive this project with `cli-anything-godot`. Everything below the line is the
template — edit the project-specific notes at the bottom as you go.

---

# Project: <YOUR GAME NAME>

This is a **Godot 4.3** game project. You (Claude) can operate this project directly
through **`cli-anything-godot`** — an agent-native CLI for Godot.
Repo / docs: https://github.com/MaylinXy/cli-anything-godot

## How to drive Godot here

- Command is installed globally: **`cli-anything-godot`** (fallback:
  `python -m cli_anything.godot.godot_cli`).
- The engine path is in the `GODOT_BIN` env var (Godot 4.3). No need to set it.
- **Always pass `--json`** so you get machine-readable output.
- Two ways to work:

### A) OFFLINE (edit project files — engine need not be open)
Operate on `.tscn` / `.tres` / `project.godot` directly. Examples:
```bash
cli-anything-godot --json -p . scene create scenes/Main.tscn --root-type Node2D --root-name Main
cli-anything-godot --json -p . node add scenes/Main.tscn --name Player --type CharacterBody2D --parent .
cli-anything-godot --json -p . node set-prop scenes/Main.tscn --path Player --prop position --value "Vector2(100, 50)"
cli-anything-godot --json -p . 2d add-sprite scenes/Main.tscn --name Sprite --parent Player --texture res://icon.svg
cli-anything-godot --json -p . input add jump --physical-key SPACE
cli-anything-godot --json -p . script new src/player.gd --extends CharacterBody2D --class-name Player
cli-anything-godot --json -p . script validate src/player.gd
```
`-p .` means "this project" (run from the project root).

### B) LIVE (control the running editor in real time)
Requires: the `live_bridge` addon installed in this project AND the Godot editor open.
```bash
# one-time per project (with the editor CLOSED):
cli-anything-godot -p . live install
# then the human opens the project in Godot (or: "$GODOT_BIN" --editor --path .)
cli-anything-godot --json live status      # check the bridge is reachable
cli-anything-godot --json live add Sprite2D --parent . --name Hero
cli-anything-godot --json live set Hero position --vec2 120 80
cli-anything-godot --json live get Hero position
cli-anything-godot --json live save
cli-anything-godot --json live undo
```
Notes: live ops act on the **currently active scene tab** in the editor. Bridge listens
on `127.0.0.1:8787`. If `live status` errors, the editor isn't open or the addon isn't
enabled — fall back to OFFLINE, or ask the human to open the editor.

## Discovering the full command set
- Full cheat-sheet: run `cli-anything-godot <group> --help` for any of:
  `project scene node signal settings autoload input group layer resource script 2d export engine live`
- Or read the installed package's `USAGE.md` / `skills/SKILL.md`.

## Guidance
- Prefer OFFLINE for batch/structural work; use LIVE when the human is watching the
  editor and wants to see changes appear live.
- After OFFLINE edits that add assets/textures, run `cli-anything-godot -p . project reimport`.
- Verify by reading back: `scene read` / `scene tree` (offline) or `live tree` (live).

---

## Project-specific notes (fill in as the project grows)
- Main scene:
- Key autoloads / singletons:
- Coding conventions:
- TODO:
