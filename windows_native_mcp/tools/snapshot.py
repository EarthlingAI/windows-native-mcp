"""Snapshot tool — capture desktop state (screenshot + UI tree + element labels)."""
import json
import logging
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image as MCPImage
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.screen import (
	capture_screenshot,
	annotate_screenshot,
	screenshot_to_bytes,
	get_dpi_scale,
	get_screen_size,
)
from windows_native_mcp.core.uia import get_desktop_elements


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

		logging.info(f"Snapshot: detail={detail}, window={window}, screenshot={screenshot}")

		# Get UI elements
		elements, metadata = get_desktop_elements(
			detail=detail,
			window_name=window,
			scale_factor=scale_factor,
		)

		# Update shared state
		desktop_state.elements = elements
		desktop_state.scale_factor = scale_factor
		desktop_state.screen_size = screen_size
		desktop_state.is_stale = False

		metadata["scale_factor"] = scale_factor
		metadata["screen_size"] = list(screen_size)

		# Build element summary for text output
		elements_summary = []
		for label, elem in elements.items():
			entry = {
				"label": label,
				"name": elem.name,
				"type": elem.control_type,
				"enabled": elem.is_enabled,
			}
			if elem.coords_unavailable:
				entry["coords_unavailable"] = True
			else:
				entry["center"] = list(elem.center)
				entry["rect"] = list(elem.bounding_rect)
			if elem.automation_id:
				entry["automation_id"] = elem.automation_id
			elements_summary.append(entry)

		if not screenshot:
			return {
				"metadata": metadata,
				"elements": elements_summary,
			}

		# Capture and annotate screenshot
		img = capture_screenshot()
		annotated = annotate_screenshot(img, elements, scale_factor)
		png_bytes = screenshot_to_bytes(annotated)

		text_content = json.dumps({
			"metadata": metadata,
			"elements": elements_summary,
		}, indent=None, separators=(",", ":"))

		return [
			MCPImage(data=png_bytes, format="png"),
			text_content,
		]
