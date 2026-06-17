# Implementation Contract — cli-anything-godot (hybrid: offline + live)

All implementation agents MUST follow this contract so their independently-written
files compose without conflict. Read this fully before writing code.

## Target environment (fixed)
- **Godot 4.3 stable** is the installed engine. `GODOT_BIN` env var is set to:
  `D:\GAME\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe`
- Platform: Windows. Python 3.13. Use `subprocess` with `GODOT_BIN`.
- Target Godot **4.3** semantics specifically (4.3 facts: `TileMapLayer` not `TileMap`;
  `[global_group]` exists; export platform string is `"Linux"` not `"Linux/X11"`;
  scripts have NO `.uid` sidecars; `format=3`, `load_steps`, `config_version=5`).
- Two design specs are authoritative for behavior/formats:
  - `D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\SPEC-offline-layer.md`
  - `D:\ClaudeWorkspace\CLI-Anything-Study\godot-build\SPEC-live-bridge.md`

## Package root (everything lives here)
```
D:\ClaudeWorkspace\CLI-Anything-Study\CLI-Anything\godot\agent-harness\cli_anything\godot\
```
Existing baseline (do NOT rewrite except where this contract says "FOUND owns"):
- `godot_cli.py` — root Click CLI (integration owner = orchestrator)
- `core/{project,scene,script,export}.py`
- `utils/{godot_backend,repl_skin}.py`

## File ownership (disjoint — no two agents touch the same file)
- **FOUND** owns (shared libraries everyone builds on):
  - `core/variant_fmt.py` (NEW) — Godot Variant text <-> Python helpers
  - `core/tscn.py` (NEW) — parse/serialize `.tscn` and `.tres`
  - `core/configfile.py` (NEW) — parse/serialize `project.godot`, `export_presets.cfg`, `.import` (ConfigFile/INI, no spaces around `=`, keys may contain `/`, preserve order)
  - `utils/godot_backend.py` (EXTEND) — add helpers below; fix `--export-all` bug
  - `core/export.py` (FIX) — replace bogus `--export-all` with per-preset loop
- **LIVE** owns:
  - `addons/live_bridge/{plugin.cfg,plugin.gd,server.gd,dispatch.gd,variants.gd,nodeutil.gd}` (NEW; live bridge addon — GDScript). Put the addon under the package at `cli_anything/godot/addons/live_bridge/` so it ships as package data; `live install` copies it into a target project's `addons/`.
  - `utils/live_client.py` (NEW) — Python WebSocket client
  - `commands/live.py` (NEW) — `live_group`
- **NODES** owns: `core/nodes.py`, `core/signals.py`, `commands/scene_nodes.py` (defines `scene_group`, `node_group`, `signal_group`)
- **CONFIG** owns: `core/settings.py`, `commands/config.py` (defines `settings_group`, `autoload_group`, `input_group`, `pgroup_group`, `layer_group`)
- **RES_SCRIPT** owns: `core/resources.py`, `core/gdscript_tools.py`, `commands/resources.py` (`resource_group`), `commands/gdscript.py` (`script_group`)
- **TWOD** owns: `core/twod.py`, `commands/twod.py` (`twod_group`)
- Orchestrator owns final integration: rewrites `godot_cli.py` to import and register
  all groups; keeps `project`/`engine`/`export` groups.

Create `commands/__init__.py` (empty) when first making the `commands/` package — FOUND creates it.

## Click group export convention (so the orchestrator can wire everything)
Each `commands/*.py` module defines top-level `click.Group` objects as module globals with
the exact names listed under "owns" above. Example:
```python
import click
node_group = click.Group("node", help="Node CRUD: add/remove/move/reparent/...")
@node_group.command("add")
@click.argument("scene")
...
```
Do NOT call `cli.add_command` yourself and do NOT edit `godot_cli.py`. The orchestrator does:
```python
from cli_anything.godot.commands.scene_nodes import scene_group, node_group, signal_group
cli.add_command(scene_group); cli.add_command(node_group); cli.add_command(signal_group)
```

## CLI conventions (match existing baseline)
- Root passes context `ctx.obj["json"]` (bool), `ctx.obj["project"]` (abs path or None),
  `ctx.obj["repl"]` (bool). Resolve project dir with: `ctx.obj.get("project") or os.getcwd()`.
- Every command supports `--json` (inherited from root). Use a shared `_out(ctx, data)`
  helper pattern like the baseline's (`godot_cli.py` lines 28-53). To avoid duplication,
  FOUND adds `core/output.py` with `emit(ctx, data)` and `handle_error` decorator copied
  from the baseline; all command modules import from there.  ### FOUND: create core/output.py
- Mutating commands return a dict including `"status": "ok"` and `"changed": true|false`.
- Errors: raise `RuntimeError(msg)` from core functions; the `handle_error` decorator
  formats them. Core modules must NOT print or sys.exit.

## core/variant_fmt.py API (FOUND implements; others import)
Property values in `.tscn`/`.tres` are stored as RAW Godot-literal strings (the text after
`=`). variant_fmt converts between convenient Python inputs and that raw text.
```python
def to_literal(value, kind: str | None = None) -> str:
    """Python value -> Godot literal text.
    kind in {None,'int','float','bool','string','vector2','vector2i','vector3','color',
             'rect2','nodepath','raw', ...}. kind=None infers from python type.
    kind='raw' returns str(value) verbatim (caller already wrote a Godot literal).
    Examples: to_literal((10,20),'vector2') -> 'Vector2(10, 20)';
              to_literal('hi') -> '"hi"'; to_literal(True) -> 'true'."""
def parse_literal(text: str):
    """Godot literal text -> Python value where feasible (numbers, bool, quoted string,
    Vector2/Color/etc -> a GDValue or tuple). Best-effort; unknown -> GDValue(raw=text)."""
class GDValue:
    """Opaque wrapper for a raw Godot literal we don't decompose. .raw is the text."""
```
Also provide helpers for references:
```python
def ext_ref(id_: str) -> str:  # -> 'ExtResource("1_x")'
def sub_ref(id_: str) -> str:  # -> 'SubResource("RectangleShape2D_a1b2c")'
```

## core/tscn.py API (FOUND implements; NODES/RES/TWOD import)
```python
class ExtResource:  type:str; path:str; uid:str|None; id:str
class SubResource:  type:str; id:str; props:"OrderedDict[str,str]"  # raw literal values
class SceneNode:
    name:str; type:str|None; parent:str|None  # parent None => root; '.' => child of root
    instance:str|None      # ExtResource id when this node is an instanced PackedScene
    groups:list[str]; index:int|None; props:"OrderedDict[str,str]"  # raw literal values
class Connection: signal:str; from_:str; to:str; method:str; flags:int|None; unbinds:int|None; binds:str|None
class TscnFile:
    kind:str              # 'scene' or 'resource'
    fmt:int=3; uid:str|None
    resource_type:str|None; script_class:str|None   # for .tres header
    ext: list[ExtResource]; sub: list[SubResource]
    nodes: list[SceneNode]                # scenes
    connections: list[Connection]; editables: list[str]
    resource_props: "OrderedDict[str,str]"  # the single [resource] block for .tres

    @classmethod
    def parse(cls, text:str) -> "TscnFile": ...
    @classmethod
    def new_scene(cls, root_type:str, root_name:str|None=None) -> "TscnFile": ...
    @classmethod
    def new_resource(cls, resource_type:str, script_path:str|None=None,
                     script_class:str|None=None) -> "TscnFile": ...
    def serialize(self) -> str: ...   # MUST recompute load_steps, order sections correctly

    # resource table (dedup by (type,path)); returns id like '2_ab3kd'
    def add_ext_resource(self, type:str, path:str, uid:str|None=None) -> str: ...
    def add_sub_resource(self, type:str, props:dict|None=None) -> str: ...  # id 'Type_xxxxx'

    # node helpers (scenes)
    def root(self) -> SceneNode|None: ...
    def find(self, node_path:str) -> SceneNode|None: ...   # 'Panel/Btn'; '.' => root
    def children_of(self, node_path:str) -> list[SceneNode]: ...
    def add_node(self, name:str, type:str|None, parent:str, *, instance:str|None=None,
                 groups:list[str]|None=None, index:int|None=None) -> SceneNode: ...
    def remove_node(self, node_path:str) -> None: ...  # also drop descendants + their connections
```
Serialization rules (from SPEC-offline §B): heading attrs `attr=val` (no spaces);
property lines `key = value` (spaces); section order header→ext→sub→nodes→connections→editable;
`load_steps = #ext + #sub + 1` (omit if zero); root node has no `parent`; child uses
`parent="."` or relative path EXCLUDING root name; parents emitted before children;
instanced node has `instance=ExtResource("id")` and NO `type=`. id namespaces for ext vs sub
are separate. Write only provided props (omit defaults). Round-trip parse→serialize of an
untouched file must not corrupt it (need not be byte-identical).

ID generation: ext ids `"<n>_<rand5>"` where n is 1-based add order; sub ids `"<Type>_<rand5>"`.
rand5 = 5 lowercase-alnum chars. To stay resume/deterministic-friendly, derive rand from a
counter+hash, NOT from system RNG/time (those are unavailable in some contexts) — e.g.
base36 of an incrementing per-file counter seeded by content hash.

## core/configfile.py API (FOUND implements; CONFIG imports)
```python
class ConfigFile:
    """Order-preserving ConfigFile (project.godot / export_presets.cfg / *.import).
    Top-level keys before any [section] are kept in section ''. Values stored as raw text."""
    @classmethod
    def load(cls, path:str) -> "ConfigFile": ...
    @classmethod
    def parse(cls, text:str) -> "ConfigFile": ...
    def save(self, path:str) -> None: ...
    def serialize(self) -> str: ...
    def get(self, section:str, key:str, default=None) -> str|None: ...
    def set(self, section:str, key:str, raw_value:str) -> None: ...  # creates section/key
    def unset(self, section:str, key:str) -> bool: ...
    def has_section(self, section:str) -> bool: ...
    def section_items(self, section:str) -> list[tuple[str,str]]: ...
    def sections(self) -> list[str]: ...
```

## utils/godot_backend.py additions (FOUND implements; RES/TWOD/SCRIPT import)
Keep existing functions. Add:
```python
def import_project(project_path:str, timeout:int=180) -> dict:
    """Run `--headless --import` to warm .godot cache / generate UIDs/.import. Returns run dict."""
def run_generated_script(project_path:str, gd_source:str, *, editor:bool=False,
                         timeout:int=120, quit_after:int|None=None,
                         user_args:list[str]|None=None) -> dict:
    """Write gd_source to a temp .gd inside the project, run via the engine, delete it.
    editor=False -> `extends SceneTree` runner with `--script ... --quit`.
    editor=True  -> `@tool extends SceneTree` with `--editor --headless` (+ --quit-after if set)
                    so EditorInterface is available. Caller's gd_source must define the right
                    base class body (provide both helpers OR document the required shape).
    Returns {returncode, stdout, stderr}. Raises RuntimeError on engine-not-found/timeout."""
```
Also FIX `core/export.py`: there is NO `--export-all` in 4.3. `export_project(preset=None)`
must enumerate runnable presets from `export_presets.cfg` and call `--export-release <name> <path>`
once per preset (after a `import_project` warmup). Add `export build-all` semantics there.

## Testing expectation (per HARNESS.md)
Every agent writes/extends tests under `tests/`. Use real Godot via `GODOT_BIN` for anything
engine-backed (NO graceful skip). Pure file-format functions get unit tests with synthetic
data. Do not fake. Where you produce a `.tscn`/`.tres`, validate it loads by running a tiny
headless script that `load()`s it. Print artifact paths. The orchestrator runs a final
integrated E2E pass; each agent should at least self-test its own module imports + a smoke run.

## Style
Match the baseline's idiom (Click, `_out`/`emit`, `--json`, RuntimeError). Type hints OK
(Python 3.13). Keep modules focused. Add concise docstrings. No new 3rd-party deps except:
`websocket-client` (LIVE, optional with clear error) and `gdtoolkit` (SCRIPT format/lint, optional).
