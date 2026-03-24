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
				description='Key combination (e.g. "ctrl+s", "ctrl+shift+s", "alt+f4", "win+d", "enter", "tab")',
			),
		],
	) -> dict:
		"""Execute a keyboard shortcut or key combination.

		Supports modifier combos (ctrl, shift, alt, win) and named keys
		(enter, tab, escape, f1-f12, etc.). Element labels are invalidated
		after shortcuts that may change the UI.
		"""
		key_combo(keys)
		desktop_state.invalidate()

		logging.info(f"Shortcut: {keys}")

		return {
			"keys": keys,
			"state": "stale — call snapshot to refresh element labels",
		}
