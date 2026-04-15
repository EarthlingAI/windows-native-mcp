"""Shortcut tool — keyboard combos via SendInput."""
import logging
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import key_combo
from windows_native_mcp.tools.snapshot import run_post_action_snapshot


def register(mcp: FastMCP):
	"""Register the shortcut tool."""

	@mcp.tool(
		name="shortcut",
		annotations=ToolAnnotations(
			title="Keyboard Shortcut",
			readOnlyHint=False,
			destructiveHint=False,
			idempotentHint=False,
			openWorldHint=False,
		),
	)
	def shortcut(
		keys: Annotated[
			str,
			Field(
				min_length=1,
				description="Lowercase key combo using + separator. Modifiers: ctrl, shift, alt, win. Named keys: enter, tab, escape, backspace, delete, space, up, down, left, right, home, end, pageup, pagedown, f1-f12",
			),
		],
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
		"""Execute a keyboard shortcut or key combination.

		Supports modifier combos (ctrl, shift, alt, win) and named keys
		(enter, tab, escape, f1-f12, etc.). Labels are invalidated and
		auto-refreshed after execution. Auto-focuses the window from the
		last scoped snapshot.
		"""
		uipi_warning = desktop_state.uipi_warning()

		key_combo(keys)
		desktop_state.invalidate()

		logging.info(f"Shortcut: {keys}")

		result = {
			"keys": keys,
			"state": "stale",
		}
		if uipi_warning:
			result["warning"] = uipi_warning
		if snapshot:
			result["snapshot"] = run_post_action_snapshot(delay=delay)
		return result
