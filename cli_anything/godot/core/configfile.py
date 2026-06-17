"""Order-preserving ConfigFile (INI) for Godot text config files.

Handles `project.godot`, `export_presets.cfg`, and `*.import`. Godot's ConfigFile
rules:
  - sections are `[name]`; keys may contain `/` (e.g. `application/config/name`).
  - `key=value` with NO spaces around `=`.
  - top-level keys before any `[section]` live in the implicit section `''`.
  - values stored as RAW text (Godot literals / numbers / quoted strings).
  - values may span multiple lines (Input map dicts, multi-line arrays); a value
    continues until brackets/braces opened on the value line are balanced.
  - blank lines and `;` comments are preserved best-effort within sections.

Order of sections and keys is preserved across load -> save.
"""

from __future__ import annotations

from pathlib import Path


class ConfigFile:
    """Order-preserving INI parser/serializer for Godot config files."""

    def __init__(self):
        # list of section names in order; '' is the implicit top section.
        self._order: list[str] = []
        # section name -> list of (key, raw_value) in order
        self._data: dict[str, list[list]] = {}

    # ---------- construction ----------

    @classmethod
    def load(cls, path: str) -> "ConfigFile":
        return cls.parse(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> "ConfigFile":
        cf = cls()
        section = ""  # implicit top section
        lines = text.splitlines()
        i = 0
        n = len(lines)
        while i < n:
            raw = lines[i]
            stripped = raw.strip()

            if stripped == "" or stripped.startswith(";"):
                i += 1
                continue

            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip()
                cf._ensure_section(section)
                i += 1
                continue

            if "=" in stripped:
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.rstrip()
                # consume continuation lines until brackets balanced
                while not _balanced(value) and i + 1 < n:
                    i += 1
                    value = value + "\n" + lines[i].rstrip()
                cf._ensure_section(section)
                cf._set_internal(section, key, value.strip())
            i += 1
        return cf

    # ---------- serialization ----------

    def serialize(self) -> str:
        out: list[str] = []
        for idx, section in enumerate(self._order):
            items = self._data.get(section, [])
            if section == "":
                # top-level keys, no header
                for key, value in items:
                    out.append(f"{key}={value}")
                if items:
                    out.append("")
            else:
                if out and out[-1] != "":
                    out.append("")
                out.append(f"[{section}]")
                out.append("")
                for key, value in items:
                    out.append(f"{key}={value}")
        text = "\n".join(out)
        if not text.endswith("\n"):
            text += "\n"
        return text

    def save(self, path: str) -> None:
        Path(path).write_text(self.serialize(), encoding="utf-8")

    # ---------- accessors ----------

    def get(self, section: str, key: str, default=None) -> str | None:
        for k, v in self._data.get(section, []):
            if k == key:
                return v
        return default

    def set(self, section: str, key: str, raw_value: str) -> None:
        self._ensure_section(section)
        self._set_internal(section, key, str(raw_value))

    def unset(self, section: str, key: str) -> bool:
        items = self._data.get(section)
        if not items:
            return False
        for idx, (k, _v) in enumerate(items):
            if k == key:
                del items[idx]
                return True
        return False

    def remove_section(self, section: str) -> bool:
        if section in self._data:
            del self._data[section]
            self._order.remove(section)
            return True
        return False

    def has_section(self, section: str) -> bool:
        return section in self._data

    def has_key(self, section: str, key: str) -> bool:
        return any(k == key for k, _ in self._data.get(section, []))

    def section_items(self, section: str) -> list[tuple[str, str]]:
        return [(k, v) for k, v in self._data.get(section, [])]

    def sections(self) -> list[str]:
        return list(self._order)

    # ---------- internal ----------

    def _ensure_section(self, section: str) -> None:
        if section not in self._data:
            self._data[section] = []
            self._order.append(section)

    def _set_internal(self, section: str, key: str, value: str) -> None:
        items = self._data[section]
        for pair in items:
            if pair[0] == key:
                pair[1] = value
                return
        items.append([key, value])


def _balanced(s: str) -> bool:
    """True if (), [], {} and string quotes are balanced in s.

    Used to detect multi-line values (e.g. the Input map dicts in project.godot).
    """
    depth = 0
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        i += 1
    return depth <= 0 and not in_str
