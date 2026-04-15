"""Screenshot capture, DPI handling, annotation drawing, and monitor management.

Uses MSS for capture (DPI-aware, no Pillow DPI bugs on Win11).
Uses Pillow for annotation drawing only.
Cursor is always composited onto screenshots via Win32 GetCursorInfo.
"""
import ctypes
import ctypes.wintypes
import io
import logging
import math
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw, ImageFont

from windows_native_mcp.core.state import ElementInfo

# Set DPI awareness before any screenshot library imports
try:
	ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
	try:
		ctypes.windll.user32.SetProcessDPIAware()
	except (AttributeError, OSError):
		pass


# --- Monitor Management ---

@dataclass
class MonitorInfo:
	"""Information about a display monitor."""
	index: int                            # 1-based
	handle: int                           # HMONITOR
	rect: tuple[int, int, int, int]       # (left, top, right, bottom) logical pixels
	work_rect: tuple[int, int, int, int]  # excludes taskbar
	dpi_scale: float                      # e.g. 1.25
	primary: bool
	width: int                            # convenience: rect[2] - rect[0]
	height: int                           # convenience: rect[3] - rect[1]


class _MONITORINFOEXW(ctypes.Structure):
	_fields_ = [
		("cbSize", ctypes.wintypes.DWORD),
		("rcMonitor", ctypes.wintypes.RECT),
		("rcWork", ctypes.wintypes.RECT),
		("dwFlags", ctypes.wintypes.DWORD),
		("szDevice", ctypes.c_wchar * 32),
	]


_MONITORINFOF_PRIMARY = 0x00000001
_MONITOR_DEFAULTTONEAREST = 0x00000002
_MDT_EFFECTIVE_DPI = 0


def _get_monitor_dpi(hmonitor: int) -> float:
	"""Get effective DPI scale for a monitor handle."""
	try:
		dpi_x = ctypes.c_uint()
		dpi_y = ctypes.c_uint()
		hr = ctypes.windll.shcore.GetDpiForMonitor(
			hmonitor, _MDT_EFFECTIVE_DPI,
			ctypes.byref(dpi_x), ctypes.byref(dpi_y),
		)
		if hr == 0 and dpi_x.value > 0:
			return dpi_x.value / 96.0
	except (AttributeError, OSError):
		pass
	# Fallback: use primary monitor DPI via GetDC
	try:
		ctypes.windll.user32.GetDC.argtypes = [ctypes.c_void_p]
		ctypes.windll.user32.GetDC.restype = ctypes.c_void_p
		ctypes.windll.gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
		ctypes.windll.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
		hdc = ctypes.windll.user32.GetDC(0)
		dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
		ctypes.windll.user32.ReleaseDC(0, hdc)
		return dpi / 96.0
	except (AttributeError, OSError, OverflowError):
		return 1.0


def enumerate_monitors() -> list[MonitorInfo]:
	"""Enumerate all display monitors with their geometry and DPI.

	Returns a sorted list: primary monitor as index 1,
	then others sorted by left edge position (left-to-right).
	"""
	raw: list[dict] = []

	MONITORENUMPROC = ctypes.WINFUNCTYPE(
		ctypes.c_int,
		ctypes.c_void_p,   # hMonitor
		ctypes.c_void_p,   # hdcMonitor
		ctypes.POINTER(ctypes.wintypes.RECT),  # lprcMonitor
		ctypes.c_longlong,  # dwData
	)

	def _callback(hmonitor, _hdc, _lprect, _data):
		info = _MONITORINFOEXW()
		info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
		if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
			is_primary = bool(info.dwFlags & _MONITORINFOF_PRIMARY)
			rm = info.rcMonitor
			rw = info.rcWork
			dpi_scale = _get_monitor_dpi(hmonitor)
			raw.append({
				"handle": hmonitor,
				"rect": (rm.left, rm.top, rm.right, rm.bottom),
				"work_rect": (rw.left, rw.top, rw.right, rw.bottom),
				"dpi_scale": dpi_scale,
				"primary": is_primary,
			})
		return 1  # Continue enumeration

	callback = MONITORENUMPROC(_callback)
	ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)

	# Sort: primary first, then by left edge
	raw.sort(key=lambda m: (not m["primary"], m["rect"][0]))

	monitors = []
	for idx, m in enumerate(raw, start=1):
		r = m["rect"]
		monitors.append(MonitorInfo(
			index=idx,
			handle=m["handle"],
			rect=m["rect"],
			work_rect=m["work_rect"],
			dpi_scale=m["dpi_scale"],
			primary=m["primary"],
			width=r[2] - r[0],
			height=r[3] - r[1],
		))
	return monitors


def get_primary_monitor() -> MonitorInfo:
	"""Get the primary monitor. Falls back to synthetic defaults."""
	monitors = enumerate_monitors()
	for m in monitors:
		if m.primary:
			return m
	if monitors:
		return monitors[0]
	# Fallback: no monitors detected (e.g. headless)
	return MonitorInfo(
		index=1, handle=0,
		rect=(0, 0, 1920, 1080), work_rect=(0, 0, 1920, 1040),
		dpi_scale=1.0, primary=True, width=1920, height=1080,
	)


def get_monitor_by_index(index: int, monitors: list[MonitorInfo] | None = None) -> MonitorInfo:
	"""Get a monitor by 1-based index. Raises ToolError if out of range."""
	from fastmcp.exceptions import ToolError
	if monitors is None:
		monitors = enumerate_monitors()
	for m in monitors:
		if m.index == index:
			return m
	indices = [str(m.index) for m in monitors]
	raise ToolError(
		f"Monitor {index} not found. Available monitors: {', '.join(indices)}. "
		f"Use snapshot() to see monitor layout in metadata."
	)


def get_monitor_for_window(hwnd: int, monitors: list[MonitorInfo] | None = None) -> MonitorInfo:
	"""Get the monitor that contains the given window handle."""
	if monitors is None:
		monitors = enumerate_monitors()
	try:
		ctypes.windll.user32.MonitorFromWindow.restype = ctypes.c_void_p
		hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
		if hmon:
			for m in monitors:
				if m.handle == hmon:
					return m
	except (AttributeError, OSError):
		pass
	# Fallback: return primary
	return get_primary_monitor()


def get_virtual_screen_rect() -> tuple[int, int, int, int]:
	"""Get virtual screen bounds (all monitors) as (left, top, width, height)."""
	SM_XVIRTUALSCREEN = 76
	SM_YVIRTUALSCREEN = 77
	SM_CXVIRTUALSCREEN = 78
	SM_CYVIRTUALSCREEN = 79
	try:
		left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
		top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
		w = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
		h = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
		if w > 0 and h > 0:
			return (left, top, w, h)
	except (AttributeError, OSError, OverflowError):
		pass
	return (0, 0, 1920, 1080)


# --- DPI and Screen Size ---

def get_dpi_scale(monitor_info: MonitorInfo | None = None) -> float:
	"""Get DPI scale factor for the given monitor (or primary)."""
	if monitor_info:
		return monitor_info.dpi_scale
	return get_primary_monitor().dpi_scale


def get_screen_size(monitor_info: MonitorInfo | None = None) -> tuple[int, int]:
	"""Get screen size in logical pixels for the given monitor (or primary)."""
	if monitor_info:
		return (monitor_info.width, monitor_info.height)
	pm = get_primary_monitor()
	return (pm.width, pm.height)


# --- Cursor Compositing ---

class _POINT(ctypes.Structure):
	_fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
	_fields_ = [
		("cbSize", ctypes.c_uint),
		("flags", ctypes.c_uint),
		("hCursor", ctypes.c_void_p),
		("ptScreenPos", _POINT),
	]


class _ICONINFO(ctypes.Structure):
	_fields_ = [
		("fIcon", ctypes.wintypes.BOOL),
		("xHotspot", ctypes.wintypes.DWORD),
		("yHotspot", ctypes.wintypes.DWORD),
		("hbmMask", ctypes.c_void_p),
		("hbmColor", ctypes.c_void_p),
	]


class _BITMAP(ctypes.Structure):
	_fields_ = [
		("bmType", ctypes.c_long),
		("bmWidth", ctypes.c_long),
		("bmHeight", ctypes.c_long),
		("bmWidthBytes", ctypes.c_long),
		("bmPlanes", ctypes.wintypes.WORD),
		("bmBitsPixel", ctypes.wintypes.WORD),
		("bmBits", ctypes.c_void_p),
	]


class _BITMAPINFOHEADER(ctypes.Structure):
	_fields_ = [
		("biSize", ctypes.wintypes.DWORD),
		("biWidth", ctypes.c_long),
		("biHeight", ctypes.c_long),
		("biPlanes", ctypes.wintypes.WORD),
		("biBitCount", ctypes.wintypes.WORD),
		("biCompression", ctypes.wintypes.DWORD),
		("biSizeImage", ctypes.wintypes.DWORD),
		("biXPelsPerMeter", ctypes.c_long),
		("biYPelsPerMeter", ctypes.c_long),
		("biClrUsed", ctypes.wintypes.DWORD),
		("biClrImportant", ctypes.wintypes.DWORD),
	]


_CURSOR_SHOWING = 0x00000001
_BI_RGB = 0
_DIB_RGB_COLORS = 0


def _draw_synthetic_cursor(image: Image.Image, px: int, py: int) -> Image.Image:
	"""Draw a bright synthetic arrow cursor at the given pixel position."""
	draw = ImageDraw.Draw(image)
	# Standard arrow shape, 20x24 pixels
	arrow = [
		(px, py),
		(px, py + 20),
		(px + 5, py + 16),
		(px + 9, py + 23),
		(px + 12, py + 22),
		(px + 8, py + 15),
		(px + 14, py + 15),
	]
	try:
		draw.polygon(arrow, fill="#00FF00", outline="#000000")
	except (OverflowError, ValueError):
		pass
	return image


def _composite_cursor(
	image: Image.Image,
	monitor_left: int,
	monitor_top: int,
	scale_factor: float,
) -> Image.Image:
	"""Draw the system cursor onto a screenshot image.

	Args:
		image: Screenshot in physical pixels.
		monitor_left: Left edge of captured region in logical pixels.
		monitor_top: Top edge of captured region in logical pixels.
		scale_factor: DPI scale of the captured monitor.

	Returns:
		Image with cursor composited (or unchanged if cursor not available).
	"""
	try:
		ci = _CURSORINFO()
		ci.cbSize = ctypes.sizeof(_CURSORINFO)
		if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)):
			return image
		if not (ci.flags & _CURSOR_SHOWING):
			return image  # Cursor hidden (e.g. during text input)

		# Map cursor screen position to image pixel position
		cursor_x = ci.ptScreenPos.x
		cursor_y = ci.ptScreenPos.y
		px = int((cursor_x - monitor_left) * scale_factor)
		py = int((cursor_y - monitor_top) * scale_factor)

		# Skip if cursor is outside the captured region
		if px < -32 or py < -32 or px > image.width + 32 or py > image.height + 32:
			return image

		# Try to extract real cursor bitmap
		icon_info = _ICONINFO()
		ctypes.windll.user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ICONINFO)]
		if not ctypes.windll.user32.GetIconInfo(ci.hCursor, ctypes.byref(icon_info)):
			return _draw_synthetic_cursor(image, px, py)

		hotspot_x = icon_info.xHotspot
		hotspot_y = icon_info.yHotspot
		hbm_color = icon_info.hbmColor
		hbm_mask = icon_info.hbmMask

		try:
			if not hbm_color:
				# Monochrome cursor — use synthetic fallback
				return _draw_synthetic_cursor(image, px - hotspot_x, py - hotspot_y)

			# Get bitmap dimensions
			bm = _BITMAP()
			ctypes.windll.gdi32.GetObjectW.argtypes = [
				ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
			]
			ctypes.windll.gdi32.GetObjectW(hbm_color, ctypes.sizeof(_BITMAP), ctypes.byref(bm))
			w, h = bm.bmWidth, bm.bmHeight
			if w <= 0 or h <= 0 or w > 256 or h > 256:
				return _draw_synthetic_cursor(image, px - hotspot_x, py - hotspot_y)

			# Extract color bitmap pixels via GetDIBits
			bmi = _BITMAPINFOHEADER()
			bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
			bmi.biWidth = w
			bmi.biHeight = -h  # Top-down
			bmi.biPlanes = 1
			bmi.biBitCount = 32
			bmi.biCompression = _BI_RGB

			buf_size = w * h * 4
			color_buf = ctypes.create_string_buffer(buf_size)

			ctypes.windll.user32.GetDC.argtypes = [ctypes.c_void_p]
			ctypes.windll.user32.GetDC.restype = ctypes.c_void_p
			ctypes.windll.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
			hdc = ctypes.windll.user32.GetDC(0)
			ctypes.windll.gdi32.GetDIBits.argtypes = [
				ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
				ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
			]
			ctypes.windll.gdi32.GetDIBits(
				hdc, hbm_color, 0, h, color_buf, ctypes.byref(bmi), _DIB_RGB_COLORS,
			)

			# Also get mask bitmap for alpha
			mask_buf = ctypes.create_string_buffer(buf_size)
			bmi_mask = _BITMAPINFOHEADER()
			bmi_mask.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
			bmi_mask.biWidth = w
			bmi_mask.biHeight = -h  # Top-down
			bmi_mask.biPlanes = 1
			bmi_mask.biBitCount = 32
			bmi_mask.biCompression = _BI_RGB
			ctypes.windll.gdi32.GetDIBits(
				hdc, hbm_mask, 0, h, mask_buf, ctypes.byref(bmi_mask), _DIB_RGB_COLORS,
			)
			ctypes.windll.user32.ReleaseDC(0, hdc)

			# Build RGBA cursor image from BGRA color + mask data
			color_bytes = bytes(color_buf)
			mask_bytes = bytes(mask_buf)
			rgba = bytearray(w * h * 4)
			has_alpha = False

			for i in range(w * h):
				b = color_bytes[i * 4]
				g = color_bytes[i * 4 + 1]
				r = color_bytes[i * 4 + 2]
				a = color_bytes[i * 4 + 3]
				if a != 0:
					has_alpha = True
				rgba[i * 4] = r
				rgba[i * 4 + 1] = g
				rgba[i * 4 + 2] = b
				rgba[i * 4 + 3] = a

			if not has_alpha:
				# No per-pixel alpha — derive from mask bitmap
				for i in range(w * h):
					mask_val = mask_bytes[i * 4]  # Any channel (mask is grayscale)
					if mask_val == 0:
						rgba[i * 4 + 3] = 255  # Opaque where mask is black
					else:
						rgba[i * 4 + 3] = 0    # Transparent where mask is white

			cursor_img = Image.frombytes("RGBA", (w, h), bytes(rgba))

			# Composite cursor onto screenshot
			dest_x = px - hotspot_x
			dest_y = py - hotspot_y
			image = image.convert("RGBA")
			image.paste(cursor_img, (dest_x, dest_y), cursor_img)
			image = image.convert("RGB")
			return image

		finally:
			# Clean up GDI objects
			if hbm_color:
				ctypes.windll.gdi32.DeleteObject(hbm_color)
			if hbm_mask:
				ctypes.windll.gdi32.DeleteObject(hbm_mask)

	except Exception as e:
		logging.info(f"Cursor composite failed ({e}), using synthetic fallback")
		try:
			ci = _CURSORINFO()
			ci.cbSize = ctypes.sizeof(_CURSORINFO)
			if ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)):
				fpx = int((ci.ptScreenPos.x - monitor_left) * scale_factor)
				fpy = int((ci.ptScreenPos.y - monitor_top) * scale_factor)
				return _draw_synthetic_cursor(image, fpx, fpy)
		except Exception:
			pass
		return image


# --- Screenshot Capture ---

def capture_screenshot(monitor_info: MonitorInfo | None = None) -> Image.Image:
	"""Capture a monitor as a PIL Image (physical pixels) with cursor composited.

	Args:
		monitor_info: Monitor to capture. None = full virtual desktop ("all").
	"""
	with mss.mss() as sct:
		if monitor_info is None:
			# Full virtual desktop
			grab_rect = sct.monitors[0]
			virt = get_virtual_screen_rect()
			mon_left, mon_top = virt[0], virt[1]
			scale = get_primary_monitor().dpi_scale
		else:
			# Specific monitor — use its rect in physical pixels for mss
			left, top, right, bottom = monitor_info.rect
			s = monitor_info.dpi_scale
			grab_rect = {
				"left": int(left * s),
				"top": int(top * s),
				"width": int((right - left) * s),
				"height": int((bottom - top) * s),
			}
			mon_left, mon_top = left, top
			scale = s

		shot = sct.grab(grab_rect)
		img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)

	# Always composite cursor
	img = _composite_cursor(img, mon_left, mon_top, scale)
	return img


# --- Window Helpers ---

def get_window_rect(handle: int) -> tuple[int, int, int, int] | None:
	"""Get window bounding rect in physical pixels via Win32 GetWindowRect.

	Returns (left, top, right, bottom) or None if the call fails.
	"""
	try:
		rect = ctypes.wintypes.RECT()
		if ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
			return (rect.left, rect.top, rect.right, rect.bottom)
		return None
	except (AttributeError, OSError, OverflowError):
		return None


# --- Screenshot Annotation ---

def annotate_screenshot(
	image: Image.Image,
	elements: dict[str, ElementInfo],
	scale_factor: float,
	monitor_origin: tuple[int, int] = (0, 0),
) -> Image.Image:
	"""Draw numbered labels on interactive elements in the screenshot.

	Labels are drawn at element centers. Elements with unavailable coordinates
	or bounding rects smaller than 8x8px are skipped.

	Args:
		image: Screenshot in physical pixels.
		elements: Element registry from desktop_state.
		scale_factor: DPI scale factor.
		monitor_origin: (left, top) of the captured monitor in logical pixels.
			Used to offset element coordinates for non-primary monitors.
	"""
	img = image.copy()
	draw = ImageDraw.Draw(img)
	img_w, img_h = img.size
	origin_x, origin_y = monitor_origin

	# Try to get a small font; fall back to default
	try:
		font = ImageFont.truetype("arial.ttf", 12)
	except (OSError, IOError):
		font = ImageFont.load_default()

	for label, elem in elements.items():
		if elem.coords_unavailable:
			continue

		left, top, right, bottom = elem.bounding_rect
		# Convert logical to physical pixels, offset by monitor origin
		px_left = int((left - origin_x) * scale_factor)
		px_top = int((top - origin_y) * scale_factor)
		px_right = int((right - origin_x) * scale_factor)
		px_bottom = int((bottom - origin_y) * scale_factor)

		# Clamp to image bounds (elements can have negative or offscreen coords
		# from multi-monitor setups or Chromium's unclipped accessibility tree)
		px_left = max(0, min(px_left, img_w))
		px_top = max(0, min(px_top, img_h))
		px_right = max(0, min(px_right, img_w))
		px_bottom = max(0, min(px_bottom, img_h))

		# Skip tiny elements (after clamping)
		if (px_right - px_left) < 8 or (px_bottom - px_top) < 8:
			continue

		try:
			# Draw bounding box
			draw.rectangle(
				[px_left, px_top, px_right, px_bottom],
				outline="#FF4444",
				width=1,
			)

			# Draw label badge at top-left corner
			text = str(label)
			bbox = font.getbbox(text)
			text_w = bbox[2] - bbox[0]
			text_h = bbox[3] - bbox[1]
			badge_x = px_left
			badge_y = max(px_top - text_h - 4, 0)

			# Background for readability
			draw.rectangle(
				[badge_x, badge_y, badge_x + text_w + 4, badge_y + text_h + 4],
				fill="#FF4444",
			)
			draw.text((badge_x + 2, badge_y + 2), text, fill="white", font=font)
		except (OverflowError, ValueError):
			continue  # Skip this label if drawing still fails

	return img


# --- Crop Helpers ---

def crop_to_rect(
	image: Image.Image,
	rect: tuple[int, int, int, int],
) -> Image.Image:
	"""Crop image to the given (left, top, right, bottom) rect in physical pixels.

	Clamps to image bounds to handle windows partially off-screen.
	"""
	img_w, img_h = image.size
	left = max(0, min(rect[0], img_w))
	top = max(0, min(rect[1], img_h))
	right = max(0, min(rect[2], img_w))
	bottom = max(0, min(rect[3], img_h))

	# Skip crop if rect is invalid or zero-sized
	if right <= left or bottom <= top:
		return image

	return image.crop((left, top, right, bottom))


def crop_region(
	image: Image.Image,
	crop: tuple[int, int, int, int],
	scale_factor: float,
	monitor_origin: tuple[int, int] = (0, 0),
) -> Image.Image:
	"""Crop image to a region specified in absolute logical pixel coordinates.

	Converts logical pixel coordinates to physical pixels relative to the
	captured monitor's origin, then delegates to crop_to_rect().
	"""
	origin_x, origin_y = monitor_origin
	physical = (
		int((crop[0] - origin_x) * scale_factor),
		int((crop[1] - origin_y) * scale_factor),
		int((crop[2] - origin_x) * scale_factor),
		int((crop[3] - origin_y) * scale_factor),
	)
	return crop_to_rect(image, physical)


# --- Grid Overlay ---

def _pick_nice_interval(max_logical_dim: float) -> int:
	"""Pick a visually clean grid interval targeting ~12 lines per axis."""
	raw = max_logical_dim / 12
	nice_values = [10, 20, 25, 50, 100, 200, 250, 500, 1000]
	for val in nice_values:
		if raw <= val:
			return val
	return int(round(raw / 100) * 100) or 100


def draw_grid_overlay(
	image: Image.Image,
	grid: str,
	grid_interval: int | str,
	scale_factor: float,
	origin: tuple[int, int] = (0, 0),
) -> Image.Image:
	"""Draw coordinate grid overlay on a screenshot.

	Args:
		image: PIL Image in physical pixels.
		grid: "rulers" (edge rulers only) or "full" (rulers + interior lines).
		grid_interval: "auto" or explicit int (logical pixels between lines).
		scale_factor: Logical-to-physical pixel ratio.
		origin: Logical pixel coordinate of the image's top-left corner,
			so grid labels show absolute screen coordinates after cropping.

	Returns:
		New image with grid overlay composited on top.
	"""
	RULER_SIZE = 28  # px width of ruler strips

	# Compute interval
	logical_w = image.width / scale_factor
	logical_h = image.height / scale_factor
	if grid_interval == "auto":
		interval = _pick_nice_interval(max(logical_w, logical_h))
	else:
		interval = max(10, int(grid_interval))

	# Convert to RGBA for transparency compositing
	base = image.convert("RGBA")
	overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
	draw = ImageDraw.Draw(overlay)

	# Font for grid labels
	try:
		font = ImageFont.truetype("arial.ttf", 11)
	except (OSError, IOError):
		font = ImageFont.load_default()

	# Draw ruler background strips
	# Top ruler
	draw.rectangle([0, 0, base.width, RULER_SIZE], fill=(0, 0, 0, 180))
	# Left ruler
	draw.rectangle([0, RULER_SIZE, RULER_SIZE, base.height], fill=(0, 0, 0, 180))

	# Compute grid lines — vertical (X axis)
	first_logical_x = math.ceil(origin[0] / interval) * interval
	x = first_logical_x
	while True:
		px = int((x - origin[0]) * scale_factor)
		if px >= base.width:
			break
		if px >= 0:
			# Interior grid line
			if grid == "full" and px > RULER_SIZE:
				draw.line([(px, RULER_SIZE), (px, base.height)], fill=(0, 200, 255, 50), width=1)
			# Tick on top ruler
			draw.line([(px, RULER_SIZE - 6), (px, RULER_SIZE)], fill=(255, 255, 255, 200), width=1)
			# Label on top ruler
			label = str(x)
			bbox = font.getbbox(label)
			tw = bbox[2] - bbox[0]
			lx = max(RULER_SIZE + 2, px - tw // 2)
			draw.text((lx, 3), label, fill=(255, 255, 255, 220), font=font)
		x += interval

	# Compute grid lines — horizontal (Y axis)
	first_logical_y = math.ceil(origin[1] / interval) * interval
	y = first_logical_y
	while True:
		py = int((y - origin[1]) * scale_factor)
		if py >= base.height:
			break
		if py >= 0:
			# Interior grid line
			if grid == "full" and py > RULER_SIZE:
				draw.line([(RULER_SIZE, py), (base.width, py)], fill=(0, 200, 255, 50), width=1)
			# Tick on left ruler
			draw.line([(RULER_SIZE - 6, py), (RULER_SIZE, py)], fill=(255, 255, 255, 200), width=1)
			# Label on left ruler
			label = str(y)
			bbox = font.getbbox(label)
			th = bbox[3] - bbox[1]
			ly = max(RULER_SIZE + 2, py - th // 2)
			draw.text((2, ly), label, fill=(255, 255, 255, 220), font=font)
		y += interval

	# Composite overlay onto base
	result = Image.alpha_composite(base, overlay)
	return result.convert("RGB")


# --- Screenshot Encoding ---

def screenshot_to_bytes(image: Image.Image, max_width: int = 1920) -> bytes:
	"""Resize if needed and encode screenshot as PNG bytes."""
	if image.width > max_width:
		ratio = max_width / image.width
		new_height = int(image.height * ratio)
		image = image.resize((max_width, new_height), Image.LANCZOS)

	buffer = io.BytesIO()
	image.save(buffer, format="PNG", optimize=True)
	return buffer.getvalue()
