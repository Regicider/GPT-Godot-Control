# SPDX-License-Identifier: MPL-2.0

"""Run one owned Godot game and exchange bounded messages with its autoload."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

from projects import local_path, spawn_godot


MAX_REPLY = 2_000_000


def receive(reader) -> dict:
    raw = reader.readline(MAX_REPLY + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_REPLY:
        raise ValueError("Missing or oversized Godot bridge reply.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid Godot bridge reply.")
    return value


class Game:
    def __init__(self, project: dict, headless: bool):
        self.project = project
        self.headless = headless
        self.game_id = secrets.token_hex(12)
        self.token = secrets.token_hex(32)
        self.listener = None
        self.connection = None
        self.reader = None
        self.disconnected = False
        temp_dir = local_path(os.environ.get("TEMP") or Path.home() / "AppData" / "Local" / "Temp")
        self.scratch = tempfile.TemporaryDirectory(
            dir=temp_dir, prefix="godot-agent-", ignore_cleanup_errors=True,
        )
        self.log = Path(self.scratch.name) / "game.log"
        try:
            env = {}
            if project["bridge_installed"]:
                self.listener = socket.create_server(("127.0.0.1", 0), backlog=4)
                env = {
                    "GODOT_AGENT_PORT": str(self.listener.getsockname()[1]),
                    "GODOT_AGENT_TOKEN": self.token,
                }
            folder = local_path(project["path"])
            args = ["--path", str(folder), "--log-file", str(self.log)]
            if headless:
                args.append("--headless")
            self.process = spawn_godot(args, folder, env)
        except BaseException:
            self.close_wire()
            self.scratch.cleanup()
            raise

    def connect(self, seconds: float):
        if not self.listener:
            return
        deadline = time.monotonic() + seconds
        while self.process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.listener.settimeout(min(remaining, 0.2))
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            conn.settimeout(min(remaining, 0.5))
            reader = conn.makefile("rb")
            try:
                hello = receive(reader)
                if hello.get("protocol") != 1 or not secrets.compare_digest(
                    str(hello.get("token", "")), self.token
                ):
                    raise ValueError("Unexpected bridge handshake.")
            except (ValueError, TypeError, OSError, UnicodeError):
                reader.close()
                conn.close()
                continue
            self.connection, self.reader = conn, reader
            self.listener.close()
            self.listener = None
            return

    def state(self) -> dict:
        exit_code = self.process.poll()
        if exit_code is not None:
            state = "exited"
        elif self.connection:
            state = "ready"
        elif self.disconnected:
            state = "bridge_disconnected"
        elif self.listener:
            state = "waiting_for_bridge"
        else:
            state = "running_without_bridge"
        result = {
            "game_id": self.game_id, "project_id": self.project["project_id"],
            "project": self.project["name"], "pid": self.process.pid,
            "headless": self.headless, "status": state,
        }
        if exit_code is not None:
            result["exit_code"] = exit_code
            if exit_code and self.log.is_file():
                with self.log.open("rb") as stream:
                    stream.seek(max(0, self.log.stat().st_size - 1600))
                    result["log_tail"] = stream.read(1600).decode("utf-8", errors="replace")
        return result

    def request(self, message: dict) -> dict:
        self.connect(0.4)
        if self.process.poll() is not None or not self.connection:
            raise ValueError(f"Game status is {self.state()['status']}.")
        try:
            payload = json.dumps(message, ensure_ascii=True, allow_nan=False).encode() + b"\n"
            self.connection.settimeout(8)
            self.connection.sendall(payload)
            reply = receive(self.reader)
        except (ValueError, OSError, UnicodeError):
            self.close_wire()
            self.disconnected = True
            raise ValueError("Godot stopped replying. Input was not retried.") from None
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply

    def close_wire(self):
        for resource in (self.reader, self.connection, self.listener):
            if resource is not None:
                resource.close()
        self.reader = self.connection = self.listener = None

    def stop(self) -> dict:
        forced = False
        if self.process.poll() is None:
            if self.connection:
                try:
                    self.request({"cmd": "quit"})
                    self.process.wait(timeout=2)
                except (ValueError, subprocess.TimeoutExpired):
                    pass
            if self.process.poll() is None:
                forced = True
                self.process.terminate()
                self.process.wait(timeout=3)
        result = self.state() | {"forced": forced}
        self.close_wire()
        self.scratch.cleanup()
        return result
