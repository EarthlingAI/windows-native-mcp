"""Shortcut tool — keyboard combos via SendInput."""
import logging
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import key_combo


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
	) -> dict:
		"""Execute a keyboard shortcut or key combination.

		Supports modifier combos (ctrl, shift, alt, win) and named keys
		(enter, tab, escape, f1-f12, etc.). Element labels are invalidated
		after shortcuts that may change the UI.
		"""
		uipi_warning = desktop_state.uipi_warning()

		key_combo(keys)
		desktop_state.invalidate()

		logging.info(f"Shortcut: {keys}")

		result = {
			"keys": keys,
			"state": "stale — call snapshot to refresh element labels",
		}
		if uipi_warning:
			result["warning"] = uipi_warning
		return result
