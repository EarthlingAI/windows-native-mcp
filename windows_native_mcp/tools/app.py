"""App tool — window launch, switch, resize, close, list, restore."""
import ctypes
import ctypes.wintypes
import logging
import subprocess
import time
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.uia import get_window_list, find_window
from windows_native_mcp.core.screen import get_screen_size

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Window message constants
WM_CLOSE = 0x0010
SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6
GW_OWNER = 4

# Size presets (calculated relative to screen dimensions)
SIZE_PRESETS = {
	"maximize", "left-half", "right-half", "top-half", "bottom-half",
	"top-left", "top-right", "bottom-left", "bottom-right",
	"center", "center-large",
}


def _find_window_handle(name: str | None, handle: int | None) -> int:
	"""Resolve a window by handle or name. Returns HWND or raises ToolError."""
	if handle is not None:
		if not user32.IsWindow(handle):
			raise ToolError(f"Window handle {handle} is not valid")
		return handle

	if name is None:
		raise ToolError("Either 'name' or 'handle' must be provided")

	# Exact match via FindWindowW
	hwnd = user32.FindWindowW(None, name)
	if hwnd:
		return hwnd

	# Substring match via window list
	windows = get_window_list()
	for win in windows:
		if name.lower() in win["title"].lower():
			return win["handle"]

	raise ToolError(
		f"No window found matching '{name}'. "
		f"Available windows: {[w['title'] for w in get_window_list() if w['title']]}"
	)


def _is_minimized(hwnd: int) -> bool:
	return bool(user32.IsIconic(hwnd))


def _switch_to_window(hwnd: int):
	"""Bring a window to the foreground using Win11-compatible approach."""
	# Restore if minimized
	if _is_minimized(hwnd):
		user32.ShowWindow(hwnd, SW_RESTORE)
		time.sleep(0.1)

	# AttachThreadInput trick for Win11's SetForegroundWindow restrictions
	foreground = user32.GetForegroundWindow()
	target_tid = user32.GetWindowThreadProcessId(hwnd, None)
	current_tid = kernel32.GetCurrentThreadId()

	if foreground != hwnd:
		fg_tid = user32.GetWindowThreadProcessId(foreground, None)
		# Attach to foreground thread to inherit its foreground rights
		user32.AttachThreadInput(current_tid, fg_tid, True)
		try:
			user32.BringWindowToTop(hwnd)
			user32.SetForegroundWindow(hwnd)
		finally:
			user32.AttachThreadInput(current_tid, fg_tid, False)

	# Verify
	time.sleep(0.1)
	actual_fg = user32.GetForegroundWindow()
	if actual_fg != hwnd:
		# Fallback: ShowWindow + SetForegroundWindow
		user32.ShowWindow(hwnd, SW_SHOW)
		user32.SetForegroundWindow(hwnd)


def _calculate_preset_rect(preset: str, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
	"""Calculate (x, y, width, height) for a size preset."""
	half_w = screen_w // 2
	half_h = screen_h // 2

	presets = {
		"maximize": (0, 0, screen_w, screen_h),
		"left-half": (0, 0, half_w, screen_h),
		"right-half": (half_w, 0, half_w, screen_h),
		"top-half": (0, 0, screen_w, half_h),
		"bottom-half": (0, half_h, screen_w, half_h),
		"top-left": (0, 0, half_w, half_h),
		"top-right": (half_w, 0, half_w, half_h),
		"bottom-left": (0, half_h, half_w, half_h),
		"bottom-right": (half_w, half_h, half_w, half_h),
		"center": (screen_w // 4, screen_h // 4, half_w, half_h),
		"center-large": (screen_w // 8, screen_h // 8, screen_w * 3 // 4, screen_h * 3 // 4),
	}

	if preset not in presets:
		raise ToolError(f"Unknown preset '{preset}'. Available: {', '.join(sorted(presets))}")
	return presets[preset]


def register(mcp: FastMCP):
	"""Register the app tool."""

	@mcp.tool(
		name="app",
		annotations=ToolAnnotations(
			title="Application Manager",
			readOnlyHint=False,
			destructiveHint=False,
			idempotentHint=False,
			openWorldHint=False,
		),
	)
	def app(
		mode: Annotated[
			Literal["launch", "switch", "resize", "close", "list", "restore"],
			Field(description="Operation mode"),
		],
		name: Annotated[
			str | None,
			Field(description="Application or window name (for launch/switch/resize/close/restore)"),
		] = None,
		handle: Annotated[
			int | None,
			Field(description="Window handle from list mode (more reliable than name)"),
		] = None,
		size: Annotated[
			list[int] | str | None,
			Field(description='[width, height] or preset name: "maximize", "left-half", "right-half", "center", etc.'),
		] = None,
		position: Annotated[
			list[int] | None,
			Field(description="[x, y] window position (used with size as [w,h])"),
		] = None,
	) -> dict | list:
		"""Manage application windows: launch, switch focus, resize, close, list, or restore.

		Use 'list' mode first to get window handles for reliable targeting.
		For switch mode, handle-based targeting is more reliable than name.
		"""
		if mode == "list":
			windows = get_window_list()
			logging.info(f"App list: {len(windows)} windows")
			return windows

		if mode == "launch":
			if not name:
				raise ToolError("'name' is required for launch mode")

			# Pre-launch check: if app is already running, switch to it
			existing_windows = get_window_list()
			for win in existing_windows:
				if name.lower() in win["title"].lower():
					hwnd = win["handle"]
					_switch_to_window(hwnd)
					desktop_state.invalidate()
					logging.info(f"App launch: '{name}' already running → switch to handle {hwnd}")
					return {
						"launched": name,
						"handle": hwnd,
						"title": win["title"],
						"already_running": True,
					}

			# Not running — launch via cmd /c start
			try:
				subprocess.Popen(
					["cmd", "/c", "start", "", name],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
				)
			except OSError as e:
				raise ToolError(f"Failed to launch '{name}': {e}")

			# Poll for new window (up to 5 seconds)
			for _ in range(50):
				time.sleep(0.1)
				windows = get_window_list()
				for win in windows:
					if name.lower() in win["title"].lower():
						desktop_state.invalidate()
						logging.info(f"App launch: '{name}' → handle {win['handle']}")
						return {
							"launched": name,
							"handle": win["handle"],
							"title": win["title"],
						}

			desktop_state.invalidate()
			logging.info(f"App launch: '{name}' (window not detected)")
			return {
				"launched": name,
				"handle": None,
				"note": "App started but window not detected within 5s. Use list mode to find it.",
			}

		if mode == "switch":
			hwnd = _find_window_handle(name, handle)
			_switch_to_window(hwnd)
			desktop_state.invalidate()

			# Verify focus
			actual_fg = user32.GetForegroundWindow()
			success = actual_fg == hwnd

			title = ctypes.create_unicode_buffer(256)
			user32.GetWindowTextW(hwnd, title, 256)

			logging.info(f"App switch: '{title.value}' (handle={hwnd}, success={success})")
			return {
				"switched_to": title.value,
				"handle": hwnd,
				"success": success,
				"note": "Window may not have received focus" if not success else None,
			}

		if mode == "resize":
			hwnd = _find_window_handle(name, handle)

			# Restore from minimized first
			if _is_minimized(hwnd):
				user32.ShowWindow(hwnd, SW_RESTORE)
				time.sleep(0.1)

			if isinstance(size, str):
				# Preset
				if size == "maximize":
					user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
					desktop_state.invalidate()
					return {"resized": size, "handle": hwnd}

				screen_w, screen_h = get_screen_size()
				x, y, w, h = _calculate_preset_rect(size, screen_w, screen_h)
			elif isinstance(size, list) and len(size) == 2:
				w, h = size
				if position and len(position) == 2:
					x, y = position
				else:
					# Center on screen
					screen_w, screen_h = get_screen_size()
					x = (screen_w - w) // 2
					y = (screen_h - h) // 2
			else:
				raise ToolError("'size' must be [width, height] or a preset name")

			user32.MoveWindow(hwnd, x, y, w, h, True)
			desktop_state.invalidate()

			logging.info(f"App resize: handle={hwnd} → ({x},{y},{w},{h})")
			return {"resized": True, "handle": hwnd, "rect": {"x": x, "y": y, "width": w, "height": h}}

		if mode == "close":
			hwnd = _find_window_handle(name, handle)

			title = ctypes.create_unicode_buffer(256)
			user32.GetWindowTextW(hwnd, title, 256)

			user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
			desktop_state.invalidate()

			logging.info(f"App close: '{title.value}' (handle={hwnd})")
			return {"closed": title.value, "handle": hwnd}

		if mode == "restore":
			hwnd = _find_window_handle(name, handle)

			title = ctypes.create_unicode_buffer(256)
			user32.GetWindowTextW(hwnd, title, 256)

			if _is_minimized(hwnd):
				user32.ShowWindow(hwnd, SW_RESTORE)
				_switch_to_window(hwnd)
				desktop_state.invalidate()
				logging.info(f"App restore: '{title.value}' (handle={hwnd})")
				return {"restored": title.value, "handle": hwnd, "was_minimized": True}
			else:
				return {"restored": title.value, "handle": hwnd, "was_minimized": False, "note": "Window was not minimized"}

		raise ToolError(f"Unknown mode '{mode}'")
