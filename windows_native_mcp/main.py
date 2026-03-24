"""FastMCP server for Windows 11+ desktop automation."""
import os

from fastmcp import FastMCP

from windows_native_mcp.tools import snapshot, click, type_text, scroll, shortcut, app

mcp = FastMCP(
	"windows_native_mcp",
	instructions="Windows 11+ desktop automation: screenshots, UI interaction, window management. Use snapshot first to observe the desktop, then act with click/type_text/scroll/shortcut/app.",
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
