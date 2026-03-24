"""Screenshot capture, DPI handling, and annotation drawing.

Uses MSS for capture (DPI-aware, no Pillow DPI bugs on Win11).
Uses Pillow for annotation drawing only.
"""
import ctypes
import ctypes.wintypes
import io
import logging

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
		# Set proper return type for 64-bit pointer safety
		ctypes.windll.user32.GetDC.restype = ctypes.c_void_p
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
	except (AttributeError, OSError):
		return (1920, 1080)


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


def screenshot_to_bytes(image: Image.Image, max_width: int = 1920) -> bytes:
	"""Resize if needed and encode screenshot as PNG bytes."""
	if image.width > max_width:
		ratio = max_width / image.width
		new_height = int(image.height * ratio)
		image = image.resize((max_width, new_height), Image.LANCZOS)

	buffer = io.BytesIO()
	image.save(buffer, format="PNG", optimize=True)
	return buffer.getvalue()
