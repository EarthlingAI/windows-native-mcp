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
		"click/type_text/scroll/shortcut/app. Labels expire after every action — pass "
		"snapshot=true on action tools to auto-refresh in one call, or call snapshot "
		"separately. Scope snapshots to the target window for faster, more complete results. "
		"Only enable screenshot when the UI tree alone is insufficient. "
		"All coordinates are logical pixels on the primary monitor."
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
