"""Click tool — mouse click, hover, and drag via SendInput."""
import logging
import time
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import mouse_click, mouse_move, mouse_drag, focus_window_if_needed


def register(mcp: FastMCP):
	"""Register the click tool."""

	@mcp.tool(
		name="click",
		annotations=ToolAnnotations(
			title="Mouse Click",
			readOnlyHint=False,
			destructiveHint=False,
			idempotentHint=False,
			openWorldHint=False,
		),
	)
	def click(
		target: Annotated[
			str | list[int],
			Field(description="Element label from snapshot (e.g. '5') or [x, y] logical pixel coordinates"),
		],
		button: Annotated[
			Literal["left", "right", "middle"],
			Field(description="Mouse button"),
		] = "left",
		clicks: Annotated[
			int,
			Field(ge=0, le=3, description="Number of clicks (0=hover, 1=single, 2=double, 3=triple)"),
		] = 1,
		drag_to: Annotated[
			str | list[int] | None,
			Field(description="Drag destination: element label or [x, y] coordinates"),
		] = None,
		modifiers: Annotated[
			list[str] | None,
			Field(description='Keys to hold during click (e.g. ["ctrl"], ["shift"])'),
		] = None,
		window: Annotated[
			str | None,
			Field(description="Window to focus before action (default: window from last snapshot)"),
		] = None,
	) -> dict:
		"""Click, double-click, right-click, hover, or drag at a target.

		Use element labels from a recent snapshot for precise targeting.
		Labels are invalidated after this action — call snapshot again to
		get fresh labels before the next interaction.
		"""
		x, y = desktop_state.resolve_target(target)
		scale = desktop_state.scale_factor

		# Bring target window to foreground before sending input
		focus_window_if_needed(desktop_state, window)

		# Hold modifiers if specified
		if modifiers:
			from windows_native_mcp.core.input import hold_modifiers, release_modifiers
			hold_modifiers(modifiers)

		try:
			if drag_to is not None:
				dx, dy = desktop_state.resolve_target(drag_to)
				mouse_drag(x, y, dx, dy, button=button, scale_factor=scale)
				action = f"drag from ({x},{y}) to ({dx},{dy})"
			elif clicks == 0:
				mouse_move(x, y, scale_factor=scale)
				action = f"hover at ({x},{y})"
			else:
				mouse_click(x, y, button=button, clicks=clicks, scale_factor=scale)
				action = f"{button} {'double-' if clicks == 2 else 'triple-' if clicks == 3 else ''}click at ({x},{y})"
		finally:
			if modifiers:
				from windows_native_mcp.core.input import release_modifiers
				release_modifiers(modifiers)

		desktop_state.invalidate()
		logging.info(f"Click: {action}")

		return {
			"action": action,
			"coordinates": [x, y],
			"state": "stale — call snapshot to refresh element labels",
		}
