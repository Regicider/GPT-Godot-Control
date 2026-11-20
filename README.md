# Godot Game Control

Godot Game Control lets Codex run and playtest your Godot game or application on Windows. It captures the running project's main viewport and sends keyboard, mouse, or controller input through Godot itself.

## Why Godot Game Control

Godot already has MCP tools that can see and play a running game. Godot Game Control is built around the **native Godot Project Manager's saved project list**. From a local Codex chat, you can pick one of your projects by name and start its main scene with the editor closed.

The bundled skill guides Codex through project selection, opt-in bridge setup, and playtesting. The toolset is focused on launching, observing, and playing the game. That's useful when you want Codex to reproduce a menu bug, check controller navigation, or verify a fix with access to the game viewport.

## Related projects

- For in-game viewport capture, keyboard, mouse, and controller input, and a recent-project list maintained by its own desktop app, see [Godot MCP Studio](https://www.godotmcpstudio.com/tools).
- For in-game screenshots and input alongside live editor and debugger tools, see [ConduitMCP](https://github.com/Advik-B/ConduitMCP).
- For frame-accurate input, screenshots, and telemetry through a bridge loaded when a configured project launches, see [evangelato/godot-mcp](https://github.com/evangelato/godot-mcp).
- For a Codex-installable Godot development skill with per-project MCP setup and engine-backed checks, see [godot-dev](https://github.com/ItsYUGAMES/godot-dev).
- For a Codex marketplace plugin that sets up an add-on and MCP configuration for one project, see [Godot Codex Bridge](https://github.com/itshege/godot-codex-bridge).

## What Codex can see and do

- **Find and launch a project.** List projects saved in Godot's Project Manager, open the native manager or editor, and launch a selected project's main scene.
- **Observe the running project.** Request a JPEG of the main viewport, see the current scene and input bindings, and read any game state your project exposes.
- **Playtest a flow.** Click buttons, move the mouse, hold keys, press controller buttons, move analog sticks, or trigger named Godot InputMap actions. Held inputs release automatically after each command.

Screenshots come from the selected project's viewport texture. The game bridge keeps input inside that game process. Project names and paths, requested screenshots, and any exposed game state can appear in Codex tool responses. This scope applies to Godot Game Control's tools. Codex permissions and other plugins are configured separately.

## Install

Use a **local Windows session in ChatGPT desktop or Codex**, **Godot 4.4 or later**, and **Python 3.11 or later** on PATH. Use the .NET edition of Godot for C# projects.

Clone the repository or extract the release ZIP to a folder you intend to keep. If your copy has an `agents` folder, rename it to **`.agents`** before installing or uploading. From the repository root, you can do that in PowerShell with `Rename-Item -LiteralPath '.\agents' -NewName '.agents'`.

From the folder containing `.agents/plugins/marketplace.json`, run these commands in PowerShell.

```powershell
python -m pip install -r .\plugins\godot-game-control\requirements.txt
codex plugin marketplace add .
codex plugin add godot-game-control@local-godot
```

After adding the marketplace, you can also install **Godot Game Control** from **Local Godot** in the desktop Plugins Directory. The plugin includes the skill that guides Codex through launch, setup, and playtesting. Start a new local chat and ask **“List my saved Godot projects.”** [OpenAI's local plugin instructions](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)

### Choose your Godot installation

The plugin finds `godot.exe`, `godot4.exe`, `godot-mono.exe`, or `godot4-mono.exe` on PATH. Restart ChatGPT desktop after changing PATH so it sees the new value. If your executable has a versioned filename or you use several Godot installations, enter the full path to the editor you want to use.

```powershell
[Environment]::SetEnvironmentVariable(
    'GODOT_AGENT_EXE',
    (Read-Host 'Full path to your Godot editor .exe').Trim('"'),
    'User'
)
```

`GODOT_AGENT_EXE` takes precedence over PATH. The plugin uses the selected editor's project list in `%APPDATA%\Godot\projects.cfg` or, in self-contained mode, `editor_data\projects.cfg` next to the executable. [Godot's self-contained editor paths](https://docs.godotengine.org/en/4.4/classes/class_editorpaths.html)

## Playtest a project

1. Add your source project to Godot's Project Manager. Open it once in the editor if Godot needs to import assets.
2. Close the editor for the first bridge setup. Ask Codex to launch the project and authorize installing its game bridge. Setup adds `godot_agent_bridge.gd` and a `GodotAgentBridge` autoload entry in `override.cfg`. The bridge activates in game sessions started by the plugin.
3. Give Codex a flow to test and an expected outcome. Use a windowed launch for visual checks or a headless launch for checks based on game state.

> Launch my project and test the Settings menu with a controller. Verify that Back returns to the title menu and restores focus to the Settings button. Report the steps and stop the game when done.

> Open the inventory, drag an item to a different slot, and check that its quantity and icon move together. Capture the game viewport if the move fails.

The play bridge is designed for Godot source projects that use ordinary engine input events. Visual tests cover the root game viewport. The project manager and editor commands open their native windows for you to use. To remove the bridge from a project, delete its script and the `GodotAgentBridge` entry from `override.cfg`.

### Expose game state for repeatable checks

For tests that are hard to verify visually, add a `godot_agent_observe()` method to the root of your current scene. Return JSON-compatible values such as the active menu, selected item, or level completion state. The agent can check those values after sending input, including in headless runs. Expose only the game state you want included in tool responses.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The project list is empty | Add the project in Godot's Project Manager and check which editor the plugin selected. Set `GODOT_AGENT_PROJECTS_FILE` to an alternate `projects.cfg` if needed. Project entries use absolute local Windows paths through regular directories. |
| Godot can't launch | Put the editor on PATH under a supported name or set `GODOT_AGENT_EXE` to its full path. |
| The game starts but input is unavailable | Check that the bridge is installed for that project and launch a fresh game through the plugin. |
| A headless run has no image | Relaunch in windowed mode for viewport screenshots. |

Set `GODOT_AGENT_EXCLUDED_DIRS` to a semicolon-separated list of absolute paths to exclude directories from the plugin's project lookup and launch scope.

## License

Godot Game Control is licensed under the [Mozilla Public License 2.0](LICENSE), a file-level copyleft license. If you distribute an MPL-covered file, including a modified copy of the game bridge, keep its license notice and make the covered source available to recipients under the MPL. You can choose the license for your own game files that contain no MPL-covered code. [Mozilla's MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)
