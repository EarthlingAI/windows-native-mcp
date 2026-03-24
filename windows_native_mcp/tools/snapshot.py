"""Snapshot tool — capture desktop state (screenshot + UI tree + element labels)."""
import json
import logging
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state, ElementInfo
from windows_native_mcp.core.screen import (
	capture_screenshot,
	annotate_screenshot,
	screenshot_to_bytes,
	get_dpi_scale,
	get_screen_size,
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

	# Recursive nesting
	def _nest(label: str) -> dict:
		node = nodes[label]
		child_labels = children_map.get(label, [])
		if child_labels:
			node["children"] = [_nest(cl) for cl in child_labels]
		return node

	# Root nodes have parent_label=None (or orphaned parent)
	root_labels = children_map.get(None, [])
	return [_nest(rl) for rl in root_labels]


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
			Field(description="Include annotated screenshot image"),
		] = True,
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
			Field(ge=1, le=5000, description="Max elements to return"),
		] = 500,
	) -> list | dict:
		"""Capture current desktop state: UI elements and optional annotated screenshot.

		Returns numbered element labels that can be used as targets for click,
		type_text, scroll, and other action tools. Always call this before
		interacting with the UI. Element labels are invalidated after any action.

		With screenshot=True (default), returns an annotated image showing
		numbered labels on interactive elements, plus a JSON summary.
		With screenshot=False, returns just the element data as a dict.
		"""
		scale_factor = get_dpi_scale()
		screen_size = get_screen_size()

		logging.info(f"Snapshot: detail={detail}, window={window}, screenshot={screenshot}, limit={limit}, types={types}")

		# Convert short type names to full UIA names
		type_filter = set(t + "Control" for t in types) if types else None

		# Get UI elements
		elements, metadata = get_desktop_elements(
			detail=detail,
			window_name=window,
			scale_factor=scale_factor,
			limit=limit,
			type_filter=type_filter,
			screen_size=screen_size,
		)

		# Update shared state
		desktop_state.elements = elements
		desktop_state.scale_factor = scale_factor
		desktop_state.screen_size = screen_size
		desktop_state.is_stale = False

		metadata["scale_factor"] = scale_factor
		metadata["screen_size"] = list(screen_size)

		# Build hierarchical element output
		elements_tree = _build_tree_output(elements, include_rects)

		if not screenshot:
			return {
				"metadata": metadata,
				"elements": elements_tree,
			}

		# Capture and annotate screenshot
		img = capture_screenshot()
		annotated = annotate_screenshot(img, elements, scale_factor)
		png_bytes = screenshot_to_bytes(annotated)

		text_content = json.dumps({
			"metadata": metadata,
			"elements": elements_tree,
		}, indent=None, separators=(",", ":"))

		return [
			MCPImage(data=png_bytes, format="png"),
			text_content,
		]
