# windows-native-mcp

MCP server for Windows 11+ desktop automation. Provides 6 tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot` | Screenshot + hierarchical UI tree + scored element labels + viewport filtering. `detail: "full"` includes `checked`/`selected` fields. Window-scoped snapshots store the window handle for auto-foreground. Supports coordinate grid overlay (`grid`) and region zoom (`crop`) for precise targeting |
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

## Snapshot Features

### Coordinate Grid Overlay

The `snapshot` tool supports an optional coordinate grid overlay for precise targeting on apps with poor accessibility trees (unnamed buttons, `coords_unavailable` elements).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `grid` | `"off"` \| `"rulers"` \| `"full"` | `"off"` | `rulers`: axis labels on edges. `full`: rulers + interior grid lines |
| `grid_interval` | `int` \| `"auto"` | `"auto"` | Grid spacing in logical pixels. Auto picks ~12 lines per axis |
| `crop` | `[left, top, right, bottom]` \| `null` | `null` | Zoom into a region (logical pixels). Overrides window auto-crop |

Both `grid` and `crop` auto-enable `screenshot=True`. Grid labels always show **absolute screen coordinates**, even when window-scoped or cropped — so coordinates read from the grid can be passed directly to `click(target=[x, y])`.

### Screenshot Output

When `screenshot=True`, annotated screenshots are saved to the `outputs/` directory (git-ignored) with timestamped filenames. The file path is included in the response metadata as `screenshot_path`.

## Performance

Standard mode uses a CacheRequest-based fast path that batches all UI Automation property reads into a single COM roundtrip, typically 10-20x faster than individual COM calls. Falls back to traditional BFS walk on any COM error. Metadata includes `cache_used: true` when the fast path is active.

## Future Enhancements

- Diff mode (return only changed elements since last snapshot)
- Element text content extraction (for EditControl/TextControl)
- Focus state indicator

## Tests

```bash
python tests/test_server.py
```
