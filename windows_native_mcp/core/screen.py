"""Screenshot capture, DPI handling, and annotation drawing.

Uses MSS for capture (DPI-aware, no Pillow DPI bugs on Win11).
Uses Pillow for annotation drawing only.
"""
import ctypes
import ctypes.wintypes
import io
import logging
import math

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


def get_dpi_scale() -> float:
	"""Get the DPI scale factor for the primary monitor."""
	try:
		# 64-bit pointer safety for DC handles
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


def get_screen_size() -> tuple[int, int]:
	"""Get primary screen size in logical pixels."""
	try:
		width = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
		height = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
		return (width, height)
	except (AttributeError, OSError, OverflowError):
		return (1920, 1080)


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


def capture_screenshot() -> Image.Image:
	"""Capture the primary monitor as a PIL Image (physical pixels)."""
	with mss.mss() as sct:
		monitor = sct.monitors[1]  # Primary monitor
		shot = sct.grab(monitor)
		return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


def annotate_screenshot(
	image: Image.Image,
	elements: dict[str, ElementInfo],
	scale_factor: float,
) -> Image.Image:
	"""Draw numbered labels on interactive elements in the screenshot.

	Labels are drawn at element centers. Elements with unavailable coordinates
	or bounding rects smaller than 8x8px are skipped.
	"""
	img = image.copy()
	draw = ImageDraw.Draw(img)
	img_w, img_h = img.size

	# Try to get a small font; fall back to default
	try:
		font = ImageFont.truetype("arial.ttf", 12)
	except (OSError, IOError):
		font = ImageFont.load_default()

	for label, elem in elements.items():
		if elem.coords_unavailable:
			continue

		left, top, right, bottom = elem.bounding_rect
		# Convert logical to physical pixels for drawing on the screenshot
		px_left = int(left * scale_factor)
		px_top = int(top * scale_factor)
		px_right = int(right * scale_factor)
		px_bottom = int(bottom * scale_factor)

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


def crop_region(
	image: Image.Image,
	crop: tuple[int, int, int, int],
	scale_factor: float,
) -> Image.Image:
	"""Crop image to a region specified in logical pixels.

	Converts logical pixel coordinates to physical pixels and delegates
	to crop_to_rect() which handles clamping to image bounds.
	"""
	physical = (
		int(crop[0] * scale_factor),
		int(crop[1] * scale_factor),
		int(crop[2] * scale_factor),
		int(crop[3] * scale_factor),
	)
	return crop_to_rect(image, physical)


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


def screenshot_to_bytes(image: Image.Image, max_width: int = 1920) -> bytes:
	"""Resize if needed and encode screenshot as PNG bytes."""
	if image.width > max_width:
		ratio = max_width / image.width
		new_height = int(image.height * ratio)
		image = image.resize((max_width, new_height), Image.LANCZOS)

	buffer = io.BytesIO()
	image.save(buffer, format="PNG", optimize=True)
	return buffer.getvalue()
