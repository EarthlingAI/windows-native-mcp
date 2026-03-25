# windows-native-mcp

MCP server for Windows 11+ desktop automation. Provides 6 tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot` | Screenshot + hierarchical UI tree + scored element labels + viewport filtering. `detail: "full"` includes `checked`/`selected` fields. Window-scoped snapshots store the window handle for auto-foreground |
| `click` | Mouse click/hover/drag via SendInput. Optional `window` param to focus a specific window before action. Auto-focuses window from last scoped snapshot |
| `type_text` | Text input via SendInput or clipboard paste. Optional `window` param for auto-foreground |
| `scroll` | Mouse wheel via SendInput. Optional `window` param for auto-foreground |
| `shortcut` | Keyboard combos via SendInput |
| `app` | Window launch (incl. UWP)/switch/resize/close/list-open/list-installed/restore |

## Setup

```bash
pip install -e .
```

## Usage

```bash
# stdio (default)
python server.py

# SSE
MCP_TRANSPORT=sse python server.py
```

## Requirements

- Python 3.12+
- Windows 11+

## Architecture

```
windows_native_mcp/
├── main.py          # FastMCP server + tool registration
├── tools/           # 6 tool modules
│   ├── snapshot.py
│   ├── click.py
│   ├── type_text.py
│   ├── scroll.py
│   ├── shortcut.py
│   └── app.py
└── core/            # Shared modules
    ├── state.py     # Element registry + invalidation
    ├── screen.py    # MSS screenshot + DPI + annotation
    ├── uia.py       # UI Automation wrapper
    ├── input.py     # SendInput (mouse + keyboard + clipboard)
    └── cached_walk.py  # CacheRequest fast-path walk
```

## Performance

Standard mode uses a CacheRequest-based fast path that batches all UI Automation property reads into a single COM roundtrip, typically 10-20x faster than individual COM calls. Falls back to traditional BFS walk on any COM error. Metadata includes `cache_used: true` when the fast path is active.

## Future Enhancements

- Region/area parameter (scope to sub-region of window)
- Diff mode (return only changed elements since last snapshot)
- Element text content extraction (for EditControl/TextControl)
- Focus state indicator

## Tests

```bash
python tests/test_server.py
```
