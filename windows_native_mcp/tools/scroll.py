"""Scroll tool — mouse wheel events via SendInput."""
import logging
from typing import Annotated, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import mouse_scroll, focus_window_if_needed
from windows_native_mcp.tools.snapshot import run_post_action_snapshot


def register(mcp: FastMCP):
	"""Register the scroll tool."""

	@mcp.tool(
		name="scroll",
		annotations=ToolAnnotations(
			title="Scroll",
			readOnlyHint=False,
			destructiveHint=False,
			idempotentHint=False,
			openWorldHint=False,
		),
	)
	def scroll(
		direction: Annotated[
			Literal["up", "down", "left", "right"],
			Field(description="Scroll direction"),
		],
		target: Annotated[
			str | list[int] | None,
			Field(description="Element label or [x, y] to scroll at (default: screen center)"),
		] = None,
		amount: Annotated[
			int,
			Field(ge=1, le=20, description="Number of scroll wheel clicks"),
		] = 3,
		window: Annotated[
			str | None,
			Field(description="Window to focus before action (default: window from last snapshot)"),
		] = None,
		snapshot: Annotated[
			bool,
			Field(description="Re-snapshot after this action using previous snapshot settings. Saves a round-trip."),
		] = False,
	) -> dict:
		"""Scroll at a target location or screen center.

		Labels are invalidated after scrolling. Pass snapshot=True to
		automatically re-snapshot, or call snapshot separately.
		Auto-focuses the window from the last scoped snapshot.
		"""
		scale = desktop_state.scale_factor
		uipi_warning = desktop_state.uipi_warning(window)

		# Bring target window to foreground before sending input
		focus_window_if_needed(desktop_state, window)

		if target is not None:
			x, y = desktop_state.resolve_target(target)
		else:
			sx, sy = desktop_state.screen_size
			x, y = sx // 2, sy // 2

		mouse_scroll(x, y, direction=direction, amount=amount, scale_factor=scale)
		desktop_state.invalidate()

		logging.info(f"Scroll: {direction} {amount} clicks at ({x},{y})")

		result = {
			"direction": direction,
			"amount": amount,
			"coordinates": [x, y],
			"state": "stale",
		}
		if uipi_warning:
			result["warning"] = uipi_warning
		if snapshot:
			result["snapshot"] = run_post_action_snapshot()
		return result
