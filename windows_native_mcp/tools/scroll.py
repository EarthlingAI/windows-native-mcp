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
			Field(description="Auto-refresh UI tree after this action using previous snapshot settings. Set false to skip."),
		] = True,
		delay: Annotated[
			float,
			Field(
				ge=0, le=10.0,
				description="Seconds to wait after action before auto-snapshot. "
				"Default 0.15s handles most UI updates. Only increase when you know "
				"the action triggers a slow transition: 0.3-0.5 for menus/dropdowns, "
				"1.0 for dialogs, 2.0+ for app launches or page navigation. "
				"Use 0 to skip the wait entirely."
			),
		] = 0.15,
	) -> dict:
		"""Scroll at a target location or screen center.

		Labels are invalidated and auto-refreshed after scrolling.
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
			result["snapshot"] = run_post_action_snapshot(delay=delay)
		return result
