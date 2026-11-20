# SPDX-License-Identifier: MPL-2.0

"""Desktop-only MCP entry point."""

import json
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from projects import (
    available_projects, godot_executable, install_bridge, local_path, native_registry_path,
    registry_path, registry_rows, select_project, spawn_godot, user_setting,
)
from session import Game


mcp = FastMCP("Godot Game Control", log_level="WARNING")
game: Game | None = None
opened = {}


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Key(Event):
    type: Literal["key"]
    name: str = Field(min_length=1, max_length=64, description="Godot key name, such as W, Space, or Escape.")


class MouseMove(Event):
    type: Literal["mouse_move"]
    x: float = Field(ge=0, le=1, description="Horizontal fraction of the game frame, from the left.")
    y: float = Field(ge=0, le=1, description="Vertical fraction of the game frame, from the top.")


class MouseDelta(Event):
    type: Literal["mouse_delta"]
    dx: float = Field(ge=-4096, le=4096, description="Horizontal motion in viewport pixels.")
    dy: float = Field(ge=-4096, le=4096, description="Vertical motion in viewport pixels.")


class MouseButton(Event):
    type: Literal["mouse_button"]
    button: Literal["left", "right", "middle", "wheel_up", "wheel_down"]


class JoyButton(Event):
    type: Literal["joy_button"]
    button: Literal["a", "b", "x", "y", "back", "guide", "start", "left_stick", "right_stick",
                    "left_shoulder", "right_shoulder", "dpad_up", "dpad_down", "dpad_left", "dpad_right"]


class JoyAxis(Event):
    type: Literal["joy_axis"]
    axis: Literal["left_x", "left_y", "right_x", "right_y", "left_trigger", "right_trigger"]
    value: float = Field(ge=-1, le=1)


class Action(Event):
    type: Literal["action"]
    name: str = Field(min_length=1, max_length=128, description="A Godot InputMap action from observe_game.")


InputEvent = Key | MouseMove | MouseDelta | MouseButton | JoyButton | JoyAxis | Action


def active_game(game_id: str) -> Game:
    if game is None or game.game_id != game_id:
        raise ValueError("Unknown game_id. Use the game_id returned by launch_game.")
    return game


def launch_window(key: str, args: list[str], folder: Path, *, executable: Path | None = None) -> dict:
    prior = opened.get(key)
    if prior is not None and prior.poll() is None:
        return {"pid": prior.pid, "already_open": True}
    opened[key] = spawn_godot(args, folder, executable=executable)
    return {"pid": opened[key].pid, "already_open": False}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_projects() -> dict:
    """Read the Windows Godot Project Manager's saved projects. Use project_id to select one."""
    registry = registry_path()
    projects, skipped = available_projects(registry)
    return {"projects": projects, "skipped_entries": skipped, "registry_exists": registry.is_file()}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def open_project_manager() -> dict:
    """Open the native Godot Project Manager. Use list_projects to select from its saved registry."""
    executable = godot_executable()
    registry = native_registry_path(executable)
    custom = user_setting("GODOT_AGENT_PROJECTS_FILE")
    if custom and local_path(custom) != registry:
        raise ValueError("GODOT_AGENT_PROJECTS_FILE differs from the native registry of the selected Godot executable.")
    for raw, _ in registry_rows(registry):
        try:
            folder = local_path(raw)
            local_path(folder / "project.godot")
        except ValueError:
            raise ValueError("The native Project Manager contains a path outside the plugin's permitted scope.") from None
    return launch_window("manager", ["--project-manager"], Path(__file__).parent, executable=executable)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def open_project_editor(project_id: str) -> dict:
    """Open a saved project in the native Godot editor."""
    project = select_project(project_id)
    folder = local_path(project["path"])
    return launch_window(project_id, ["--editor", "--path", str(folder)], folder)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def prepare_project(project_id: str) -> dict:
    """Install the scoped play bridge in a saved project. Writes godot_agent_bridge.gd and override.cfg."""
    project = select_project(project_id)
    editor = opened.get(project_id)
    if editor is not None and editor.poll() is None:
        raise ValueError("Close the Godot editor for this project before installing the bridge.")
    if game is not None and game.project["project_id"] == project_id and game.process.poll() is None:
        raise ValueError("Stop this game before installing the bridge.")
    return install_bridge(project)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def launch_game(project_id: str, headless: bool = False) -> dict:
    """Start the saved project's main game. Headless mode has no image. Only one plugin game can run at a time."""
    global game
    if game is not None:
        if game.process.poll() is None:
            raise ValueError("A plugin game is already running. Observe or stop it before launching another.")
        game.stop()
        game = None
    game = Game(select_project(project_id), headless)
    game.connect(6)
    return game.state()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def current_game() -> dict:
    """Get the current plugin-owned game's ID and process status, or idle if none has been launched."""
    if game is None:
        return {"status": "idle"}
    game.connect(0.4)
    return game.state()


def with_frame(result: dict) -> CallToolResult:
    jpeg = result.pop("jpeg", None)
    content = [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    if jpeg:
        content.append(ImageContent(type="image", data=jpeg, mimeType="image/jpeg"))
    return CallToolResult(content=content, structuredContent=result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def observe_game(game_id: str, include_frame: bool = True) -> CallToolResult:
    """Observe the running game. Returns its scene, project input bindings, optional game state, and optional game-viewport JPEG."""
    running = active_game(game_id)
    info = running.state()
    if info["status"] in ("ready", "waiting_for_bridge"):
        try:
            info |= running.request({"cmd": "observe", "frame": include_frame})
        except ValueError as error:
            info = running.state() | {"message": str(error)}
    return with_frame(info)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def send_game_input(
    game_id: str,
    events: Annotated[list[InputEvent], Field(min_length=1, max_length=12)],
    hold_ms: Annotated[int, Field(ge=40, le=3000)] = 120,
    settle_ms: Annotated[int, Field(ge=0, le=1000)] = 80,
    include_frame: bool = False,
) -> CallToolResult:
    """Send an ordered chord of scoped keyboard, mouse, controller, or named action events. Holds release automatically."""
    running = active_game(game_id)
    result = running.request({
        "cmd": "input", "events": [event.model_dump() for event in events],
        "hold_ms": hold_ms, "settle_ms": settle_ms, "frame": include_frame,
    })
    return with_frame(running.state() | result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def stop_game(game_id: str) -> dict:
    """Stop only the game process launched by this plugin instance."""
    return active_game(game_id).stop()


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    finally:
        if game is not None:
            game.stop()
