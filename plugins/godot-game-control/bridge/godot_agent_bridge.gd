# SPDX-License-Identifier: MPL-2.0

extends Node
## Game-owned input and viewport capture for Godot Game Control.

const MAX_REQUEST := 16384
const FRAME_EDGE := 1000
const MOUSE_BUTTONS := {
	"left": MOUSE_BUTTON_LEFT, "right": MOUSE_BUTTON_RIGHT, "middle": MOUSE_BUTTON_MIDDLE,
	"wheel_up": MOUSE_BUTTON_WHEEL_UP, "wheel_down": MOUSE_BUTTON_WHEEL_DOWN,
}
const JOY_BUTTONS := {
	"a": JOY_BUTTON_A, "b": JOY_BUTTON_B, "x": JOY_BUTTON_X, "y": JOY_BUTTON_Y,
	"back": JOY_BUTTON_BACK, "guide": JOY_BUTTON_GUIDE, "start": JOY_BUTTON_START,
	"left_stick": JOY_BUTTON_LEFT_STICK, "right_stick": JOY_BUTTON_RIGHT_STICK,
	"left_shoulder": JOY_BUTTON_LEFT_SHOULDER, "right_shoulder": JOY_BUTTON_RIGHT_SHOULDER,
	"dpad_up": JOY_BUTTON_DPAD_UP, "dpad_down": JOY_BUTTON_DPAD_DOWN,
	"dpad_left": JOY_BUTTON_DPAD_LEFT, "dpad_right": JOY_BUTTON_DPAD_RIGHT,
}
const JOY_AXES := {
	"left_x": JOY_AXIS_LEFT_X, "left_y": JOY_AXIS_LEFT_Y,
	"right_x": JOY_AXIS_RIGHT_X, "right_y": JOY_AXIS_RIGHT_Y,
	"left_trigger": JOY_AXIS_TRIGGER_LEFT, "right_trigger": JOY_AXIS_TRIGGER_RIGHT,
}

var peer := StreamPeerTCP.new()
var token := ""
var announced := false
var busy := false
var buffer := PackedByteArray()
var held: Array[InputEvent] = []
var cursor := Vector2(0.5, 0.5)
var mouse_mask := 0


func _ready() -> void:
	set_process(false)
	var port := OS.get_environment("GODOT_AGENT_PORT").to_int()
	token = OS.get_environment("GODOT_AGENT_TOKEN")
	if port < 1 or port > 65535 or token.length() != 64:
		return
	OS.unset_environment("GODOT_AGENT_PORT")
	OS.unset_environment("GODOT_AGENT_TOKEN")
	process_mode = Node.PROCESS_MODE_ALWAYS
	if peer.connect_to_host("127.0.0.1", port) == OK:
		set_process(true)


func _process(_delta: float) -> void:
	peer.poll()
	match peer.get_status():
		StreamPeerTCP.STATUS_CONNECTING:
			return
		StreamPeerTCP.STATUS_CONNECTED:
			if not announced:
				announced = true
				peer.set_no_delay(true)
				_send({"protocol": 1, "token": token})
				token = ""
		_:
			_disconnect()
			return
	if busy:
		return
	var available := peer.get_available_bytes()
	if available > 0:
		var chunk := peer.get_partial_data(mini(available, MAX_REQUEST))
		if chunk[0] != OK:
			_disconnect()
			return
		buffer.append_array(chunk[1])
	var boundary := buffer.find(10)
	if boundary == -1:
		if buffer.size() >= MAX_REQUEST:
			_disconnect()
		return
	var message: Variant = JSON.parse_string(buffer.slice(0, boundary).get_string_from_utf8())
	buffer = buffer.slice(boundary + 1)
	if message is Dictionary:
		_handle(message)
	else:
		_send({"error": "Invalid game command."})


func _disconnect() -> void:
	_release()
	peer.disconnect_from_host()
	set_process(false)
	if announced:
		get_tree().quit()


func _send(value: Dictionary) -> void:
	var data := (JSON.stringify(value) + "\n").to_utf8_buffer()
	if data.size() > 1900000:
		data = "{\"error\":\"Game observation exceeded the reply limit.\"}\n".to_utf8_buffer()
	if peer.put_data(data) != OK:
		_disconnect()


func _handle(message: Dictionary) -> void:
	match message.get("cmd"):
		"quit":
			_release()
			_send({"stopping": true})
			get_tree().quit()
		"observe":
			_send(_observe(message.get("frame", false)))
		"input":
			var events: Array = message.get("events", [])
			for spec: Dictionary in events:
				match spec.get("type"):
					"key":
						if OS.find_keycode_from_string(spec.name) == KEY_NONE:
							_send({"error": "Unknown Godot key: " + spec.name})
							return
					"action":
						if not InputMap.has_action(spec.name):
							_send({"error": "Unknown Godot input action: " + spec.name})
							return
			busy = true
			for spec: Dictionary in events:
				_apply(spec)
			await get_tree().create_timer(float(message.hold_ms) / 1000.0, true, false, true).timeout
			_release()
			if message.settle_ms > 0:
				await get_tree().create_timer(float(message.settle_ms) / 1000.0, true, false, true).timeout
			if peer.get_status() == StreamPeerTCP.STATUS_CONNECTED:
				_send(_observe(message.get("frame", false)))
			busy = false
		_:
			_send({"error": "Unknown game command."})


func _viewport_position() -> Vector2:
	var root := get_tree().root
	var rect := root.get_visible_rect()
	return root.get_final_transform() * (rect.position + cursor * (rect.size - Vector2.ONE))


func _set_modifiers(event: InputEventWithModifiers) -> void:
	event.shift_pressed = Input.is_key_pressed(KEY_SHIFT)
	event.ctrl_pressed = Input.is_key_pressed(KEY_CTRL)
	event.alt_pressed = Input.is_key_pressed(KEY_ALT)
	event.meta_pressed = Input.is_key_pressed(KEY_META)


func _dispatch(event: InputEvent) -> void:
	if event is InputEventWithModifiers:
		_set_modifiers(event)
	Input.parse_input_event(event)
	Input.flush_buffered_events()


func _apply(spec: Dictionary) -> void:
	var event: InputEvent
	match spec.type:
		"key":
			var key := InputEventKey.new()
			key.keycode = OS.find_keycode_from_string(spec.name)
			key.physical_keycode = key.keycode
			key.pressed = true
			if spec.name.length() == 1:
				var character: String = spec.name.to_upper() if Input.is_key_pressed(KEY_SHIFT) else spec.name.to_lower()
				key.unicode = character.unicode_at(0)
			elif key.keycode == KEY_SPACE:
				key.unicode = 32
			event = key
		"mouse_move", "mouse_delta":
			var motion := InputEventMouseMotion.new()
			var before := _viewport_position()
			if spec.type == "mouse_move":
				cursor = Vector2(spec.x, spec.y)
				motion.relative = _viewport_position() - before
			else:
				motion.relative = Vector2(spec.dx, spec.dy)
				var size := get_tree().root.get_visible_rect().size - Vector2.ONE
				cursor = (cursor + motion.relative / size.max(Vector2.ONE)).clamp(Vector2.ZERO, Vector2.ONE)
			motion.screen_relative = motion.relative
			motion.position = _viewport_position()
			motion.global_position = motion.position
			motion.button_mask = mouse_mask as MouseButtonMask
			_dispatch(motion)
			return
		"mouse_button":
			var button := InputEventMouseButton.new()
			button.button_index = MOUSE_BUTTONS[spec.button]
			button.pressed = true
			button.position = _viewport_position()
			button.global_position = button.position
			if button.button_index >= MOUSE_BUTTON_WHEEL_UP:
				button.factor = 1.0
				button.button_mask = mouse_mask as MouseButtonMask
				_dispatch(button)
				var up := button.duplicate() as InputEventMouseButton
				up.pressed = false
				_dispatch(up)
				return
			mouse_mask |= 1 << (button.button_index - 1)
			button.button_mask = mouse_mask as MouseButtonMask
			event = button
		"joy_button":
			var button := InputEventJoypadButton.new()
			button.button_index = JOY_BUTTONS[spec.button]
			button.pressed = true
			button.pressure = 1.0
			event = button
		"joy_axis":
			var axis := InputEventJoypadMotion.new()
			axis.axis = JOY_AXES[spec.axis]
			axis.axis_value = spec.value
			event = axis
		"action":
			var action := InputEventAction.new()
			action.action = spec.name
			action.pressed = true
			action.strength = 1.0
			event = action
	if event != null:
		held.append(event)
		_dispatch(event)


func _release() -> void:
	for index in range(held.size() - 1, -1, -1):
		var event := held[index].duplicate() as InputEvent
		if event is InputEventJoypadMotion:
			event.axis_value = 0.0
		else:
			event.pressed = false
			if event is InputEventAction:
				event.strength = 0.0
			elif event is InputEventJoypadButton:
				event.pressure = 0.0
			elif event is InputEventMouseButton:
				mouse_mask &= ~(1 << (event.button_index - 1))
				event.button_mask = mouse_mask as MouseButtonMask
				event.position = _viewport_position()
				event.global_position = event.position
		_dispatch(event)
	held.clear()


func _observe(include_frame: bool) -> Dictionary:
	var tree := get_tree()
	var root := tree.root
	var size := root.get_visible_rect().size
	var scene := tree.current_scene
	var result := {
		"scene": scene.scene_file_path if scene else "",
		"viewport": {"width": int(size.x), "height": int(size.y)},
		"cursor": {"x": cursor.x, "y": cursor.y},
		"input_map": {},
	}
	for action in InputMap.get_actions():
		var setting := "input/" + action
		var default: Variant = ProjectSettings.property_get_revert(setting)
		if default == null or ProjectSettings.get_setting(setting) != default or action in [
			"ui_accept", "ui_cancel", "ui_left", "ui_right", "ui_up", "ui_down", "ui_focus_next",
		]:
			var bindings: Array[String] = []
			for event in InputMap.action_get_events(action):
				bindings.append(event.as_text())
			result.input_map[action] = bindings
	if scene and scene.has_method("godot_agent_observe"):
		var state: Variant = scene.call("godot_agent_observe")
		var encoded := JSON.stringify(state)
		if encoded.length() <= 16000:
			result.game_state = JSON.parse_string(encoded)
		else:
			result.game_state_status = "too_large"
	if include_frame:
		_capture(result, root)
	return result


func _capture(result: Dictionary, root: Window) -> void:
	if DisplayServer.get_name() == "headless":
		result.frame_status = "headless"
		return
	var image := root.get_texture().get_image()
	if image == null or image.is_empty():
		result.frame_status = "unavailable"
		return
	if root.use_hdr_2d:
		image.convert(Image.FORMAT_RGBA8)
		image.linear_to_srgb()
	var size := image.get_size()
	var scale := minf(1.0, float(FRAME_EDGE) / maxi(size.x, size.y))
	if scale < 1.0:
		image.resize(maxi(1, roundi(size.x * scale)), maxi(1, roundi(size.y * scale)), Image.INTERPOLATE_BILINEAR)
	var jpeg := image.save_jpg_to_buffer(0.82)
	if jpeg.is_empty():
		result.frame_status = "unavailable"
		return
	result.frame = {"width": image.get_width(), "height": image.get_height()}
	result.jpeg = Marshalls.raw_to_base64(jpeg)
