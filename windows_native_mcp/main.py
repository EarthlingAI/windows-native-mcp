"""FastMCP server for Windows 11+ desktop automation."""
import os

from fastmcp import FastMCP

from windows_native_mcp.tools import snapshot, click, type_text, scroll, shortcut, app

mcp = FastMCP(
	"windows_native_mcp",
	instructions=(
		"OS-level native Windows desktop automation — take screenshots, click UI elements, "
		"type text, scroll, press keyboard shortcuts, and launch or manage applications. "
		"Observe-act loop: call snapshot to get numbered UI element labels, then act with "
		"click/type_text/scroll/shortcut/app. Action tools auto-snapshot after execution "
		"(pass snapshot=false to skip). Scope snapshots to the target window for faster, "
		"more complete results. Modal dialogs (File Open, Save As, Print) are children of "
		"their parent window — scope to the parent app, not the dialog title. "
		"Only enable screenshot when the UI tree alone is insufficient. "
		"In address bars with autocomplete, use Ctrl+L to re-focus before typing a path. "
		"All coordinates are logical pixels. Use the monitor parameter to work on different displays."
	),
)


def register_tools():
	"""Register all tools with the MCP server."""
	snapshot.register(mcp)
	click.register(mcp)
	type_text.register(mcp)
	scroll.register(mcp)
	shortcut.register(mcp)
	app.register(mcp)


def run_server():
	"""Run the Windows Native MCP server."""
	register_tools()
	mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio").lower())
