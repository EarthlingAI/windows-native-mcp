"""FastMCP server for Windows 11+ desktop automation."""
import os

from fastmcp import FastMCP

from windows_native_mcp.tools import snapshot, click, type_text, scroll, shortcut, app

mcp = FastMCP(
	"windows_native_mcp",
	instructions="Windows 11+ desktop automation: screenshots, UI interaction, window management. Observe-act loop: snapshot to get numbered labels, then act with click/type_text/scroll/shortcut/app. Labels expire after every action — re-snapshot before the next interaction. All coordinates are logical pixels on the primary monitor. Cannot send input to elevated (admin) windows from a non-elevated process.",
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
