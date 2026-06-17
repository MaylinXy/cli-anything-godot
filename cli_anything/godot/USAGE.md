# cli-anything-godot — command cheat-sheet

Quick reference for every command. Invocation:

```bash
cli-anything-godot [--json] [-p|--project DIR] <group> <subcommand> [opts]
# module form: python -m cli_anything.godot.godot_cli ...
```

Global flags (before the group): `--json` (machine output), `-p/--project DIR`.
**[E]** = engine-backed (needs `GODOT_BIN`); everything else is a pure-file edit.
**[L]** = LIVE op (needs a running editor + `live_bridge` + `websocket-client`).

---

## OFFLINE

### project
| Command | Description | Example |
|---------|-------------|---------|
| `project create PATH [--name]` | Create a new project | `... project create ./game --name "RPG"` |
| `project info` | Show `project.godot` metadata | `... -p ./game project info` |
| `project scenes` | List `.tscn`/`.scn` | `... -p ./game project scenes` |
| `project scripts` | List `.gd` | `... -p ./game project scripts` |
| `project resources` | List `.tres`/`.res` | `... -p ./game project resources` |
| `project reimport` **[E]** | Re-import all resources | `... -p ./game project reimport` |

### scene
| Command | Description | Example |
|---------|-------------|---------|
| `scene create SCENE [--root-type] [--root-name]` | New `.tscn` | `... scene create scenes/Main.tscn --root-type Node2D` |
| `scene read SCENE` | Tree + resources + connections | `... --json scene read scenes/Main.tscn` |
| `scene tree SCENE` | Pretty node tree | `... scene tree scenes/Main.tscn` |
| `scene instance SCENE --child-scene --name [--parent] [--prop k=v] [--index]` | Instance a PackedScene | `... scene instance lvl.tscn --child-scene res://Enemy.tscn --name E1` |
| `scene make-editable SCENE --path` | Mark instanced node editable | `... scene make-editable lvl.tscn --path E1` |
| `scene override-child SCENE …` | Override prop on instanced child | `... scene override-child lvl.tscn --instance E1 --child Sprite2D --prop visible=false` |
| `scene repack SCENE` **[E]** | Normalize/validate via engine | `... scene repack lvl.tscn` |
| `scene add-node` | *deprecated alias → `node add`* | — |

### node
| Command | Description | Example |
|---------|-------------|---------|
| `node add SCENE --name --type [--parent] [--index] [--groups]` | Add child node | `... node add Main.tscn --name Player --type CharacterBody2D --parent .` |
| `node remove SCENE --path` | Remove node + descendants + connections | `... node remove Main.tscn --path Panel/Btn` |
| `node move SCENE --path --index` | Reorder among siblings | `... node move Main.tscn --path Btn --index 0` |
| `node reparent SCENE --path --to-parent` | Move subtree, fix paths | `... node reparent Main.tscn --path Btn --to-parent Panel` |
| `node rename SCENE --path --to` | Rename + update refs | `... node rename Main.tscn --path Btn --to OkButton` |
| `node duplicate SCENE --path --name` | Clone subtree | `... node duplicate lvl.tscn --path Coin --name Coin2` |
| `node get-prop SCENE --path --prop` | Read a property | `... node get-prop lvl.tscn --path Player --prop position` |
| `node set-prop SCENE --path --prop (--value [--type]\|--raw\|--ext-resource\|--sub-resource)` | Set property/value/ref | `... node set-prop lvl.tscn --path Player --prop position --value "Vector2(10,20)"` |
| `node attach-script SCENE --path --script` | Attach a `.gd` | `... node attach-script lvl.tscn --path Player --script res://player.gd` |
| `node add-to-group SCENE --path --group` | Add to scene group | `... node add-to-group lvl.tscn --path E --group enemies` |
| `node remove-from-group SCENE --path --group` | Remove from scene group | `... node remove-from-group lvl.tscn --path E --group enemies` |

`node set-prop --type` accepts: `int,float,bool,string,vector2,color,nodepath,raw…`.
`--ext-resource res://path[:Type]` writes `ExtResource(...)`; `--sub-resource Type:k=v,...` writes `SubResource(...)`.

### signal
| Command | Description | Example |
|---------|-------------|---------|
| `signal connect SCENE --signal --from --to --method [--flags] [--unbinds] [--binds]` | Write a `[connection]` | `... signal connect ui.tscn --signal pressed --from Ok --to . --method _on_ok` |
| `signal disconnect SCENE (--signal --from --to --method \| --index N)` | Remove a connection | `... signal disconnect ui.tscn --index 0` |
| `signal list SCENE` | List outgoing connections | `... --json signal list ui.tscn` |

### settings
| Command | Description | Example |
|---------|-------------|---------|
| `settings get KEY` | Read a setting | `... settings get rendering/renderer/rendering_method` |
| `settings set KEY VALUE [--type string\|int\|float\|bool\|color\|vector2\|raw]` | Set a setting | `... settings set display/window/size/viewport_width 1280 --type int` |
| `settings unset KEY` | Delete a setting line | `... settings unset rendering/...` |
| `settings list [--section]` | List settings | `... settings list --section input` |

### autoload
| Command | Description | Example |
|---------|-------------|---------|
| `autoload add NAME PATH [--disabled]` | Register a singleton | `... autoload add GameState res://globals/gs.gd` |
| `autoload remove NAME` | Remove | `... autoload remove GameState` |
| `autoload enable NAME` | Enable (add leading `*`) | `... autoload enable Audio` |
| `autoload disable NAME` | Disable (remove `*`) | `... autoload disable Audio` |
| `autoload list` | List with enabled state | `... --json autoload list` |

### input
| Command | Description | Example |
|---------|-------------|---------|
| `input add ACTION (--physical-key\|--key\|--mouse\|--joy-button\|--joy-axis) [--deadzone]` | New action + first event | `... input add jump --physical-key SPACE` |
| `input add-event ACTION …` | Append another event | `... input add-event jump --joy-button a` |
| `input remove ACTION [--event-index N]` | Remove action or one event | `... input remove jump` |
| `input list [ACTION]` | List actions / one action's events | `... --json input list` |

### group  (`[global_group]`, 4.3+)
| Command | Description | Example |
|---------|-------------|---------|
| `group add NAME [--description]` | Add a project group | `... group add enemies --description "Hostiles"` |
| `group remove NAME` | Remove | `... group remove enemies` |
| `group list` | List | `... --json group list` |

### layer
| Command | Description | Example |
|---------|-------------|---------|
| `layer name --space {2d_physics,...} --layer N NAME` | Name a collision/render/nav layer | `... layer name --space 2d_physics --layer 1 world` |

### resource
| Command | Description | Example |
|---------|-------------|---------|
| `resource create TRES [--type] [--script] [--class-name] [--prop k=v]` | Create a `.tres` | `... resource create data/sword.tres --script res://item.gd --prop price=100` |
| `resource edit TRES …` | Edit props/refs | `... resource edit data/sword.tres --prop price=120` |
| `resource read TRES` | Print parsed props | `... --json resource read data/sword.tres` |
| `resource create-curve TRES --point t,v …` **[E]** | Build a Curve | `... resource create-curve ramp.tres --point 0,0 --point 1,1` |
| `resource create-gradient TRES --stop offset,Color(...) …` **[E]** | Build a Gradient | `... resource create-gradient fade.tres --stop 0,Color(0,0,0,1) --stop 1,Color(1,1,1,1)` |

### script
| Command | Description | Example |
|---------|-------------|---------|
| `script new PATH [--extends] [--class-name] [--tool]` | Skeleton `.gd` | `... script new src/player.gd --extends CharacterBody2D --class-name Player` |
| `script run SCRIPT` **[E]** | Run headless (`extends SceneTree`) | `... script run tools/gen.gd` |
| `script inline CODE` **[E]** | Run inline code | `... script inline 'print(2+2)'` |
| `script validate SCRIPT` **[E]** | Syntax check one file | `... script validate src/player.gd` |
| `script validate-all` **[E]** | Validate every `*.gd` | `... script validate-all` |
| `script format` | Format via `gdformat` (gdtoolkit) | `... script format src/player.gd` |
| `script lint` | Lint via `gdlint` (gdtoolkit) | `... script lint src/` |
| `script docs --out DIR [--path]` **[E]** | Docs from `##` comments | `... script docs --out docs --path res://src` |
| `script test [--dir] [--pattern] [--timeout]` **[E]** | Headless `test_*.gd` harness | `... script test --dir res://tests` |

### 2d
| Command | Description | Example |
|---------|-------------|---------|
| `2d add-sprite SCENE --name --texture [--parent] [--region]` | Sprite2D + texture | `... 2d add-sprite Main.tscn --name Hero --texture res://hero.png` |
| `2d add-camera SCENE …` | Camera2D | `... 2d add-camera Main.tscn --name Cam` |
| `2d add-body SCENE --name --type {CharacterBody2D,RigidBody2D,StaticBody2D,Area2D}` | Physics body | `... 2d add-body Main.tscn --name Player --type CharacterBody2D` |
| `2d add-collision SCENE --parent --shape SPEC` | CollisionShape2D + Shape2D | `... 2d add-collision Main.tscn --parent Player --shape rectangle:size=Vector2(16,24)` |
| `2d set-physics-layer SCENE …` | Set collision_layer/mask | `... 2d set-physics-layer Main.tscn --path Player --collision-layer 2` |
| `2d add-animationplayer SCENE …` | Empty AnimationPlayer | `... 2d add-animationplayer Main.tscn --parent Hero` |
| `2d anim create SCENE --player --name --length [--loop] [--library]` **[E]** | Animation in a library | `... 2d anim create Main.tscn --player Anim --name walk --length 0.6 --loop` |
| `2d anim add-track …` **[E]** | Add a value/method track | `... 2d anim add-track Main.tscn --player Anim --anim walk --track-type value --path "Sprite2D:frame" --key 0,0` |
| `2d tilemap add SCENE --name --parent --tileset` | TileMapLayer node (4.3) | `... 2d tilemap add Main.tscn --tileset res://world.tres` |
| `2d tilemap paint SCENE --layer --cells` **[E]** | Paint cells (re-packs scene) | `... 2d tilemap paint Main.tscn --layer TM --cells "0,0=0:0,0"` |
| `2d tileset create TRES --texture --tile-size [--tiles]` **[E]** | TileSet + atlas source | `... 2d tileset create world.tres --texture res://t.png --tile-size 16,16` |

Shape spec: `rectangle:size=Vector2(32,48)` \| `circle:radius=16` \| `capsule:radius=8;height=32`.

### export  (all **[E]**)
| Command | Description | Example |
|---------|-------------|---------|
| `export presets` | List configured presets | `... --json export presets` |
| `export build [--preset] [--output] [--debug]` | Export one preset (all if `--preset` omitted) | `... export build --preset "Windows Desktop" --output build/game.exe` |
| `export build-all [--debug]` | Export every runnable preset | `... export build-all` |

### engine
| Command | Description | Example |
|---------|-------------|---------|
| `engine status` | Is the Godot binary available | `... --json engine status` |
| `engine version` | Engine version | `... engine version` |

### session
| Command | Description |
|---------|-------------|
| `session` | Interactive REPL (also the default with no group) |

---

## LIVE  (all **[L]**; flags `--host/--port/--token` or env `GODOT_LIVE_HOST/PORT/TOKEN`, default `127.0.0.1:8787`)

| Command | Description | Example |
|---------|-------------|---------|
| `live install [--force]` | Copy + enable the `live_bridge` addon (editor closed) | `... -p ./game live install` |
| `live status` | Probe the bridge; editor/session info | `... --json live status` |
| `live tree [--from] [--depth]` | Dump live scene tree | `... --json live tree --depth 2` |
| `live add TYPE [--parent] [--name]` | Add a node | `... live add Sprite2D --parent . --name Hero` |
| `live delete PATH` | Delete a node | `... live delete Hero` |
| `live select PATHS` | Set editor selection | `... live select Hero` |
| `live set PATH PROP (--value\|--vec2 X Y\|--vec3 X Y Z\|--color '#hex'\|--res res://…)` | Set a property | `... live set Hero position --vec2 100 50` |
| `live get PATH PROP` | Read a property | `... --json live get Hero position` |
| `live connect FROM SIGNAL TO METHOD` | Connect a signal | `... live connect Button pressed Hero _on_pressed` |
| `live instance res://X.tscn [--parent] [--name]` | Instance a PackedScene | `... live instance res://Enemy.tscn --parent .` |
| `live save [PATH]` | Save current scene (Save As if PATH) | `... live save` |
| `live play [SCENE]` | Play current/given scene | `... live play` |
| `live stop` | Stop the running game | `... live stop` |
| `live undo [COUNT]` | Undo editor actions | `... live undo 2` |
| `live redo [COUNT]` | Redo editor actions | `... live redo` |
