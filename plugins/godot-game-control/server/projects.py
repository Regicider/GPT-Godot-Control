# SPDX-License-Identifier: MPL-2.0

"""Godot 4 Project Manager registry and Windows engine launches."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import winreg


PLUGIN = Path(__file__).resolve().parents[1]
BRIDGE_FILE = "godot_agent_bridge.gd"
BRIDGE_AUTOLOAD = 'GodotAgentBridge="*res://godot_agent_bridge.gd"'
BRIDGE_LICENSE_HEADER = b"# SPDX-License-Identifier: MPL-2.0\n\n"
SECTION = re.compile(r"^\s*\[(.*)\]\s*(?:;.*)?$")
EXECUTABLE_NAMES = ("godot.exe", "godot4.exe", "godot-mono.exe", "godot4-mono.exe")


def normalized_path(value: str | Path) -> Path:
    raw = os.fspath(value).strip('"')
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    path = Path(raw)
    if not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive):
        raise ValueError("Use an absolute local Windows drive path.")
    if any(
        part != ".." and (part.endswith((" ", ".")) or "~" in part or ":" in part)
        for part in path.parts[1:]
    ):
        raise ValueError("Use a full local path without filesystem aliases.")
    return Path(os.path.normpath(path))


def registry_setting(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return os.path.expandvars(value) if isinstance(value, str) else None
    except FileNotFoundError:
        return None


def user_setting(name: str) -> str | None:
    return os.environ.get(name) or registry_setting(name)


def local_path(value: str | Path, *, follow_links: bool = False) -> Path:
    """Check the path and every redirect before accessing any target."""
    path = normalized_path(value)
    name = "GODOT_AGENT_EXCLUDED_DIRS"
    exclusions = tuple(
        normalized_path(item.strip())
        for setting in (os.environ.get(name), registry_setting(name))
        if setting
        for item in setting.split(os.pathsep)
        if item.strip()
    )
    for _ in range(16):
        if any(path.is_relative_to(root) for root in exclusions):
            raise ValueError("Path is outside the permitted scope.")
        for part in (*reversed(path.parents), path):
            try:
                attrs = part.lstat().st_file_attributes
            except FileNotFoundError:
                return path
            if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                if not follow_links:
                    raise ValueError("Use a path without junctions or symlinks.")
                target = part.parent / os.readlink(part)
                path = normalized_path(target / path.relative_to(part))
                break
        else:
            return path
    raise ValueError("Too many filesystem redirects.")


def native_registry_path(executable: Path | None) -> Path:
    if executable is not None:
        for marker in ("._sc_", "_sc_"):
            if local_path(executable.parent / marker).is_file():
                return local_path(executable.parent / "editor_data" / "projects.cfg")
    app_data = os.environ.get("APPDATA")
    if not app_data:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        ) as key:
            app_data, _ = winreg.QueryValueEx(key, "AppData")
    return local_path(Path(app_data) / "Godot" / "projects.cfg")


def registry_path() -> Path:
    custom = user_setting("GODOT_AGENT_PROJECTS_FILE")
    if custom:
        return local_path(custom)
    try:
        executable = godot_executable()
    except (ValueError, OSError):
        executable = None
    return native_registry_path(executable)


def registry_rows(path: Path) -> list[tuple[str, bool]]:
    rows = {}
    section = None
    if not path.is_file():
        return []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = SECTION.fullmatch(line)
        if match:
            section = match[1]
            rows.setdefault(section, False)
        elif section and re.fullmatch(r"\s*favorite\s*=\s*true\s*(?:;.*)?", line):
            rows[section] = True
    return list(rows.items())


def available_projects(registry: Path) -> tuple[list[dict], int]:
    projects = {}
    skipped = 0
    for raw, favorite in registry_rows(registry):
        try:
            folder = local_path(raw)
            config = local_path(folder / "project.godot")
            project_source = config.read_text(encoding="utf-8-sig")
        except (ValueError, OSError, UnicodeError):
            skipped += 1
            continue
        name = folder.name
        match = re.search(r'^\s*config/name\s*=\s*("(?:[^"\\]|\\.)*")', project_source, re.M)
        if match:
            try:
                name = json.loads(match[1]) or name
            except ValueError:
                pass
        key = hashlib.sha256(os.path.normcase(str(folder)).encode()).hexdigest()[:20]
        projects[key] = {
            "project_id": key, "name": name, "path": str(folder),
            "favorite": favorite, "bridge_installed": bridge_installed(folder),
        }
    return sorted(projects.values(), key=lambda item: (not item["favorite"], item["name"].casefold())), skipped


def select_project(project_id: str) -> dict:
    projects, _ = available_projects(registry_path())
    for project in projects:
        if project["project_id"] == project_id:
            return project
    raise ValueError("Project is unavailable. Select a project_id from list_projects.")


def bridge_installed(folder: Path) -> bool:
    try:
        bridge = local_path(folder / BRIDGE_FILE)
        override = local_path(folder / "override.cfg")
        return bridge.is_file() and BRIDGE_AUTOLOAD in override.read_text(encoding="utf-8-sig").splitlines()
    except (ValueError, OSError, UnicodeError):
        return False


def install_bridge(project: dict) -> dict:
    folder = local_path(project["path"])
    bridge = local_path(folder / BRIDGE_FILE)
    override = local_path(folder / "override.cfg")
    code = (PLUGIN / "bridge" / BRIDGE_FILE).read_bytes()
    found_bridge = bridge.exists()
    if found_bridge:
        existing = bridge.read_bytes().replace(b"\r\n", b"\n").removeprefix(BRIDGE_LICENSE_HEADER)
        expected = code.replace(b"\r\n", b"\n").removeprefix(BRIDGE_LICENSE_HEADER)
        if existing != expected:
            raise ValueError(f"{BRIDGE_FILE} already exists with different content.")

    old = override.read_bytes() if override.exists() else b""
    source = old.decode("utf-8")
    lines = source.splitlines(keepends=True)
    autoload_index = None
    registered = False
    section = ""
    for index, line in enumerate(lines):
        match = SECTION.fullmatch(line.rstrip("\r\n").lstrip("\ufeff"))
        if match:
            section = match[1]
            if section == "autoload":
                autoload_index = index
        elif section == "autoload" and re.match(r"\s*GodotAgentBridge\s*=", line):
            if line.strip() != BRIDGE_AUTOLOAD:
                raise ValueError("override.cfg already declares a different GodotAgentBridge.")
            registered = True

    config = local_path(folder / "project.godot").read_text(encoding="utf-8-sig")
    if re.search(r"^\s*GodotAgentBridge\s*=", config, re.M):
        raise ValueError("project.godot already declares GodotAgentBridge. Resolve that entry before setup.")
    if not found_bridge:
        with bridge.open("xb") as stream:
            stream.write(code)
    if not registered:
        newline = "\r\n" if "\r\n" in source else "\n"
        if autoload_index is None:
            separation = "" if not source or source.endswith("\n") else newline
            updated = source + separation + f"[autoload]{newline}{BRIDGE_AUTOLOAD}{newline}"
        else:
            header = lines[autoload_index]
            if not header.endswith("\n"):
                lines[autoload_index] += newline
            lines.insert(autoload_index + 1, BRIDGE_AUTOLOAD + newline)
            updated = "".join(lines)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".godot-agent-", delete=False) as scratch:
            pending = Path(scratch.name)
            scratch.write(updated.encode("utf-8"))
        try:
            if (override.read_bytes() if override.exists() else b"") != old:
                raise ValueError("override.cfg changed during setup. Retry with the editor closed.")
            pending.replace(override)
        finally:
            pending.unlink(missing_ok=True)
    return {
        "project_id": project["project_id"], "bridge_installed": True,
        "changed": not (found_bridge and registered),
    }


def godot_executable() -> Path:
    configured = user_setting("GODOT_AGENT_EXE")
    if configured:
        executable = local_path(configured, follow_links=True)
        if executable.suffix.lower() == ".exe" and executable.is_file():
            return executable
        raise ValueError("GODOT_AGENT_EXE must point to an existing Godot 4 Windows .exe.")
    for folder in os.get_exec_path():
        for name in EXECUTABLE_NAMES:
            try:
                executable = local_path(Path(folder.strip('"')) / name, follow_links=True)
                if executable.suffix.lower() == ".exe" and executable.is_file():
                    return executable
            except (ValueError, OSError):
                continue
    raise ValueError("Godot was not found on PATH. Add its directory to PATH under a recognized executable name, or set GODOT_AGENT_EXE to the full path of your Godot 4 .exe.")


def spawn_godot(
    args: list[str], cwd: Path, bridge_env: dict | None = None, *, executable: Path | None = None,
) -> subprocess.Popen:
    env = os.environ.copy()
    for name in ("GODOT_AGENT_PORT", "GODOT_AGENT_TOKEN"):
        env.pop(name, None)
    env.update(bridge_env or {})
    return subprocess.Popen(
        [str(executable or godot_executable()), *args], cwd=cwd, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
