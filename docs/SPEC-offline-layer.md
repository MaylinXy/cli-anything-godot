# Godot 4.x Agent-Native CLI — Offline (File-Based + Headless) Layer Spec

Status: DESIGN ONLY (no implementation). Targets Godot **4.2 / 4.3 / 4.4** (notes for 4.5/4.6 where relevant).
Methodology: "CLI-Anything" (`cli-anything-plugin/HARNESS.md`).
Scope: the **offline layer** — operations done by (a) directly editing Godot text formats, and (b) running the Godot binary headlessly with native flags or generated temporary scripts. This builds on the existing baseline (`godot_cli.py`, `core/{project,scene,script,export}.py`, `utils/godot_backend.py`).

This spec is the contract implementers generate formats from. Sections **B** (file formats) and **C** (command tree) are the load-bearing parts.

---

## 0. Design principles for this layer

1. **Prefer pure-file edits when the format is unambiguous.** Node CRUD, property set/get for simple Variants, signals, instancing, project settings, autoload, input map — all are deterministic text transforms and need no engine subprocess. These are fast, hermetic, and testable offline.
2. **Fall back to a generated headless script only when text editing is unsafe or impossible.** Complex typed resources (Curve, Gradient, Mesh, TileSet, AnimationLibrary), baking, re-packing instanced trees, and anything needing UID/`.import` generation must go through the engine. See §D and §C.
3. **Never invent a native flag.** Verified flag set in §A. In particular there is **no `--export-all`** in 4.2–4.4 (the baseline `export.py` is wrong; see §A note and §D risk 5).
4. **Always offer `--json`** (baseline already does). Every mutate command returns the changed entity plus a `changed: true/false` idempotency hint.
5. **Round-trip safety.** A read→write of an untouched file should be byte-stable enough that Godot re-saves identically (we don't need byte-identical, but must not corrupt). When in doubt, let the engine re-serialize via a headless `--editor` scan.

---

## A. Godot CLI flag reference

Verified against the official Command Line Tutorial (4.4 docs) and engine source. "Avail" = Godot versions where it works as described.

| Flag | Purpose | Avail | Example |
|---|---|---|---|
| `-h, --help` | Print all CLI options. | all 4.x | `godot --help` |
| `--version` | Print version string (e.g. `4.4.1.stable.official`). | all 4.x | `godot --version` |
| `-v, --verbose` | Verbose stdout (extra diagnostics, import logs). | all 4.x | `godot --verbose --import` |
| `-q, --quiet` | Silence stdout (errors still shown). | all 4.x | `godot --quiet --headless --script res://t.gd` |
| `--no-header` | Suppress the engine/renderer banner line on startup (cleaner machine parsing). | 4.x | `godot --no-header --version` |
| `--headless` | Headless mode = `--display-driver headless --audio-driver Dummy`. No GPU/window. | all 4.x | `godot --headless --import` |
| `--path <dir>` | Set project dir (must contain `project.godot`). Alternative to `cwd`. | all 4.x | `godot --path ./game --headless --import` |
| `--scene <path\|uid>` | Run a specific scene (path or `uid://...`). 4.x added UID acceptance. | 4.x (UID arg 4.4+) | `godot --headless --scene res://Test.tscn` |
| `--main-pack <file>` | Load a `.pck`/`.zip` pack as the project. | all 4.x | `godot --main-pack game.pck` |
| `-e, --editor` | Boot the **editor** subsystems (gives `EditorInterface`, import pipeline). Combine with `--headless` for headless editor automation. | all 4.x | `godot --editor --headless --quit` |
| `-p, --project-manager` | Force the Project Manager. (Not used by this layer.) | all 4.x | `godot -p` |
| `--quit` | Quit after the first main-loop iteration. | all 4.x | `godot --headless --script res://t.gd --quit` |
| `--quit-after <N>` | Quit after N iterations (`0` = never). Use when a tool needs a few frames (async import/bake). | 4.x | `godot --headless --editor --quit-after 30` |
| `--script <res-path>` | Run a GDScript. **Script MUST `extends SceneTree` or `MainLoop`** — NOT `EditorScript`. Path is `res://`-relative. | all 4.x | `godot --headless --script res://tools/gen.gd` |
| `--main-loop <ClassName>` | Run a MainLoop by global `class_name`. | 4.x | `godot --headless --main-loop MyRunner` |
| `--check-only` | Parse `--script` for errors and quit; no execution. **Caveat:** some 4.x builds exit 0 even on parse errors — also scan stderr (baseline already does this). | 4.x (reliable 4.2+) | `godot --headless --check-only --script res://x.gd` |
| `--import` | Import/reimport all assets, generate `.import`/UID, then quit. **Implies `--editor` and `--quit`.** | all 4.x | `godot --headless --import` |
| `--export-release <preset> <path>` | Export named preset in release mode to `<path>`. Needs editor + installed export templates; project must be imported first. | all 4.x | `godot --headless --export-release "Linux/X11" build/game.x86_64` |
| `--export-debug <preset> <path>` | Same, debug mode (includes debug symbols / debug template). | all 4.x | `godot --headless --export-debug "Windows Desktop" build/game.exe` |
| `--export-pack <preset> <path>` | Export only the data pack (`.pck`/`.zip`), no executable. | all 4.x | `godot --headless --export-pack "Linux/X11" build/game.pck` |
| `--export-patch <preset> <path>` | Export a pack containing only changed files (patch). | 4.4+ | `godot --headless --export-patch "Web" build/patch.pck` |
| `--build-solutions` | Build C#/.NET (mono) solutions before running/exporting. Mono builds only. | 4.x mono | `godot --headless --build-solutions --quit` |
| `--doctool [<path>]` | Dump the full engine API to XML (class reference). | all 4.x | `godot --doctool ./api_xml` |
| `--no-docbase` | With `--doctool`, omit base/builtin types. | 4.x | `godot --doctool out --no-docbase` |
| `--gdscript-docs <res-path>` | Generate API docs from inline GDScript `##` doc comments for scripts under a path. | 4.x | `godot --headless --doctool docs --gdscript-docs res://src` |
| `--quit` / `--quit-after` timing | For async editor ops (scan/bake) use `--quit-after N` not `--quit` so frames elapse. | 4.x | `godot --editor --headless --quit-after 60` |
| `--rendering-driver <drv>` | Force rendering driver (`vulkan`, `opengl3`, `d3d12`, `dummy`). Rarely needed headless. | 4.x | `godot --rendering-driver dummy --headless` |
| `--display-driver <drv>` | Force display driver (`headless` is the no-op). | 4.x | `godot --display-driver headless` |
| `--audio-driver <drv>` | Force audio driver (`Dummy` headless). | 4.x | `godot --audio-driver Dummy` |
| `--write-movie <file>` | Record output to `.avi`/`.png` (Movie Maker mode). Needs a real run, not headless-friendly. | 4.x | `godot --write-movie out.avi res://Demo.tscn` |
| `--` or `++` | Separator: following args go to `OS.get_cmdline_user_args()`. Use to pass our tool params into a generated script. | 4.x | `godot --headless --script res://t.gd -- --target res://Foo.tscn` |

### Critical flag note — there is NO native "export all"
`--export-all` does **not** exist in Godot 4.2–4.4. It is a common community misconception (a PR proposing it, #104204, was only recently opened). The current baseline `export.py` passes `--export-all`, which silently fails / is treated as an unknown arg. The offline layer MUST iterate presets and call `--export-release`/`--export-debug` per preset (see §C `export build-all`, §D risk 5).

### Headless export reliability note
`--export-*` from a clean checkout with no `.godot/` cache can hang or produce broken builds (engine issue #95287, #69511). The robust sequence is: `--headless --import` (build cache) **then** `--headless --export-release ...`. The `--editor --headless --quit` reimport workaround is unreliable; prefer `--import`.

---

## B. File-format cheat-sheet (the load-bearing section)

Lexical rules common to `.tscn`/`.tres`:
- A section heading is `[<type> attr1=val1 attr2=val2]` — **no spaces around `=`** in heading attributes.
- Property lines under a heading are `key = value` — **WITH spaces** around `=`.
- Comments: `;` to end of line (dropped on re-save). Whitespace between sections insignificant.
- **Only non-default property values need to be written**; Godot drops defaults on re-save. Generators should emit only what they set.
- For 4.2–4.4: header carries `load_steps`, `format=3`, `uid="uid://..."`; ext_resource ids are strings `"N_xxxxx"`. (4.6 deprecates `load_steps` and adds per-node `unique_id=` — do NOT emit those for 4.2–4.4.)

`project.godot` / `export_presets.cfg` / `*.import` use `ConfigFile` (INI) rules: `[section]`, `key=value` with **no spaces** around `=`, keys may contain `/`.

### B.1 `project.godot` (annotated)

```ini
; Engine configuration file.
; It's best edited using the editor UI and not directly.

config_version=5                                  ; INI format version; 5 for all 4.x. Lives in the implicit top section, BEFORE any [section].

[application]

config/name="My Game"                             ; display name
config/description="One-line description."
config/version="1.2.0"
run/main_scene="res://scenes/Main.tscn"           ; scene launched on game start (res:// path)
config/features=PackedStringArray("4.4", "Forward Plus")
                                                  ; [0] = engine version that wrote the file ("4.2"/"4.3"/"4.4")
                                                  ; remaining = feature tags; renderer is "Forward Plus" | "Mobile" | "GL Compatibility"
config/icon="res://icon.svg"
run/disable_stdout=false
run/flush_stdout_on_print=true

[autoload]

GameState="*res://globals/game_state.gd"          ; leading * = ENABLED singleton (added to root at startup)
Audio="*res://globals/audio.tscn"                 ; value may be .gd, .tscn/.scn, or .tres
Disabled="res://globals/x.gd"                      ; NO * = registered but DISABLED
                                                  ; order = load order (later may depend on earlier)

[input]                                            ; the Input Map. Each action = dict { deadzone, events:[Object(...),...] }

jump={
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":32,"location":0,"echo":false,"script":null)
]
}
move_left={
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":65,"key_label":0,"unicode":97,"location":0,"echo":false,"script":null)
, Object(InputEventJoypadMotion,"resource_local_to_scene":false,"resource_name":"","device":-1,"axis":0,"axis_value":-1.0,"script":null)
]
}
fire={
"deadzone": 0.5,
"events": [Object(InputEventMouseButton,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"button_mask":0,"position":Vector2(0, 0),"global_position":Vector2(0, 0),"factor":1.0,"button_index":1,"canceled":false,"pressed":false,"double_click":false,"script":null)
]
}

[rendering]

renderer/rendering_method="forward_plus"           ; "forward_plus" | "mobile" | "gl_compatibility" — must match [application] feature tag
renderer/rendering_method.mobile="gl_compatibility"; ".mobile" = feature-tag override
textures/canvas_textures/default_texture_filter=0  ; 0=Nearest (pixel art) 1=Linear 2=Nearest+MM 3=Linear+MM
environment/defaults/default_clear_color=Color(0.3, 0.3, 0.3, 1)

[display]

window/size/viewport_width=1920                    ; design/base resolution
window/size/viewport_height=1080
window/stretch/mode="canvas_items"                 ; "disabled" | "canvas_items" | "viewport"
window/stretch/aspect="keep"                       ; "keep"|"keep_width"|"keep_height"|"expand"|"ignore"

[global_group]                                      ; Godot 4.3+ ONLY. Each key = group name, value = description string.

enemies=""
interactables="Things the player can use"

[layer_names]

2d_physics/layer_1="world"                          ; cosmetic names for the 32 collision/visibility layers
2d_physics/layer_2="player"

[physics]

common/physics_ticks_per_second=60
2d/default_gravity=980.0

[debug]

gdscript/warnings/unused_variable=2                 ; 0=ignore 1=warn 2=error
```

**Input-map encoding cheat (the hard part):**
- Action = `name={ "deadzone": <float>, "events": [ <Object(...)>, ... ] }`. Events comma-separated, each on the same logical block (editor puts each `Object(...)` after a leading `, ` on a new line).
- `Object(<ClassName>,"prop":val,...,"script":null)` — `ClassName` unquoted positional; props quoted; always ends `"script":null`; `"device":-1` = all devices.
- **InputEventKey** key fields: `physical_keycode` (layout-independent, preferred for WASD; Key enum == uppercase ASCII: A=65 D=68 S=83 W=87, Space=32), `keycode` (current layout; usually 0 when physical used), `unicode` (codepoint; 'a'=97, space=32), `key_label`/`location` usually 0, plus modifier bools.
- **InputEventMouseButton**: `button_index` (MouseButton: 1=L 2=R 3=Mid 4=WheelUp 5=WheelDown).
- **InputEventJoypadButton**: `button_index` (JoyButton: 0=A 1=B 2=X 3=Y ...), `pressure`.
- **InputEventJoypadMotion**: `axis` (JoyAxis: 0=LX 1=LY 2=RX 3=RY 4=LTrig 5=RTrig), `axis_value` (-1.0 or 1.0 direction).

### B.2 `.tscn` scene (annotated, full example)

Section order is strict: header → ext_resources → sub_resources → nodes → connections/editable.

```ini
[gd_scene load_steps=4 format=3 uid="uid://b8x7y6z5w4v3u"]
; load_steps = (#ext_resource + #sub_resource) + 1. Here 2 ext + 1 sub = 3, +1 = 4.
; OMIT load_steps entirely if the scene has zero resources.
; format=3 for all 4.x. uid = this scene's own stable id (see B.5).

[ext_resource type="Script" uid="uid://c1a2b3c4d5e6f" path="res://enemy.gd" id="1_script"]
[ext_resource type="Texture2D" uid="uid://d7g8h9i0j1k2l" path="res://enemy.png" id="2_tex"]
; type = resource class. uid = TARGET file's uid (engine prefers uid over path when resolving).
; id = in-file string "<loadIndex>_<random5>" (e.g. "1_script"). Referenced via ExtResource("1_script").
; ext_resource ids and sub_resource ids are SEPARATE namespaces.

[sub_resource type="RectangleShape2D" id="RectangleShape2D_a1b2c"]
size = Vector2(32, 48)
; no path/uid on sub_resources. id convention "<Type>_<random5>". Referenced via SubResource("RectangleShape2D_a1b2c").
; If a sub_resource references another, the referenced one must appear FIRST.

[node name="Enemy" type="CharacterBody2D" groups=["enemies"]]
; ROOT node: NO parent attribute. Exactly one root or import fails.
; groups=["a","b"] one line, each quoted. script set as a property below.
script = ExtResource("1_script")
speed = 200.0

[node name="Sprite2D" type="Sprite2D" parent="."]
; parent="." = direct child of root. Deeper: parent="Sprite2D" or parent="Path/To/Parent"
; (parent path EXCLUDES the root's own name). Parents must appear BEFORE children.
position = Vector2(0, -16)
texture = ExtResource("2_tex")
modulate = Color(1, 1, 1, 1)

[node name="CollisionShape2D" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_a1b2c")

[node name="Slot" type="Marker2D" parent="." index="0"]
; index="N" forces sibling order (quoted int). Only emit when ordering matters.

[node name="Spawner" parent="." instance=ExtResource("3_enemyscene")]
; INSTANCED scene: NO type=, has instance=ExtResource(<PackedScene id>).
; Property lines below override the instanced root's properties.
position = Vector2(400, 300)
health = 50

[editable path="Spawner"]
; mark an instanced child editable so we can override ITS internal nodes via
; a [node ... parent="Spawner/Child"] block (no type, no instance) carrying overrides.

[connection signal="body_entered" from="." to="." method="_on_body_entered"]
[connection signal="timeout" from="Timer" to="." method="_on_timeout" flags=3 unbinds=1]
; signal=emitted signal; from=emitter node path ("."=root); to=receiver; method=callback.
; flags: only written when != default PERSIST(2). DEFERRED=1 PERSIST=2 ONE_SHOT=4 REF_COUNTED=8 → 1|2=3.
; unbinds: only when >0. binds= [..]: extra bound args (note literal space before value).
```

**Variant value encodings** (property lines, `key = value`):

| Type | Encoding | | Type | Encoding |
|---|---|---|---|---|
| bool | `true`/`false` | | NodePath | `NodePath("../Other:scale.x")` |
| int | `42` | | Array (untyped) | `[1, 2, "x"]` |
| float | `1.0`, `2.59096e-05` | | Typed Array | `Array[int]([1, 2, 3])` |
| String | `"text"` (c-escaped) | | Dictionary | `{\n"k": v\n}` (newline pairs) |
| Vector2 | `Vector2(100, 50)` | | Typed Dict (4.4+) | `Dictionary[String, int]({...})` |
| Vector2i | `Vector2i(100, 50)` | | PackedByteArray | `PackedByteArray(1, 2, 3)` |
| Vector3 | `Vector3(1, 2, 3)` | | PackedInt32Array | `PackedInt32Array(1, 2, 3)` |
| Color | `Color(1, 1, 1, 1)` (RGBA 0–1) | | PackedFloat32Array | `PackedFloat32Array(0, 0.5, 1)` |
| Rect2 | `Rect2(x, y, w, h)` | | PackedStringArray | `PackedStringArray("a", "b")` |
| Transform2D | `Transform2D(xx, xy, yx, yy, ox, oy)` | | PackedVector2Array | `PackedVector2Array(0,0, 1,1)` (flat) |
| Transform3D | `Transform3D(b0..b8, ox, oy, oz)` (12 floats) | | PackedColorArray | `PackedColorArray(r,g,b,a, ...)` (flat) |

Reference encodings inside properties:
- ext resource: `texture = ExtResource("2_tex")`
- sub resource: `shape = SubResource("RectangleShape2D_a1b2c")`
- sub-property path keys (verbatim names with `/`): `surface_material_override/0 = SubResource(...)`, `bones/1/position = Vector3(...)`, `tracks/0/path = NodePath("Box:scale")`.

### B.3 `.tres` resource (annotated)

```ini
[gd_resource type="Resource" script_class="EnemyStats" load_steps=2 format=3 uid="uid://b1234567890ab"]
; type = base class (Resource, Curve, Material, ArrayMesh, ...).
; script_class = registered global class_name of the attached script (omit if none).
; load_steps same rule as scenes (ext+sub+1; omit if none). format=3. uid = this file's id.

[ext_resource type="Script" uid="uid://c0987654321zz" path="res://enemy_stats.gd" id="1_stats"]

[resource]
; exactly ONE [resource] block = the main resource's properties.
script = ExtResource("1_stats")        ; script set first (when custom resource)
max_health = 100                       ; the script's @export vars follow (non-defaults only)
move_speed = 250.0
display_name = "Goblin"
loot_table = Array[String](["coin", "potion"])
base = ExtResource("2_base_stats")     ; reference another .tres via an ext_resource
```

Typed/packed/dict encodings identical to §B.2. Sub-resources allowed (`[sub_resource ...]` between ext and `[resource]`), referenced via `SubResource("...")`.

### B.4 `.gd` GDScript (tooling note only — not parsed structurally)

We never parse GDScript internals offline. Relevant only for: creating skeleton scripts, attaching via `script = ExtResource(...)`, validation via `--check-only`, and `## ` doc comments consumed by `--gdscript-docs`. A `@tool` annotation is required for scripts run in the editor context (EditorScript helpers). Headless run scripts must `extends SceneTree`/`MainLoop`.

### B.5 UID system (must understand to keep references valid)

- Text form `uid://<base34>`; underlying value is a positive 64-bit int. Encoding alphabet is **base-34**: `a`–`y` then `0`–`8` (the letters `z` and `9` never appear — engine off-by-one quirk). Use `ResourceUID.id_to_text()`/`text_to_id()` for exactness; do NOT assume base36.
- Stored: scene/resource header `uid=`, each ext_resource's `uid=` (target's), `.import` `[remap] uid=`, and (4.4+) per-script sidecars `<file>.gd.uid` containing just the `uid://...` line.
- Engine prefers `uid` over `path` when resolving ext_resources (makes moves safe). Missing uid → falls back to path. `.godot/uid_cache.bin` maps uid→path, rebuilt on editor scan.
- **4.2/4.3 have NO script UIDs / `.uid` sidecars** — references to scripts are path-only. `.uid` sidecars and `preload("uid://...")` for scripts are 4.4+.
- Generator policy: when writing a brand-new `.tscn`/`.tres` we may either (a) omit `uid=` and let a follow-up `--import`/`--editor` scan assign one, or (b) synthesize one via a headless `ResourceUID.create_id()` call. For ext_resource `uid=` we should **copy the target's real uid** if known; if unknown, omit `uid=` and keep only `path=` (still loads).

### B.6 `.import` sidecar (overview)

INI-style; one per imported asset (`foo.png.import`). Generated by the import pipeline — we generally do NOT hand-write these; we trigger `--headless --import` instead.

```ini
[remap]
importer="texture"
type="CompressedTexture2D"
uid="uid://c0uns7dwubl7m"
path="res://.godot/imported/foo.png-<md5>.ctex"

[deps]
source_file="res://art/foo.png"
dest_files=["res://.godot/imported/foo.png-<md5>.ctex"]

[params]
compress/mode=0
mipmaps/generate=false
; ...all importer params (defaults ARE written here, unlike tscn)
```

### B.7 `export_presets.cfg` (annotated)

Separate `ConfigFile`; often gitignored (may contain signing keys). Two sections per preset.

```ini
[preset.0]
name="Windows Desktop"            ; <-- the string passed to --export-release
platform="Windows Desktop"        ; exact platform string. NOTE: 4.3 renamed "Linux/X11" -> "Linux" (issue #89012)
runnable=true
export_filter="all_resources"     ; "all_resources"|"scenes"|"resources"|"exclude"
include_filter=""                 ; comma glob list, e.g. "*.json, *.txt"
exclude_filter=""
export_path="build/game.exe"      ; output path
script_export_mode=1              ; 0=text 1=binary tokens 2=encrypted
script_encryption_key=""

[preset.0.options]
custom_template/debug=""
custom_template/release=""
binary_format/64_bits=true
binary_format/embed_pck=false
application/icon=""
; ...platform-specific options
```

---

## C. Proposed command tree (offline layer)

Conventions: all commands take the global `--project/-p` and `--json` from the baseline. "Mechanism" is one of **FILE** (direct text edit), **SCRIPT** (generated headless `.gd` run via the engine), **FLAG** (native CLI flag). Where a simple FILE path covers the common case but edge cases need the engine, both are listed (FILE / SCRIPT-fallback). Existing baseline commands are marked *(exists)* or *(fix)*.

Domains in priority order: (1) UI/Control + general scene/project, (2) GDScript tooling, (3) 2D.

### C.1 General: project settings, autoload, input map *(highest priority)*

| Command | Args / flags | Mechanism | Example |
|---|---|---|---|
| `project create` *(exists)* | `PATH --name --renderer {forward_plus,mobile,gl_compatibility} --features-version 4.4` | FILE | `gd project create ./game --name "RPG" --renderer gl_compatibility` |
| `project info` *(exists)* | — | FILE | `gd -p ./game project info` |
| `settings get` | `KEY` (e.g. `application/config/name`) | FILE | `gd settings get rendering/renderer/rendering_method` |
| `settings set` | `KEY VALUE --type {string,int,float,bool,color,vector2,raw}` | FILE | `gd settings set display/window/size/viewport_width 1280 --type int` |
| `settings unset` | `KEY` (revert to default = delete line) | FILE | `gd settings unset rendering/...` |
| `settings list` | `[--section application]` | FILE | `gd settings list --section input` |
| `autoload add` | `NAME PATH [--disabled]` (writes `NAME="*res://..."`) | FILE | `gd autoload add GameState res://globals/gs.gd` |
| `autoload remove` | `NAME` | FILE | `gd autoload remove GameState` |
| `autoload enable/disable` | `NAME` (toggle leading `*`) | FILE | `gd autoload disable Audio` |
| `autoload list` | — | FILE | `gd autoload list` |
| `input add` | `ACTION --key SPACE \| --physical-key A \| --mouse left \| --joy-button a \| --joy-axis lx:-1 [--deadzone 0.5]` (builds `Object(...)`) | FILE | `gd input add jump --physical-key SPACE` |
| `input add-event` | append additional event to existing action | FILE | `gd input add-event jump --joy-button a` |
| `input remove` | `ACTION` (whole action) or `ACTION --event-index N` | FILE | `gd input remove jump` |
| `input list` | `[ACTION]` | FILE | `gd input list` |
| `group add` *(4.3+)* | `NAME [--description "..."]` → `[global_group]` | FILE | `gd group add enemies --description "Hostiles"` |
| `group remove` *(4.3+)* | `NAME` | FILE | `gd group remove enemies` |
| `layer name` | `--space {2d_physics,3d_physics,2d_render,3d_render} --layer N NAME` | FILE | `gd layer name --space 2d_physics --layer 1 world` |

`settings set` `--type raw` writes the value verbatim (for `PackedStringArray(...)`, `Color(...)`, etc.). Input encoders own the `Object(InputEventKey,...)` serialization per §B.1.

### C.2 General: scene + node CRUD, properties, signals, instancing

| Command | Args / flags | Mechanism | Example |
|---|---|---|---|
| `scene create` *(exists, extend)* | `SCENE --root-type Control --root-name UI` | FILE | `gd scene create ui/Main.tscn --root-type Control` |
| `scene read` *(exists)* | `SCENE` (node tree + ext/sub/connections JSON) | FILE | `gd scene read ui/Main.tscn` |
| `scene tree` | `SCENE` (pretty indented tree) | FILE | `gd scene tree ui/Main.tscn` |
| `node add` *(exists as add-node, extend)* | `SCENE --name --type --parent "." [--index N] [--groups a,b]` | FILE | `gd node add ui/Main.tscn --name Btn --type Button --parent .` |
| `node remove` | `SCENE --path "Panel/Btn"` (removes node + descendants + their connections) | FILE | `gd node remove ui/Main.tscn --path Panel/Btn` |
| `node rename` | `SCENE --path P --to NewName` (rewrites name + all child parent= + connection from/to + NodePaths) | FILE | `gd node rename ui/Main.tscn --path Btn --to OkButton` |
| `node move` | `SCENE --path P --index N` (reorder among siblings via `index=`) | FILE | `gd node move ui/Main.tscn --path Btn --index 0` |
| `node reparent` | `SCENE --path P --to-parent "Other"` (update parent=, fix descendant paths) | FILE / SCRIPT-fallback | `gd node reparent ui/Main.tscn --path Btn --to-parent Panel` |
| `node duplicate` | `SCENE --path P --name Copy` (clone subtree + props) | FILE | `gd node duplicate lvl/Lvl.tscn --path Coin --name Coin2` |
| `node get-prop` | `SCENE --path P --prop position` | FILE | `gd node get-prop lvl/Lvl.tscn --path Player --prop position` |
| `node set-prop` | `SCENE --path P --prop position --value "Vector2(10,20)" [--raw]` | FILE | `gd node set-prop lvl/Lvl.tscn --path Player --prop position --value "Vector2(10,20)"` |
| `node set-prop` (ext ref) | `... --prop texture --ext-resource res://p.png[:Texture2D]` (adds/reuses ext_resource, writes `ExtResource(...)`) | FILE | `gd node set-prop s.tscn --path Spr --prop texture --ext-resource res://p.png` |
| `node set-prop` (sub ref) | `... --prop shape --sub-resource RectangleShape2D:size=Vector2(32,48)` (creates `[sub_resource]`, writes `SubResource(...)`) | FILE | see TileMap/physics below |
| `node attach-script` | `SCENE --path P --script res://x.gd` (ext_resource + `script =`) | FILE | `gd node attach-script lvl.tscn --path Player --script res://player.gd` |
| `node add-to-group` / `remove-from-group` | `SCENE --path P --group enemies` | FILE | `gd node add-to-group lvl.tscn --path E --group enemies` |
| `scene instance` | `SCENE --child-scene res://Enemy.tscn --name E --parent "." [--prop k=v ...]` (ext_resource PackedScene + `instance=`) | FILE | `gd scene instance lvl.tscn --child-scene res://Enemy.tscn --name E1` |
| `scene make-editable` | `SCENE --path InstancedNode` (`[editable path=...]`) | FILE | `gd scene make-editable lvl.tscn --path E1` |
| `scene override-child` | `SCENE --instance E1 --child Sprite2D --prop modulate=Color(1,0,0,1)` (editable + override `[node]`) | FILE | `gd scene override-child lvl.tscn --instance E1 --child Sprite2D --prop visible=false` |
| `scene repack` | `SCENE` — load+pack+save via engine to normalize/validate | SCRIPT (`PackedScene.pack`+`ResourceSaver.save`) | `gd scene repack lvl.tscn` |
| `signal connect` | `SCENE --signal pressed --from Btn --to "." --method _on_pressed [--flags 3] [--unbinds 1] [--binds "[42]"]` | FILE | `gd signal connect ui.tscn --signal pressed --from Ok --to . --method _on_ok` |
| `signal disconnect` | `SCENE --signal s --from F --to T --method M` (or `--index N`) | FILE | `gd signal disconnect ui.tscn --signal pressed --from Ok --to . --method _on_ok` |
| `signal list` | `SCENE [--from NODE]` | FILE | `gd signal list ui.tscn` |

UI/Control specifics are just `node add --type {Control,Button,Label,Panel,VBoxContainer,...}` plus `node set-prop` for `anchors_preset`, `offset_left/top/right/bottom`, `custom_minimum_size = Vector2(...)`, `size_flags_horizontal`, `theme = ExtResource(...)`. No special commands needed beyond generic node/prop/signal.

### C.3 GDScript tooling

| Command | Args / flags | Mechanism | Example |
|---|---|---|---|
| `script new` | `PATH --extends Node [--class-name Foo] [--tool]` (skeleton .gd) | FILE | `gd script new src/player.gd --extends CharacterBody2D --class-name Player` |
| `script run` *(exists)* | `SCRIPT --timeout N` (must `extends SceneTree`/`MainLoop`) | FLAG `--script` | `gd script run tools/gen.gd` |
| `script inline` *(exists)* | `CODE --timeout N` (wraps in SceneTree, temp file) | FLAG `--script` | `gd script inline 'print(2+2)'` |
| `script validate` *(exists)* | `SCRIPT` (parse only; stderr scan) | FLAG `--check-only --script` | `gd script validate src/player.gd` |
| `script validate-all` | scan `*.gd`, validate each | FLAG (loop) | `gd script validate-all` |
| `script format` | `SCRIPT [--check] [--write]` — uses `gdformat` (gdtoolkit) if present, else no-op warning | external tool / FILE | `gd script format src/player.gd --write` |
| `script lint` | `SCRIPT` — `gdlint` (gdtoolkit) if present | external tool | `gd script lint src/` |
| `script docs` | `--out DIR [--path res://src]` | FLAG `--gdscript-docs` | `gd script docs --out docs --path res://src` |
| `script test` | `--dir res://tests [--pattern test_*.gd]` — run a GUT/WAT-style headless runner or our generated SceneTree harness | SCRIPT / FLAG | `gd script test --dir res://tests` |

Note: `format`/`lint` rely on the external **gdtoolkit** (`gdformat`/`gdlint`, pip), not the engine — Godot ships no formatter. Document as optional dependency; degrade with a clear message.

### C.4 2D: nodes, physics, sprites, camera, tilemap, animation

Most 2D work is generic `node add` + `node set-prop`. Listed here are the 2D-specific conveniences and the cases that need sub_resources or scripts.

| Command | Args / flags | Mechanism | Example |
|---|---|---|---|
| `2d add-sprite` | `SCENE --name S --parent . --texture res://p.png [--region x,y,w,h]` (Sprite2D + ext_resource; region via `region_enabled=true` + `region_rect=Rect2(...)`) | FILE | `gd 2d add-sprite lvl.tscn --name Hero --texture res://hero.png` |
| `2d add-camera` | `SCENE --name Cam --parent . [--current] [--zoom 2,2]` (Camera2D) | FILE | `gd 2d add-camera lvl.tscn --current --zoom 2,2` |
| `2d add-body` | `SCENE --name P --type {CharacterBody2D,RigidBody2D,StaticBody2D,Area2D} --parent .` | FILE | `gd 2d add-body lvl.tscn --name Player --type CharacterBody2D` |
| `2d add-collision` | `SCENE --name Col --parent Player --shape {rectangle:size=Vector2(32,48),circle:radius=16,capsule:radius=8;height=32}` (CollisionShape2D + `[sub_resource]` Shape2D + `shape=SubResource(...)`) | FILE | `gd 2d add-collision lvl.tscn --parent Player --shape rectangle:size=Vector2(16,24)` |
| `2d set-physics-layer` | `SCENE --path P --collision-layer 0b0001 --collision-mask 0b0110` | FILE | `gd 2d set-physics-layer lvl.tscn --path Player --collision-layer 2` |
| `2d add-animationplayer` | `SCENE --name Anim --parent .` (AnimationPlayer node; library is empty) | FILE | `gd 2d add-animationplayer lvl.tscn --parent Hero` |
| `2d anim create` | `SCENE --player Anim --name walk --length 0.6 [--loop]` — create an Animation in an AnimationLibrary `.tres` | SCRIPT (Animation/AnimationLibrary API + ResourceSaver) | `gd 2d anim create lvl.tscn --player Anim --name walk --length 0.6 --loop` |
| `2d anim add-track` | `--player Anim --anim walk --track-type {value,method} --path "Sprite2D:frame" --key time=0,value=0 --key time=0.3,value=1` | SCRIPT | `gd 2d anim add-track ... --path "Sprite2D:frame" --key 0,0 --key 0.3,1` |
| `2d tilemap add` | `SCENE --name TM --parent . --tileset res://world.tres` (TileMapLayer node 4.3+, or TileMap pre-4.3) | FILE | `gd 2d tilemap add lvl.tscn --tileset res://world.tres` |
| `2d tileset create` | `TRES --texture res://tiles.png --tile-size 16,16 [--tiles 0,0 1,0 2,0]` (TileSet + TileSetAtlasSource; needs engine) | SCRIPT | `gd 2d tileset create world.tres --texture res://t.png --tile-size 16,16` |
| `2d tilemap paint` | `SCENE --layer TM --cells "x,y=source:atlasx,atlasy ..."` (set `tile_data`/`layer_0/tile_data` Packed array — fragile, prefer SCRIPT) | SCRIPT | `gd 2d tilemap paint lvl.tscn --layer TM --cells "0,0=0:0,0"` |

`resource create` / `resource edit` (general, used by 2D + everywhere):

| Command | Args / flags | Mechanism | Example |
|---|---|---|---|
| `resource create` | `TRES --type ResourceClass [--script res://x.gd --class-name Foo] [--prop k=v ...]` | FILE (simple props) / SCRIPT (typed/complex) | `gd resource create data/sword.tres --script res://item.gd --prop price=100` |
| `resource edit` | `TRES --prop k=v [--raw] [--ext-resource ...] [--sub-resource ...]` | FILE | `gd resource edit data/sword.tres --prop price=120` |
| `resource read` | `TRES` (parsed props JSON) | FILE | `gd resource read data/sword.tres` |
| `resource create-curve` | `TRES --point t,v [--point t,v ...]` (Curve) | SCRIPT | `gd resource create-curve ramp.tres --point 0,0 --point 1,1` |
| `resource create-gradient` | `TRES --stop offset,color [...]` (Gradient) | SCRIPT | `gd resource create-gradient fade.tres --stop 0,Color(0,0,0,1) --stop 1,Color(1,1,1,1)` |

Engine-backed maintenance (FLAG, mostly baseline):

| Command | Mechanism | Example |
|---|---|---|
| `project reimport` *(exists)* | FLAG `--import` | `gd project reimport` |
| `project scan` | FLAG `--editor --headless --quit-after N` (filesystem rescan / uid refresh) | `gd project scan` |
| `export presets` *(exists)* | FILE (parse `export_presets.cfg`) | `gd export presets` |
| `export build` *(exists)* | FLAG `--export-release/-debug PRESET PATH` | `gd export build --preset "Linux/X11" --output build/g.x86_64` |
| `export build-all` *(FIX — replaces bogus --export-all)* | FLAG, loop over presets, one `--export-release` per runnable preset | `gd export build-all --out-dir build/` |
| `export pack` | FLAG `--export-pack` | `gd export pack --preset "Web" --output build/game.pck` |
| `engine version` / `status` *(exists)* | FLAG `--version` / discovery | `gd engine version` |

---

## D. Hard cases / risks

1. **`--export-all` does not exist (baseline bug).** Godot 4.2–4.4 have no native "export all" flag (only the recent proposal PR #104204). `export build-all` MUST enumerate presets from `export_presets.cfg` and invoke `--export-release`/`--export-debug` once per preset. Also: CLI export from a clean checkout can hang/produce broken builds (issues #95287, #69511) — always run `--headless --import` first to warm `.godot/`.

2. **Things NOT cleanly doable via text editing → require a SCRIPT (engine):**
   - **Re-packing instanced trees / `scene repack`**: serializing a live node tree (PackedScene.pack + ResourceSaver.save). The `owner` gotcha (every saved node's `owner` must be the scene root or it's silently dropped).
   - **Complex typed resources**: Curve, Gradient, Mesh/ArrayMesh, AudioStream, TileSet/TileSetAtlasSource, AnimationLibrary/Animation tracks, NavigationMesh. Their text form is large, order-sensitive, and easy to corrupt; build them via the class API + ResourceSaver.
   - **Baking**: NavigationRegion, LightmapGI, OccluderInstance3D — editor-only bakers; need `--editor --headless` and frames to elapse (`--quit-after N`, not `--quit`).
   - **UID assignment / `.import` generation**: runtime `--script` does NOT run the importer. After generating assets/resources, run `--headless --import` (or do the work under `--editor`) so UIDs, `.import`, and `.godot/uid_cache.bin` are produced. Skipping this is the #1 cause of "resource has no UID"/"needs reimport"/broken-reference warnings.

3. **EditorScript vs SceneTree confusion (a real footgun).** `--script` requires `extends SceneTree`/`MainLoop`; it will **not** run an `extends EditorScript`'s `_run()` — there is no supported CLI flag that does. To get editor APIs headlessly, run a `@tool extends SceneTree` script with `--editor --headless` and reach the editor via the **`EditorInterface` singleton (4.2+)**, e.g. `EditorInterface.get_resource_filesystem().scan()`. Heavy ops are async — wait frames in `_process()` and quit later, or use `--quit-after`. Our SCRIPT-mechanism generator must pick the right base class + flags per task.

4. **The UID system is fragile (4.x):**
   - ext_resource resolution prefers `uid` over `path`; a wrong/stale `uid=` we hand-write can point loads at the wrong file. Safest: copy the target's real uid, or omit `uid=` and keep only `path=`.
   - `z`/`9` never appear in uid text (base-34 quirk) — don't validate uids as base36.
   - **Script `.uid` sidecars are 4.4+ only.** Generating `<file>.gd.uid` against 4.2/4.3 is meaningless; referencing scripts by `uid://` (`preload("uid://...")`) only works 4.4+. Branch on engine version.
   - `.uid` sidecars must be committed to VCS; if regenerated per-clone, `uid://` links break for collaborators.

5. **`.import` regeneration.** Never hand-write `.import` files; they encode md5-hashed cache paths under `.godot/imported/`. Generating source assets without a subsequent `--import` leaves them unusable. `.godot/imported/` must not be committed; `.import` must be.

6. **Version-specific gotchas to branch on:**
   - **Platform string rename (4.3):** `"Linux/X11"` → `"Linux"` (issue #89012). Preset name lookups and examples must not hardcode the old string for 4.3+. Always read names from `export_presets.cfg`.
   - **`[global_group]` section is 4.3+.** `group add/remove` must error or no-op cleanly on 4.2.
   - **TileMap → TileMapLayer (4.3+).** The monolithic `TileMap` node is deprecated in favor of one `TileMapLayer` per layer; `tile_data` layout differs (`layer_N/tile_data` vs `tile_data`). `2d tilemap *` must branch on version.
   - **`format=3` / `load_steps` present for 4.2–4.4; 4.6 deprecates `load_steps` and adds per-node `unique_id=`.** Generators must target the 4.2–4.4 form and not emit `unique_id=`.
   - **`--check-only` exit code unreliable** on some builds (exits 0 on parse error) — keep the baseline's stderr-marker scan.

7. **Round-trip / normalization risk.** Hand-edited `.tscn`/`.tres` that we don't perfectly match to Godot's serializer (attribute order, `load_steps` count, escaping) still load, but the editor will rewrite them on next save, producing noisy diffs. For correctness-critical writes (instancing, sub_resources, tilemaps), prefer the SCRIPT path so the engine's own serializer produces canonical output. Offer `scene repack`/`project scan` as a "normalize after bulk edits" step.

8. **Concurrent edits / engine cache lock.** Running an engine subprocess (`--import`, `--editor`) while also editing files by hand can race the `.godot/` cache. Sequence engine calls; treat FILE edits and SCRIPT/FLAG calls as a pipeline (edit → scan/import → export), never interleaved concurrently.

---

## Sources

- [Command line tutorial — Godot 4.4 docs](https://docs.godotengine.org/en/4.4/tutorials/editor/command_line_tutorial.html)
- [Running code in the editor (@tool, EditorScript) — Godot 4.4 docs](https://docs.godotengine.org/en/4.4/tutorials/plugins/running_code_in_the_editor.html)
- [TSCN file format — godot-docs](https://github.com/godotengine/godot-docs/blob/master/engine_details/file_formats/tscn.rst)
- [Godot source: resource_format_text.cpp](https://github.com/godotengine/godot/blob/master/scene/resources/resource_format_text.cpp), [variant_parser.cpp](https://github.com/godotengine/godot/blob/master/core/variant/variant_parser.cpp), [resource_uid.cpp](https://github.com/godotengine/godot/blob/master/core/io/resource_uid.cpp)
- [ResourceUID — Godot 4.4 class ref](https://docs.godotengine.org/en/4.4/classes/class_resourceuid.html), [UID changes in 4.4 — Godot blog](https://godotengine.org/article/uid-changes-coming-to-godot-4-4/)
- [InputEventKey — Godot 4.4 class ref](https://docs.godotengine.org/en/4.4/classes/class_inputeventkey.html)
- [Project-wide groups PR #60965](https://github.com/godotengine/godot/pull/60965)
- [Export-all proposal PR #104204](https://github.com/godotengine/godot/pull/104204), [Headless export hang #95287](https://github.com/godotengine/godot/issues/95287), [CLI reimport before export #69511](https://github.com/godotengine/godot/issues/69511), [Linux platform rename #89012](https://github.com/godotengine/godot/issues/89012)
- [Allow running headless editor scripts — proposals #8664](https://github.com/godotengine/godot-proposals/discussions/8664)
