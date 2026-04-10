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
) -> dict:
	"""Core snapshot logic (no screenshot). Used by snapshot tool and post-action auto-snapshot."""
	scale_factor = get_dpi_scale()
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
	)

	desktop_state.elements = elements
	desktop_state.scale_factor = scale_factor
	desktop_state.screen_size = screen_size
	desktop_state.is_stale = False
	desktop_state.window_name = window
	desktop_state.window_handle = metadata.get("window_handle")
	desktop_state.last_element_count = len(elements)
	desktop_state.last_snapshot_params = {
		"detail": detail, "window": window, "limit": limit,
		"types": types, "viewport_only": viewport_only,
	}

	metadata["scale_factor"] = scale_factor
	metadata["screen_size"] = list(screen_size)
	elements_tree = _build_tree_output(elements, include_rects)
	return {"metadata": metadata, "elements": elements_tree}


def run_post_action_snapshot() -> dict:
	"""Run snapshot using last snapshot params. For post-action auto-refresh."""
	import time
	params = desktop_state.last_snapshot_params
	if params is None:
		params = {"detail": "standard", "window": None, "limit": 500, "types": None, "viewport_only": True}
	time.sleep(0.15)  # Brief settling delay for UI transitions
	return _execute_snapshot(**params)


def run_post_action_snapshot_unscoped() -> dict:
	"""Run snapshot ignoring previous window scope. For shortcuts that may change focus."""
	import time
	params = desktop_state.last_snapshot_params
	if params is None:
		params = {"detail": "standard", "window": None, "limit": 500, "types": None, "viewport_only": True}
	else:
		params = {**params, "window": None}
	time.sleep(0.15)  # Brief settling delay for UI transitions
	return _execute_snapshot(**params)


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
			Field(description="Coordinate grid overlay on screenshot: off (default), rulers (axis labels on edges), full (rulers + interior grid lines). Useful for precise coordinate targeting"),
		] = "off",
		grid_interval: Annotated[
			int | str,
			Field(description="Grid spacing in logical pixels. 'auto' (default) picks a clean interval for ~12 lines per axis. Pass an int for explicit spacing (e.g. 50)"),
		] = "auto",
		crop: Annotated[
			list[int] | None,
			Field(description="Crop to region [left, top, right, bottom] in absolute screen coordinates (logical pixels). Coordinates must be within the target window's screen position. Overrides window auto-crop"),
		] = None,
	) -> list | dict:
		"""Capture current desktop state as a UI element tree with numbered labels.

		Returns numbered element labels for use as targets in click, type_text,
		scroll, and other action tools. Labels are invalidated after any action —
		always re-snapshot before the next interaction.

		Screenshot is off by default — the UI tree alone is sufficient for most
		interactions. Enable screenshot=True when labels aren't giving enough
		context, elements show coords_unavailable, or you need to verify visual
		layout.

		Elements marked coords_unavailable (common in UWP apps) cannot use label
		targeting — use [x, y] coordinates from the screenshot instead.
		When window-scoped, screenshot is auto-cropped to the window bounds.
		Otherwise, screenshot captures the primary monitor.

		Use grid="rulers" or grid="full" to overlay a coordinate grid on the
		screenshot for precise coordinate-based targeting. Use crop to zoom into
		a specific region. Both auto-enable screenshot.
		"""
		# Auto-enable screenshot when grid or crop is requested
		if grid != "off" or crop is not None:
			screenshot = True

		logging.info(f"Snapshot: detail={detail}, window={window}, screenshot={screenshot}, limit={limit}, types={types}")

		# Core element collection + state update
		result = _execute_snapshot(
			detail=detail, window=window, limit=limit,
			types=types, viewport_only=viewport_only,
			include_rects=include_rects,
		)

		if not screenshot:
			return result

		metadata = result["metadata"]
		elements_tree = result["elements"]

		# Capture and annotate screenshot
		img = capture_screenshot()
		scale_factor = desktop_state.scale_factor
		annotated = annotate_screenshot(img, desktop_state.elements, scale_factor)

		# Determine crop and track origin for grid coordinate labels
		window_handle = metadata.get("window_handle")
		crop_origin = (0, 0)

		if crop is not None:
			# User crop takes precedence over window auto-crop
			if len(crop) != 4:
				raise ToolError("crop must be [left, top, right, bottom] with 4 values")
			annotated = crop_region(annotated, tuple(crop), scale_factor)
			crop_origin = (crop[0], crop[1])
		elif window_handle and not metadata.get("window_minimized"):
			win_rect = get_window_rect(window_handle)
			if win_rect:
				annotated = crop_to_rect(annotated, win_rect)
				crop_origin = (int(win_rect[0] / scale_factor), int(win_rect[1] / scale_factor))

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

		text_content = json.dumps({
			"metadata": {**metadata, "screenshot_path": str(screenshot_path)},
			"elements": elements_tree,
		}, indent=None, separators=(",", ":"))

		return [
			MCPImage(data=png_bytes, format="png"),
			text_content,
		]
