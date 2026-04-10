"""Type tool — text input via SendInput or clipboard paste."""
import logging
import time
from typing import Annotated, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import (
	mouse_click,
	type_text_sendinput,
	paste_text,
	key_combo,
	focus_window_if_needed,
)
from windows_native_mcp.tools.snapshot import run_post_action_snapshot


def register(mcp: FastMCP):
	"""Register the type_text tool."""

	@mcp.tool(
		name="type_text",
		annotations=ToolAnnotations(
			title="Type Text",
			readOnlyHint=False,
			destructiveHint=False,
			idempotentHint=False,
			openWorldHint=False,
		),
	)
	def type_text(
		text: Annotated[
			str,
			Field(min_length=1, description="Text to type or paste"),
		],
		target: Annotated[
			str | list[int] | None,
			Field(description="Element label or [x, y] to click before typing (omit for currently focused element)"),
		] = None,
		clear: Annotated[
			bool,
			Field(description="Clear the field before typing (Ctrl+A then Delete)"),
		] = False,
		submit: Annotated[
			bool,
			Field(description="Press Enter after typing"),
		] = False,
		method: Annotated[
			Literal["type", "paste", "auto"],
			Field(description="Input method: type (SendInput, preserves clipboard), paste (clipboard, overwrites contents), auto (paste if >20 chars)"),
		] = "auto",
		window: Annotated[
			str | None,
			Field(description="Window to focus before action (default: window from last snapshot)"),
		] = None,
		snapshot: Annotated[
			bool,
			Field(description="Re-snapshot after this action using previous snapshot settings. Saves a round-trip."),
		] = False,
	) -> dict:
		"""Type text into the focused element or a specified target.

		Auto mode uses clipboard paste for text >20 chars (overwrites clipboard)
		and SendInput for shorter text. Use method='type' to preserve clipboard.
		Labels are invalidated after this action. Pass snapshot=True to
		automatically re-snapshot, or call snapshot separately.

		In address bars with autocomplete, typed text may be intercepted by
		dropdown suggestions. Use shortcut(keys='ctrl+l') to re-focus the
		address bar cleanly before retyping.
		"""
		scale = desktop_state.scale_factor
		uipi_warning = desktop_state.uipi_warning(window)

		# Bring target window to foreground before sending input
		focus_window_if_needed(desktop_state, window)

		# Click target to focus it
		if target is not None:
			x, y = desktop_state.resolve_target(target)
			mouse_click(x, y, scale_factor=scale)
			time.sleep(0.15)  # Wait for focus

		# Clear field
		if clear:
			key_combo("ctrl+a")
			time.sleep(0.05)
			key_combo("delete")
			time.sleep(0.05)

		# Determine method
		actual_method = method
		if method == "auto":
			actual_method = "paste" if len(text) > 20 else "type"

		# Type or paste (with fallback on paste failure)
		if actual_method == "paste":
			try:
				paste_text(text)
			except RuntimeError as e:
				logging.warning("paste_text failed (%s), falling back to SendInput", e)
				type_text_sendinput(text)
				actual_method = "type (fallback)"
		else:
			type_text_sendinput(text)

		# Submit
		if submit:
			time.sleep(0.05)
			key_combo("enter")

		desktop_state.invalidate()
		logging.info(f"Type: {len(text)} chars via {actual_method}" + (" + submit" if submit else ""))

		result = {
			"typed": len(text),
			"method": actual_method,
			"submitted": submit,
			"state": "stale",
		}
		if uipi_warning:
			result["warning"] = uipi_warning
		if snapshot:
			result["snapshot"] = run_post_action_snapshot()
		return result
