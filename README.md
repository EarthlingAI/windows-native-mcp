# windows-native-mcp

MCP server for Windows 11+ desktop automation. Provides 6 tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot` | Screenshot + hierarchical UI tree + scored element labels + viewport filtering. `detail: "full"` includes `checked`/`selected` fields. Window-scoped snapshots store the window handle for auto-foreground. Supports coordinate grid overlay (`grid`) and region zoom (`crop`) for precise targeting |
| `click` | Mouse click/hover/drag via SendInput. `snapshot=True` auto-refreshes element labels after action. Auto-focuses window from last scoped snapshot |
| `type_text` | Text input via SendInput or clipboard paste. `snapshot=True` auto-refreshes after action |
| `scroll` | Mouse wheel via SendInput. `snapshot=True` auto-refreshes after action |
| `shortcut` | Keyboard combos via SendInput. `snapshot=True` auto-refreshes after action |
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

## Auto-Snapshot on Action Tools

All action tools (`click`, `type_text`, `scroll`, `shortcut`) accept a `snapshot` parameter (default `false`). When `true`, the tool automatically re-snapshots after the action using the previous snapshot's settings (window, detail level, limit, types, viewport_only), saving a round-trip. A 150ms settling delay allows UI transitions to complete before re-capturing.

## Scoring System

Elements are ranked by a scoring function that considers:
- **Area** (log scale) — larger elements score higher
- **Name quality** — named elements get a bonus; PUA-only names get a penalty
- **Depth bonus** — shallow elements (depth ≤2: +40, depth ≤5: +20) are prioritized as they're structurally important (navigation, toolbars)
- **Sibling repetition penalty** — elements with >20 same-type siblings under the same parent get -30, deprioritizing data rows in large lists
- **Offscreen/coords_unavailable penalties**

## Data Collapse Output Optimization

For data-heavy UIs (Task Manager, File Explorer), the tree output collapses Text children of data types (`TreeItem`, `ListItem`, `DataItem`) into a compact `values` array when all children are Text and there are 2+. This reduces context size by 40-60% for data-heavy apps while preserving all label references for targeting.

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
