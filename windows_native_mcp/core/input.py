"""Win32 SendInput wrapper for mouse and keyboard input.

Uses only ctypes (stdlib) — no pywin32, no comtypes.
All coordinates are logical pixels; multiply by scale_factor for physical.
"""
import ctypes
import ctypes.wintypes
import logging
import time

# --- Win32 Constants ---

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

WHEEL_DELTA = 120

SM_CXSCREEN = 0
SM_CYSCREEN = 1

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VK_RETURN = 0x0D
VK_TAB = 0x09

# --- Virtual Key Code Map ---

VK_MAP: dict[str, int] = {
	"enter": 0x0D,
	"return": 0x0D,
	"tab": 0x09,
	"escape": 0x1B,
	"esc": 0x1B,
	"backspace": 0x08,
	"delete": 0x2E,
	"del": 0x2E,
	"space": 0x20,
	"up": 0x26,
	"down": 0x28,
	"left": 0x25,
	"right": 0x27,
	"home": 0x24,
	"end": 0x23,
	"pageup": 0x21,
	"pagedown": 0x22,
	"insert": 0x2D,
	"capslock": 0x14,
	"numlock": 0x90,
	"scrolllock": 0x91,
	"printscreen": 0x2C,
	"pause": 0x13,
	"ctrl": 0x11,
	"control": 0x11,
	"shift": 0x10,
	"alt": 0x12,
	"menu": 0x12,
	"win": 0x5B,
	"lwin": 0x5B,
	"rwin": 0x5C,
	"apps": 0x5D,
	"plus": 0xBB,
	"minus": 0xBD,
	"equals": 0xBB,
	"comma": 0xBC,
	"period": 0xBE,
	"semicolon": 0xBA,
	"slash": 0xBF,
	"backslash": 0xDC,
	"bracketleft": 0xDB,
	"bracketright": 0xDD,
	"quote": 0xDE,
	"tilde": 0xC0,
	"f1": 0x70,
	"f2": 0x71,
	"f3": 0x72,
	"f4": 0x73,
	"f5": 0x74,
	"f6": 0x75,
	"f7": 0x76,
	"f8": 0x77,
	"f9": 0x78,
	"f10": 0x79,
	"f11": 0x7A,
	"f12": 0x7B,
}

MODIFIER_VKS: dict[str, int] = {
	"ctrl": 0x11,
	"control": 0x11,
	"shift": 0x10,
	"alt": 0x12,
	"win": 0x5B,
}

# --- Mouse Button Mappings ---

_BUTTON_DOWN: dict[str, int] = {
	"left": MOUSEEVENTF_LEFTDOWN,
	"right": MOUSEEVENTF_RIGHTDOWN,
	"middle": MOUSEEVENTF_MIDDLEDOWN,
}

_BUTTON_UP: dict[str, int] = {
	"left": MOUSEEVENTF_LEFTUP,
	"right": MOUSEEVENTF_RIGHTUP,
	"middle": MOUSEEVENTF_MIDDLEUP,
}


# --- ctypes Structures ---

class MOUSEINPUT(ctypes.Structure):
	_fields_ = [
		("dx", ctypes.wintypes.LONG),
		("dy", ctypes.wintypes.LONG),
		("mouseData", ctypes.wintypes.DWORD),
		("dwFlags", ctypes.wintypes.DWORD),
		("time", ctypes.wintypes.DWORD),
		("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
	]


class KEYBDINPUT(ctypes.Structure):
	_fields_ = [
		("wVk", ctypes.wintypes.WORD),
		("wScan", ctypes.wintypes.WORD),
		("dwFlags", ctypes.wintypes.DWORD),
		("time", ctypes.wintypes.DWORD),
		("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
	]


class _INPUT_UNION(ctypes.Union):
	_fields_ = [
		("mi", MOUSEINPUT),
		("ki", KEYBDINPUT),
	]


class INPUT(ctypes.Structure):
	_fields_ = [
		("type", ctypes.wintypes.DWORD),
		("union", _INPUT_UNION),
	]


# --- Private Helpers ---

def _send_inputs(*inputs: INPUT) -> int:
	"""Send INPUT structures via Win32 SendInput."""
	n = len(inputs)
	arr = (INPUT * n)(*inputs)
	size = ctypes.sizeof(INPUT)
	sent = ctypes.windll.user32.SendInput(n, arr, size)
	if sent != n:
		logging.warning("SendInput: sent %d of %d inputs", sent, n)
	return sent


def _make_mouse_input(
	dx: int = 0,
	dy: int = 0,
	flags: int = 0,
	mouse_data: int = 0,
) -> INPUT:
	"""Create a mouse INPUT structure."""
	inp = INPUT()
	inp.type = INPUT_MOUSE
	inp.union.mi.dx = dx
	inp.union.mi.dy = dy
	inp.union.mi.dwFlags = flags
	inp.union.mi.mouseData = mouse_data
	inp.union.mi.time = 0
	inp.union.mi.dwExtraInfo = None
	return inp


def _make_key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
	"""Create a keyboard INPUT structure."""
	inp = INPUT()
	inp.type = INPUT_KEYBOARD
	inp.union.ki.wVk = vk
	inp.union.ki.wScan = scan
	inp.union.ki.dwFlags = flags
	inp.union.ki.time = 0
	inp.union.ki.dwExtraInfo = None
	return inp


def _get_physical_screen_size() -> tuple[int, int]:
	"""Get primary screen size in physical pixels.

	With per-monitor DPI awareness (level 2), GetSystemMetrics returns
	physical pixel dimensions.
	"""
	try:
		w = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
		h = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
		if w > 0 and h > 0:
			return (w, h)
	except (AttributeError, OSError, OverflowError):
		pass
	return (1920, 1080)


def _to_absolute(x: int, y: int, scale_factor: float) -> tuple[int, int]:
	"""Convert logical coordinates to SendInput absolute coords (0-65535).

	Logical coords are multiplied by scale_factor to get physical pixels,
	then mapped to the 0-65536 range that MOUSEEVENTF_ABSOLUTE expects.
	"""
	phys_w, phys_h = _get_physical_screen_size()
	try:
		abs_x = int(x * scale_factor * 65536 / phys_w)
		abs_y = int(y * scale_factor * 65536 / phys_h)
		return (abs_x, abs_y)
	except (OverflowError, ValueError):
		return (32768, 32768)


# --- Mouse Functions ---

def mouse_move(x: int, y: int, scale_factor: float = 1.0) -> None:
	"""Move cursor to logical coordinates without clicking."""
	abs_x, abs_y = _to_absolute(x, y, scale_factor)
	inp = _make_mouse_input(
		dx=abs_x,
		dy=abs_y,
		flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
	)
	_send_inputs(inp)
	logging.info("Mouse move to (%d, %d)", x, y)


def mouse_click(
	x: int,
	y: int,
	button: str = "left",
	clicks: int = 1,
	scale_factor: float = 1.0,
) -> None:
	"""Click at logical coordinates.

	Args:
		x: Logical X coordinate.
		y: Logical Y coordinate.
		button: "left", "right", or "middle".
		clicks: Number of clicks. 0 = hover only (move without clicking).
		scale_factor: DPI scale factor for coordinate conversion.
	"""
	button = button.lower()
	if button not in _BUTTON_DOWN:
		logging.warning("Unknown mouse button '%s', defaulting to left", button)
		button = "left"

	abs_x, abs_y = _to_absolute(x, y, scale_factor)

	# Move to position
	move = _make_mouse_input(
		dx=abs_x,
		dy=abs_y,
		flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
	)

	if clicks == 0:
		_send_inputs(move)
		logging.info("Mouse hover at (%d, %d)", x, y)
		return

	# Build click inputs
	inputs: list[INPUT] = [move]
	down_flag = _BUTTON_DOWN[button]
	up_flag = _BUTTON_UP[button]

	for _ in range(clicks):
		inputs.append(_make_mouse_input(flags=down_flag))
		inputs.append(_make_mouse_input(flags=up_flag))

	_send_inputs(*inputs)
	logging.info("Mouse %s-click x%d at (%d, %d)", button, clicks, x, y)


def mouse_drag(
	x1: int,
	y1: int,
	x2: int,
	y2: int,
	button: str = "left",
	scale_factor: float = 1.0,
) -> None:
	"""Drag from (x1, y1) to (x2, y2)."""
	button = button.lower()
	if button not in _BUTTON_DOWN:
		logging.warning("Unknown mouse button '%s', defaulting to left", button)
		button = "left"

	abs_x1, abs_y1 = _to_absolute(x1, y1, scale_factor)
	abs_x2, abs_y2 = _to_absolute(x2, y2, scale_factor)
	down_flag = _BUTTON_DOWN[button]
	up_flag = _BUTTON_UP[button]

	# Move to start position
	_send_inputs(_make_mouse_input(
		dx=abs_x1, dy=abs_y1,
		flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
	))
	time.sleep(0.05)

	# Press button
	_send_inputs(_make_mouse_input(flags=down_flag))
	time.sleep(0.05)

	# Move to end position
	_send_inputs(_make_mouse_input(
		dx=abs_x2, dy=abs_y2,
		flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
	))
	time.sleep(0.05)

	# Release button
	_send_inputs(_make_mouse_input(flags=up_flag))

	logging.info("Mouse drag (%d,%d) -> (%d,%d) [%s]", x1, y1, x2, y2, button)


def mouse_scroll(
	x: int,
	y: int,
	direction: str,
	amount: int = 3,
	scale_factor: float = 1.0,
) -> None:
	"""Scroll at logical coordinates.

	Args:
		x: Logical X coordinate.
		y: Logical Y coordinate.
		direction: "up", "down", "left", or "right".
		amount: Number of scroll increments (each = WHEEL_DELTA).
		scale_factor: DPI scale factor.
	"""
	# Move to position first
	mouse_move(x, y, scale_factor)

	direction = direction.lower()
	if direction in ("up", "down"):
		delta = amount * WHEEL_DELTA if direction == "up" else -amount * WHEEL_DELTA
		inp = _make_mouse_input(flags=MOUSEEVENTF_WHEEL, mouse_data=delta)
	elif direction in ("left", "right"):
		delta = amount * WHEEL_DELTA if direction == "right" else -amount * WHEEL_DELTA
		inp = _make_mouse_input(flags=MOUSEEVENTF_HWHEEL, mouse_data=delta)
	else:
		logging.warning("Unknown scroll direction '%s'", direction)
		return

	_send_inputs(inp)
	logging.info("Mouse scroll %s x%d at (%d, %d)", direction, amount, x, y)


# --- Keyboard Functions ---

def hold_modifiers(mods: list[str]) -> list[INPUT]:
	"""Create keydown inputs for modifier keys.

	Returns the INPUT list (also sends them immediately).
	"""
	inputs = []
	for mod in mods:
		vk = MODIFIER_VKS.get(mod.lower())
		if vk is None:
			logging.warning("Unknown modifier '%s', skipping", mod)
			continue
		inputs.append(_make_key_input(vk=vk))
	if inputs:
		_send_inputs(*inputs)
	return inputs


def release_modifiers(mods: list[str]) -> list[INPUT]:
	"""Create keyup inputs for modifier keys (reverse order).

	Returns the INPUT list (also sends them immediately).
	"""
	inputs = []
	for mod in reversed(mods):
		vk = MODIFIER_VKS.get(mod.lower())
		if vk is None:
			continue
		inputs.append(_make_key_input(vk=vk, flags=KEYEVENTF_KEYUP))
	if inputs:
		_send_inputs(*inputs)
	return inputs


def key_combo(keys_str: str) -> None:
	"""Send a key combination like "ctrl+shift+s" or "alt+f4".

	Modifiers are held, the main key is tapped, then modifiers released
	in reverse order. Single characters use VkKeyScanW for VK lookup.
	"""
	parts = [k.strip().lower() for k in keys_str.split("+")]
	if not parts:
		return

	mods = []
	main_keys = []
	for part in parts:
		if part in MODIFIER_VKS:
			mods.append(part)
		else:
			main_keys.append(part)

	# Press modifiers
	if mods:
		hold_modifiers(mods)

	# Tap each main key
	for key in main_keys:
		vk = VK_MAP.get(key)
		if vk is None and len(key) == 1:
			# Single character — get VK code via VkKeyScanW
			result = ctypes.windll.user32.VkKeyScanW(ord(key))
			if result == -1:
				logging.warning("VkKeyScanW failed for '%s', skipping", key)
				continue
			vk = result & 0xFF
			# If VkKeyScan indicates shift is needed and shift isn't already held
			shift_needed = (result >> 8) & 0x01
			if shift_needed and "shift" not in mods:
				_send_inputs(_make_key_input(vk=0x10))  # VK_SHIFT down
				_send_inputs(
					_make_key_input(vk=vk),
					_make_key_input(vk=vk, flags=KEYEVENTF_KEYUP),
				)
				_send_inputs(_make_key_input(vk=0x10, flags=KEYEVENTF_KEYUP))
				continue
		elif vk is None:
			logging.warning("Unknown key '%s' in combo '%s'", key, keys_str)
			continue

		_send_inputs(
			_make_key_input(vk=vk),
			_make_key_input(vk=vk, flags=KEYEVENTF_KEYUP),
		)

	# Release modifiers in reverse order
	if mods:
		release_modifiers(mods)

	logging.info("Key combo: %s", keys_str)


def type_text_sendinput(text: str) -> None:
	"""Type text using KEYEVENTF_UNICODE for full character support.

	Sends UTF-16 code points directly via wScan — handles ALL printable
	characters including *, @, #, international chars, emoji, etc.
	Newlines are sent as Enter (VK_RETURN), tabs as Tab (VK_TAB).
	"""
	if not text:
		return

	inputs: list[INPUT] = []

	for char in text:
		if char == "\n":
			inputs.append(_make_key_input(vk=VK_RETURN))
			inputs.append(_make_key_input(vk=VK_RETURN, flags=KEYEVENTF_KEYUP))
		elif char == "\t":
			inputs.append(_make_key_input(vk=VK_TAB))
			inputs.append(_make_key_input(vk=VK_TAB, flags=KEYEVENTF_KEYUP))
		else:
			code = ord(char)
			inputs.append(_make_key_input(
				scan=code,
				flags=KEYEVENTF_UNICODE,
			))
			inputs.append(_make_key_input(
				scan=code,
				flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
			))

	if inputs:
		_send_inputs(*inputs)
		logging.info("Typed %d characters via SendInput Unicode", len(text))


def paste_text(text: str) -> None:
	"""Paste text via Win32 clipboard + Ctrl+V.

	Uses clipboard API directly via ctypes — no pyperclip or win32clipboard.
	Retries OpenClipboard up to 10 times (50ms apart) to handle transient
	clipboard locks from other processes. Raises RuntimeError on failure
	so callers can fall back to type_text_sendinput.
	"""
	kernel32 = ctypes.windll.kernel32
	user32 = ctypes.windll.user32

	# Set proper return/arg types for 64-bit pointer safety
	kernel32.GlobalAlloc.restype = ctypes.c_void_p
	kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
	kernel32.GlobalLock.restype = ctypes.c_void_p
	kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
	kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
	kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
	user32.SetClipboardData.restype = ctypes.c_void_p
	user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

	# Encode as UTF-16LE with null terminator
	encoded = text.encode("utf-16-le") + b"\x00\x00"

	# Open clipboard with retry (clipboard is a system-wide lock)
	clipboard_opened = False
	for attempt in range(10):
		if user32.OpenClipboard(0):
			clipboard_opened = True
			break
		time.sleep(0.05)

	if not clipboard_opened:
		raise RuntimeError("Failed to open clipboard after 10 retries (another process holds the lock)")

	try:
		user32.EmptyClipboard()

		# Allocate global memory
		h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
		if not h_mem:
			raise RuntimeError("GlobalAlloc failed for clipboard text")

		# Lock, copy, unlock
		p_mem = kernel32.GlobalLock(h_mem)
		if not p_mem:
			kernel32.GlobalFree(h_mem)
			raise RuntimeError("GlobalLock failed for clipboard text")

		ctypes.memmove(p_mem, encoded, len(encoded))
		kernel32.GlobalUnlock(h_mem)

		# Set clipboard data
		if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
			kernel32.GlobalFree(h_mem)
			raise RuntimeError("SetClipboardData failed")
		# Note: after successful SetClipboardData, system owns h_mem — do NOT free it
	finally:
		user32.CloseClipboard()

	# Ctrl+V immediately after CloseClipboard to minimize race window
	key_combo("ctrl+v")
	time.sleep(0.1)  # Wait for target app to process the paste

	logging.info("Pasted %d characters via clipboard", len(text))
