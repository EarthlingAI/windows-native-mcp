"""App tool — window launch, switch, resize, close, list-open, list-installed, restore."""
import ctypes
import ctypes.wintypes
import json
import logging
import shutil
import subprocess
import time
import winreg
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.uia import get_window_list, find_window
from windows_native_mcp.core.screen import get_screen_size
from windows_native_mcp.core.input import ensure_foreground

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Window message constants
WM_CLOSE = 0x0010
SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6
GW_OWNER = 4


class SHELLEXECUTEINFO(ctypes.Structure):
	_fields_ = [
		("cbSize", ctypes.wintypes.DWORD),
		("fMask", ctypes.c_ulong),
		("hwnd", ctypes.wintypes.HWND),
		("lpVerb", ctypes.c_wchar_p),
		("lpFile", ctypes.c_wchar_p),
		("lpParameters", ctypes.c_wchar_p),
		("lpDirectory", ctypes.c_wchar_p),
		("nShow", ctypes.c_int),
		("hInstApp", ctypes.wintypes.HINSTANCE),
		("lpIDList", ctypes.c_void_p),
		("lpClass", ctypes.c_wchar_p),
		("hkeyClass", ctypes.wintypes.HKEY),
		("dwHotKey", ctypes.wintypes.DWORD),
		("hIcon", ctypes.wintypes.HANDLE),
		("hProcess", ctypes.wintypes.HANDLE),
	]

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400

# Thread pool for non-blocking ShellExecuteExW calls
_launch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shell-launch")

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
	ensure_foreground(hwnd)


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


def _can_resolve(target: str) -> bool:
	"""Check if target resolves to something launchable.

	Checks PATH, App Paths registry, and protocol handlers.
	Returns False for unknown names (prevents ShellExecuteExW blocking).
	"""
	# Protocol URIs (ms-settings:, calculator:, etc.) — always allow
	if ":" in target:
		return True

	# Check PATH + PATHEXT (covers 'notepad', 'calc', etc.)
	if shutil.which(target):
		return True

	# Check App Paths registry (covers 'winword', 'excel', etc.)
	for suffix in ("", ".exe"):
		try:
			key = winreg.OpenKey(
				winreg.HKEY_LOCAL_MACHINE,
				rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{target}{suffix}",
			)
			winreg.CloseKey(key)
			return True
		except OSError:
			pass

	return False


def _shell_execute(target: str, args: str | None = None) -> tuple[bool, int]:
	"""Launch via ShellExecuteExW. Returns (success, hProcess handle).

	Uses pre-validation to avoid blocking on unknown app names,
	SEE_MASK_FLAG_NO_UI to suppress error dialogs, and a thread
	timeout as a safety net against unexpected blocking.
	"""
	# Pre-validate to avoid blocking on "Open with" dialog
	if not _can_resolve(target):
		return (False, 0)

	def _do_execute():
		sei = SHELLEXECUTEINFO()
		sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
		sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
		sei.lpVerb = "open"
		sei.lpFile = target
		sei.lpParameters = args
		sei.nShow = SW_SHOW
		success = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
		return (bool(success), sei.hProcess or 0)

	# Thread timeout safety net (5s) — catches edge cases that pass
	# validation but still show a blocking dialog
	future = _launch_pool.submit(_do_execute)
	try:
		return future.result(timeout=5)
	except FutureTimeout:
		logging.warning("ShellExecuteExW timed out for '%s' (dialog likely blocking)", target)
		return (False, 0)


def _is_error_dialog(hwnd: int) -> bool:
	"""Check if a window is a standard Windows error dialog (#32770)."""
	class_buf = ctypes.create_unicode_buffer(256)
	user32.GetClassNameW(hwnd, class_buf, 256)
	return class_buf.value == "#32770"


def _resolve_start_app(name: str) -> str | None:
	"""Query Start Menu for a matching app. Returns AppID or None."""
	# Sanitize to prevent PowerShell injection
	safe_name = name.replace("'", "''").replace("`", "``")
	try:
		result = subprocess.run(
			["powershell", "-NoProfile", "-Command",
			 f"Get-StartApps | Where-Object {{ $_.Name -like '*{safe_name}*' }} | Select-Object -First 1 -ExpandProperty AppID"],
			capture_output=True, text=True, timeout=5,
		)
		app_id = result.stdout.strip()
		return app_id if app_id else None
	except (subprocess.TimeoutExpired, OSError):
		return None


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
			Literal["launch", "switch", "resize", "close", "list-open", "list-installed", "restore"],
			Field(description="Operation mode"),
		],
		name: Annotated[
			str | None,
			Field(description="Application or window name (for launch/switch/resize/close/restore)"),
		] = None,
		handle: Annotated[
			int | None,
			Field(description="Window handle from list-open (more reliable than name)"),
		] = None,
		app_id: Annotated[
			str | None,
			Field(description="AppID from list-installed for precise launch (bypasses name resolution)"),
		] = None,
		args: Annotated[
			str | None,
			Field(description="Arguments to pass to the application (e.g., a file path or URL)"),
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

		Use 'list-open' to get open window handles for reliable targeting.
		Use 'list-installed' to discover launchable apps from the Start Menu.
		For switch mode, handle-based targeting is more reliable than name.
		"""
		if mode == "list-open":
			windows = get_window_list()
			logging.info(f"App list-open: {len(windows)} windows")
			return windows

		if mode == "list-installed":
			try:
				result = subprocess.run(
					["powershell", "-NoProfile", "-Command",
					 "Get-StartApps | ConvertTo-Json"],
					capture_output=True, text=True, timeout=10,
				)
				apps = json.loads(result.stdout) if result.stdout.strip() else []
				# PowerShell returns a single object (not array) if only one result
				if isinstance(apps, dict):
					apps = [apps]
				logging.info(f"App list-installed: {len(apps)} apps")
				return apps
			except (subprocess.TimeoutExpired, OSError) as e:
				raise ToolError(f"Failed to query installed apps: {e}")
			except json.JSONDecodeError:
				raise ToolError("Failed to parse installed apps list")

		if mode == "launch":
			if not name and not app_id:
				raise ToolError("'name' or 'app_id' is required for launch mode")

			launch_label = name or app_id

			# Pre-launch check: if app is already running, switch to it
			existing_windows = get_window_list()
			if name:
				for win in existing_windows:
					if name.lower() in win["title"].lower():
						hwnd = win["handle"]
						_switch_to_window(hwnd)
						desktop_state.invalidate()
						logging.info(f"App launch: '{name}' already running → switch to handle {hwnd}")
						return {
							"launched": launch_label,
							"handle": hwnd,
							"title": win["title"],
							"already_running": True,
						}

			# Snapshot window handles before launch (for diff-based detection)
			pre_handles = {w["handle"] for w in existing_windows}

			if app_id:
				# Direct AppID launch — skip PATH/registry resolution
				success, h_process = _shell_execute(f"shell:AppsFolder\\{app_id}")
				if not success:
					raise ToolError(
						f"Launch failed: AppID '{app_id}' not found. "
						"Verify the AppID from app(mode='list-installed')."
					)
			else:
				# Launch via ShellExecuteExW (pre-validates to avoid blocking dialogs)
				success, h_process = _shell_execute(name, args)
				if not success:
					# Fallback: try Start Menu / UWP app resolution
					resolved_id = _resolve_start_app(name)
					if resolved_id:
						success, h_process = _shell_execute(f"shell:AppsFolder\\{resolved_id}")
					if not success:
						raise ToolError(
							f"Launch failed: '{name}' not found. "
							"Not in PATH, App Paths registry, Start Menu, or protocol handlers."
						)

			# If we got a process handle, wait for it to be ready
			if h_process:
				try:
					user32.WaitForInputIdle(h_process, 5000)
				except (AttributeError, OSError):
					pass
				finally:
					kernel32.CloseHandle(h_process)

			# Poll for new window via handle diff (up to 10 seconds)
			for _ in range(100):
				time.sleep(0.1)
				for win in get_window_list():
					if win["handle"] not in pre_handles:
						# Check if it's an error dialog (false positive)
						if _is_error_dialog(win["handle"]):
							user32.PostMessageW(win["handle"], WM_CLOSE, 0, 0)
							desktop_state.invalidate()
							raise ToolError(
								f"Launch failed: '{launch_label}' not found "
								"(Windows error dialog detected)"
							)
						desktop_state.invalidate()
						logging.info(f"App launch: '{launch_label}' → handle {win['handle']}")
						return {
							"launched": launch_label,
							"handle": win["handle"],
							"title": win["title"],
						}

			desktop_state.invalidate()
			logging.info(f"App launch: '{launch_label}' (window not detected)")
			return {
				"launched": launch_label,
				"handle": None,
				"note": "App started but no new window detected within 10s. Use app(mode='list-open') to find it.",
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
			# Prefer the minimized window among name matches
			if name and handle is None:
				windows = get_window_list()
				minimized = [w for w in windows if name.lower() in w["title"].lower() and w["is_minimized"]]
				if minimized:
					hwnd = minimized[0]["handle"]
				else:
					hwnd = _find_window_handle(name, handle)
			else:
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
