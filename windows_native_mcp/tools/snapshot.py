"""Snapshot tool — capture desktop state (screenshot + UI tree + element labels)."""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state, ElementInfo
from fastmcp.exceptions import ToolError
from windows_native_mcp.core.screen import (
	capture_screenshot,
	annotate_screenshot,
	screenshot_to_bytes,
	get_dpi_scale,
	get_screen_size,
	get_window_rect,
	crop_to_rect,
	crop_region,
	draw_grid_overlay,
	enumerate_monitors,
	get_primary_monitor,
	get_monitor_by_index,
	get_monitor_for_window,
	MonitorInfo,
)
from windows_native_mcp.core.uia import get_desktop_elements


def _build_tree_output(
	elements: dict[str, ElementInfo],
	include_rects: bool,
) -> list[dict]:
	"""Build nested tree output from flat elements with parent_label references."""
	# Build node for each element
	nodes: dict[str, dict] = {}
	for label, elem in elements.items():
		node: dict = {
			"label": label,
			"type": elem.control_type,
		}
		if elem.name:
			node["name"] = elem.name
		if not elem.coords_unavailable:
			node["center"] = list(elem.center)
		if not elem.is_enabled:
			node["enabled"] = False
		if elem.coords_unavailable:
			node["coords_unavailable"] = True
		if elem.automation_id:
			node["automation_id"] = elem.automation_id
		if elem.checked is not None:
			node["checked"] = elem.checked
		if elem.selected is not None:
			node["selected"] = elem.selected
		if include_rects and not elem.coords_unavailable:
			node["rect"] = list(elem.bounding_rect)
		nodes[label] = node

	# Build adjacency: parent_label → [child_labels]
	# Orphans (parent pruned by cap) become root-level nodes
	children_map: dict[str | None, list[str]] = {}
	for label, elem in elements.items():
		parent = elem.parent_label
		# Treat orphans (parent not in elements) as roots
		if parent is not None and parent not in elements:
			parent = None
		if parent not in children_map:
			children_map[parent] = []
		children_map[parent].append(label)

	# Data types whose Text children can be collapsed into a values array
	_DATA_TYPES = {"TreeItem", "ListItem", "DataItem"}

	# Recursive nesting
	def _nest(label: str) -> dict:
		node = nodes[label]
		child_labels = children_map.get(label, [])
		if child_labels:
			parent_type = elements[label].control_type
			if parent_type in _DATA_TYPES:
				text_children = [cl for cl in child_labels if elements[cl].control_type == "Text"]
				non_text_children = [cl for cl in child_labels if elements[cl].control_type != "Text"]
				if len(text_children) >= 2 and not non_text_children:
					values = [elements[cl].name for cl in text_children if elements[cl].name]
					if values:
						node["values"] = values
					return node
			node["children"] = [_nest(cl) for cl in child_labels]
		return node

	# Root nodes have parent_label=None (or orphaned parent)
	root_labels = children_map.get(None, [])
	return [_nest(rl) for rl in root_labels]


def _execute_snapshot(
	detail: str = "standard",
	window: str | None = None,
	limit: int = 500,
	types: list[str] | None = None,
	viewport_only: bool = True,
	include_rects: bool = False,
	scale_factor: float | None = None,
	screen_size: tuple[int, int] | None = None,
	screen_origin: tuple[int, int] = (0, 0),
) -> dict:
	"""Core snapshot logic (no screenshot). Used by snapshot tool and post-action auto-snapshot.

	Args:
		scale_factor: DPI scale override. None = use primary monitor.
		screen_size: Screen dimensions override. None = use primary monitor.
		screen_origin: Top-left of active monitor in logical pixels (for scoring).
	"""
	if scale_factor is None:
		scale_factor = get_dpi_scale()
	if screen_size is None:
		screen_size = get_screen_size()
	type_filter = set(t + "Control" for t in types) if types else None

	elements, metadata = get_desktop_elements(
		detail=detail,
		window_name=window,
		scale_factor=scale_factor,
		limit=limit,
		type_filter=type_filter,
		screen_size=screen_size,
		viewport_only=viewport_only,
		screen_origin=screen_origin,
	)

	desktop_state.elements = elements
	desktop_state.scale_factor = scale_factor
	desktop_state.screen_size = screen_size
	desktop_state.is_stale = False
	desktop_state.window_name = window
	desktop_state.window_handle = metadata.get("window_handle")
	desktop_state.last_element_count = len(elements)

	metadata["scale_factor"] = scale_factor
	metadata["screen_size"] = list(screen_size)
	elements_tree = _build_tree_output(elements, include_rects)
	return {"metadata": metadata, "elements": elements_tree}


def _capture_annotated_screenshot(
	metadata: dict,
	grid: str = "rulers",
	grid_interval: int | str = "auto",
	crop: list[int] | None = None,
	monitor_info: MonitorInfo | None = None,
) -> tuple[bytes, str]:
	"""Capture, annotate, crop, and grid-overlay a screenshot.

	Returns (png_bytes, screenshot_path_str).
	"""
	scale_factor = desktop_state.scale_factor

	# Determine monitor origin for coordinate mapping
	if monitor_info is not None:
		monitor_origin = (monitor_info.rect[0], monitor_info.rect[1])
	else:
		monitor_origin = (0, 0)

	# Capture screenshot (includes cursor compositing)
	img = capture_screenshot(monitor_info)

	# Annotate with element labels
	annotated = annotate_screenshot(img, desktop_state.elements, scale_factor, monitor_origin)

	# Determine crop and track origin for grid coordinate labels
	window_handle = metadata.get("window_handle")
	crop_origin = monitor_origin

	if crop is not None:
		# User crop takes precedence over window auto-crop
		if len(crop) != 4:
			raise ToolError("crop must be [left, top, right, bottom] with 4 values")
		annotated = crop_region(annotated, tuple(crop), scale_factor, monitor_origin)
		crop_origin = (crop[0], crop[1])
	elif window_handle and not metadata.get("window_minimized"):
		win_rect = get_window_rect(window_handle)
		if win_rect:
			# win_rect is in absolute physical coords on the virtual desktop.
			# Offset by monitor's physical origin so it's relative to the captured image.
			if monitor_info:
				phys_origin_x = int(monitor_info.rect[0] * scale_factor)
				phys_origin_y = int(monitor_info.rect[1] * scale_factor)
				win_rect = (
					win_rect[0] - phys_origin_x, win_rect[1] - phys_origin_y,
					win_rect[2] - phys_origin_x, win_rect[3] - phys_origin_y,
				)
			annotated = crop_to_rect(annotated, win_rect)
			# crop_origin in logical coords for grid labels (absolute screen coords)
			crop_origin = (
				int(win_rect[0] / scale_factor) + monitor_origin[0],
				int(win_rect[1] / scale_factor) + monitor_origin[1],
			)

	# Draw grid overlay (no-op if grid="off")
	if grid != "off":
		annotated = draw_grid_overlay(annotated, grid, grid_interval, scale_factor, crop_origin)

	png_bytes = screenshot_to_bytes(annotated)

	# Save annotated screenshot to outputs/ for external viewing
	outputs_dir = Path(__file__).resolve().parent.parent.parent / "outputs"
	outputs_dir.mkdir(exist_ok=True)
	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	screenshot_path = outputs_dir / f"snapshot_{timestamp}.png"
	screenshot_path.write_bytes(png_bytes)
	logging.info(f"Screenshot saved to {screenshot_path}")

	return (png_bytes, str(screenshot_path))


def run_post_action_snapshot(delay: float = 0.15) -> dict:
	"""Run snapshot using last snapshot params. For post-action auto-refresh.

	Replays ALL params from the last explicit snapshot() call, including
	screenshot, grid, crop, and monitor settings. Always returns a dict
	(action tools need consistent return types). Screenshots are saved to
	disk and the path is included in metadata.
	"""
	import time
	time.sleep(delay)

	params = desktop_state.last_snapshot_params
	if params is None:
		params = {
			"detail": "standard", "window": None, "limit": 500,
			"types": None, "viewport_only": True, "include_rects": False,
			"screenshot": False, "grid": "rulers", "grid_interval": "auto",
			"crop": None, "monitor": None,
		}

	# Split into tree params and screenshot params
	tree_params = {
		"detail": params.get("detail", "standard"),
		"window": params.get("window"),
		"limit": params.get("limit", 500),
		"types": params.get("types"),
		"viewport_only": params.get("viewport_only", True),
		"include_rects": params.get("include_rects", False),
	}

	# Resolve monitor for tree collection
	monitor_param = params.get("monitor")
	active_monitor = _resolve_monitor(monitor_param, tree_params["window"])

	if active_monitor:
		tree_params["scale_factor"] = active_monitor.dpi_scale
		tree_params["screen_size"] = (active_monitor.width, active_monitor.height)
		tree_params["screen_origin"] = (active_monitor.rect[0], active_monitor.rect[1])

	desktop_state.active_monitor = active_monitor
	result = _execute_snapshot(**tree_params)

	screenshot = params.get("screenshot", False)
	if not screenshot:
		return result

	# Replay screenshot pipeline — save to disk, include path in metadata
	_png_bytes, screenshot_path = _capture_annotated_screenshot(
		metadata=result["metadata"],
		grid=params.get("grid", "rulers"),
		grid_interval=params.get("grid_interval", "auto"),
		crop=params.get("crop"),
		monitor_info=active_monitor,
	)

	result["metadata"]["screenshot_path"] = screenshot_path
	return result


def run_post_action_snapshot_unscoped(delay: float = 0.15) -> dict:
	"""Run snapshot ignoring previous window scope. For shortcuts that may change focus."""
	import time
	time.sleep(delay)

	params = desktop_state.last_snapshot_params
	if params is None:
		params = {
			"detail": "standard", "window": None, "limit": 500,
			"types": None, "viewport_only": True, "include_rects": False,
			"screenshot": False, "grid": "rulers", "grid_interval": "auto",
			"crop": None, "monitor": None,
		}
	else:
		params = {**params, "window": None}

	# Split into tree params and screenshot params
	tree_params = {
		"detail": params.get("detail", "standard"),
		"window": None,
		"limit": params.get("limit", 500),
		"types": params.get("types"),
		"viewport_only": params.get("viewport_only", True),
		"include_rects": params.get("include_rects", False),
	}

	# Resolve monitor (no window to auto-detect from)
	monitor_param = params.get("monitor")
	active_monitor = _resolve_monitor(monitor_param, None)

	if active_monitor:
		tree_params["scale_factor"] = active_monitor.dpi_scale
		tree_params["screen_size"] = (active_monitor.width, active_monitor.height)
		tree_params["screen_origin"] = (active_monitor.rect[0], active_monitor.rect[1])

	desktop_state.active_monitor = active_monitor
	result = _execute_snapshot(**tree_params)

	screenshot = params.get("screenshot", False)
	if not screenshot:
		return result

	# Replay screenshot pipeline — save to disk, include path in metadata
	_png_bytes, screenshot_path = _capture_annotated_screenshot(
		metadata=result["metadata"],
		grid=params.get("grid", "rulers"),
		grid_interval=params.get("grid_interval", "auto"),
		crop=params.get("crop"),
		monitor_info=active_monitor,
	)

	result["metadata"]["screenshot_path"] = screenshot_path
	return result


def _resolve_monitor(
	monitor_param: int | str | None,
	window: str | None,
) -> MonitorInfo | None:
	"""Resolve a monitor parameter to a MonitorInfo.

	Returns None for "all" (full virtual desktop) or when defaulting to primary.
	"""
	if monitor_param == "all":
		return None  # Full virtual desktop

	monitors = enumerate_monitors()
	desktop_state.monitors = monitors

	if isinstance(monitor_param, int):
		return get_monitor_by_index(monitor_param, monitors)

	# Auto-detect from window if available
	if window and desktop_state.window_handle:
		return get_monitor_for_window(desktop_state.window_handle, monitors)

	# Default: primary monitor
	return get_primary_monitor()


def register(mcp: FastMCP):
	"""Register the snapshot tool."""

	@mcp.tool(
		name="snapshot",
		output_schema=None,
		annotations=ToolAnnotations(
			title="Desktop Snapshot",
			readOnlyHint=True,
			destructiveHint=False,
			idempotentHint=True,
			openWorldHint=False,
		),
	)
	def snapshot(
		detail: Annotated[
			Literal["minimal", "standard", "full"],
			Field(description="Level of detail: minimal (windows only), standard (interactive elements), full (entire UI tree)"),
		] = "standard",
		window: Annotated[
			str | None,
			Field(description="Window name to scope snapshot to (exact match, then substring)"),
		] = None,
		screenshot: Annotated[
			bool,
			Field(description="Include annotated screenshot image. Off by default — enable when UI tree labels aren't sufficient, elements show coords_unavailable, or you need visual verification"),
		] = False,
		include_rects: Annotated[
			bool,
			Field(description="Include bounding rectangles in output"),
		] = False,
		types: Annotated[
			list[str] | None,
			Field(description='Filter element types (e.g. ["Button", "Edit"])'),
		] = None,
		limit: Annotated[
			int,
			Field(ge=1, le=5000, description="Max elements to return (ranked by visibility and relevance). Increase if important elements are missing"),
		] = 500,
		viewport_only: Annotated[
			bool,
			Field(description="Exclude elements outside the visible viewport"),
		] = True,
		grid: Annotated[
			Literal["off", "rulers", "full"],
			Field(description="Coordinate grid overlay on screenshot: rulers (default, axis labels on edges), full (rulers + interior grid lines), off (clean image). Only applies when screenshot is enabled"),
		] = "rulers",
		grid_interval: Annotated[
			int | str,
			Field(description="Grid spacing in logical pixels. 'auto' (default) picks a clean interval for ~12 lines per axis. Pass an int for explicit spacing (e.g. 50)"),
		] = "auto",
		crop: Annotated[
			list[int] | None,
			Field(description="Crop to region [left, top, right, bottom] in absolute screen coordinates (logical pixels). Coordinates must be within the target window's screen position. Overrides window auto-crop"),
		] = None,
		monitor: Annotated[
			int | str | None,
			Field(
				description="Monitor to capture: 1 for primary, 2+ for others, "
				"'all' for full virtual desktop. Auto-detected from window when scoped. "
				"Omit for primary monitor."
			),
		] = None,
	) -> list | dict:
		"""Capture current desktop state as a UI element tree with numbered labels.

		Returns numbered element labels for use as targets in click, type_text,
		scroll, and other action tools. Labels are invalidated after any action.

		Screenshot is off by default — the UI tree alone is sufficient for most
		interactions. Enable screenshot=True when labels aren't giving enough
		context, elements show coords_unavailable, or you need to verify visual
		layout. Screenshots include a coordinate ruler overlay by default.

		Elements marked coords_unavailable (common in UWP apps) cannot use label
		targeting — use [x, y] coordinates from the screenshot instead.
		When window-scoped, screenshot is auto-cropped to the window bounds.

		Use crop to zoom into a specific region (auto-enables screenshot).
		Use monitor to work on different displays.
		"""
		# Auto-enable screenshot when crop is requested (crop is meaningless without screenshot)
		if crop is not None:
			screenshot = True

		logging.info(f"Snapshot: detail={detail}, window={window}, screenshot={screenshot}, limit={limit}, types={types}, monitor={monitor}")

		# Enumerate monitors and resolve active monitor
		monitors = enumerate_monitors()
		desktop_state.monitors = monitors

		# Core element collection + state update (needs window resolved first for handle)
		tree_params = {
			"detail": detail, "window": window, "limit": limit,
			"types": types, "viewport_only": viewport_only,
			"include_rects": include_rects,
		}

		# First pass: collect elements to get window_handle for monitor auto-detection
		result = _execute_snapshot(**tree_params)

		# Now resolve monitor (may need window_handle from _execute_snapshot)
		if monitor == "all":
			active_monitor = None
		elif isinstance(monitor, int):
			active_monitor = get_monitor_by_index(monitor, monitors)
		elif window and desktop_state.window_handle:
			active_monitor = get_monitor_for_window(desktop_state.window_handle, monitors)
		else:
			active_monitor = get_primary_monitor()

		desktop_state.active_monitor = active_monitor

		# Re-run element collection with correct monitor DPI/size/origin if non-primary
		if active_monitor:
			mon_origin = (active_monitor.rect[0], active_monitor.rect[1])
			needs_rerun = (
				active_monitor.dpi_scale != desktop_state.scale_factor
				or (active_monitor.width, active_monitor.height) != desktop_state.screen_size
				or mon_origin != (0, 0)
			)
			if needs_rerun:
				tree_params["scale_factor"] = active_monitor.dpi_scale
				tree_params["screen_size"] = (active_monitor.width, active_monitor.height)
				tree_params["screen_origin"] = mon_origin
				result = _execute_snapshot(**tree_params)

		# Store ALL params for auto-snapshot replay (after _execute_snapshot so window_handle is set)
		desktop_state.last_snapshot_params = {
			"detail": detail, "window": window, "limit": limit,
			"types": types, "viewport_only": viewport_only,
			"include_rects": include_rects,
			"screenshot": screenshot, "grid": grid,
			"grid_interval": grid_interval, "crop": crop,
			"monitor": monitor,
		}

		# Add monitor metadata
		metadata = result["metadata"]
		metadata["monitors"] = [
			{"index": m.index, "rect": list(m.rect), "primary": m.primary}
			for m in monitors
		]
		metadata["active_monitor"] = active_monitor.index if active_monitor else "all"

		if not screenshot:
			return result

		elements_tree = result["elements"]

		# Capture annotated screenshot via shared pipeline
		png_bytes, screenshot_path = _capture_annotated_screenshot(
			metadata=metadata,
			grid=grid,
			grid_interval=grid_interval,
			crop=crop,
			monitor_info=active_monitor,
		)

		text_content = json.dumps({
			"metadata": {**metadata, "screenshot_path": screenshot_path},
			"elements": elements_tree,
		}, indent=None, separators=(",", ":"))

		return [
			MCPImage(data=png_bytes, format="png"),
			text_content,
		]
