# windows-native-mcp

MCP server for Windows 11+ desktop automation. Provides 6 tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot` | Screenshot + hierarchical UI tree + scored element labels + viewport filtering. `detail: "full"` includes `checked`/`selected` fields. Window-scoped snapshots store the window handle for auto-foreground. Supports coordinate grid overlay (`grid`) and region zoom (`crop`) for precise targeting |
| `click` | Mouse click/hover/drag via SendInput. Auto-refreshes element labels after action (default). Auto-focuses window from last scoped snapshot |
| `type_text` | Text input via SendInput or clipboard paste. Auto-refreshes after action (default) |
| `scroll` | Mouse wheel via SendInput. Auto-refreshes after action (default) |
| `shortcut` | Keyboard combos via SendInput. Auto-refreshes after action (default, desktop-wide) |
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

- **`main.py` is a thin dispatcher** — server construction with `instructions=`, tool registration via module `register()` functions. No business logic.
- **`core/state.py` is the shared state contract** — `DesktopState` singleton holds the element registry populated by snapshot and consumed by action tools. `resolve_target()` converts labels or `[x, y]` coordinates to pixel coords.
- **`core/uia.py` and `core/cached_walk.py` are dual code paths** — the cached walk batches all COM property reads into a single roundtrip (faster). BFS walk is the fallback. Both must produce identical filtering, scoring, and termination behavior.
- **Each `tools/*.py` exports `register(mcp)`** — handles parameter validation (Pydantic), state consumption, and response formatting. Business logic stays in `core/`.

## Snapshot Features

### Coordinate Grid Overlay

The `snapshot` tool supports an optional coordinate grid overlay for precise targeting on apps with poor accessibility trees (unnamed buttons, `coords_unavailable` elements).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `grid` | `"off"` \| `"rulers"` \| `"full"` | `"rulers"` | `rulers`: axis labels on edges. `full`: rulers + interior grid lines. Only applies when screenshot is enabled |
| `grid_interval` | `int` \| `"auto"` | `"auto"` | Grid spacing in logical pixels. Auto picks ~12 lines per axis |
| `crop` | `[left, top, right, bottom]` \| `null` | `null` | Zoom into a region (logical pixels). Overrides window auto-crop |

`crop` auto-enables `screenshot=True`. Grid only applies when screenshot is enabled (ignored otherwise). Grid labels always show **absolute screen coordinates**, even when window-scoped or cropped — so coordinates read from the grid can be passed directly to `click(target=[x, y])`.

### Screenshot Output

When `screenshot=True`, annotated screenshots are saved to the `outputs/` directory (git-ignored) with timestamped filenames. The file path is included in the response metadata as `screenshot_path`.

## Auto-Snapshot on Action Tools

All action tools (`click`, `type_text`, `scroll`, `shortcut`) accept a `snapshot` parameter (default `true`). The tool automatically re-snapshots after the action using the previous snapshot's settings (window, detail level, limit, types, viewport_only). A 150ms settling delay allows UI transitions to complete before re-capturing. Pass `snapshot=false` to skip. The `shortcut` tool uses a desktop-wide (unscoped) auto-snapshot since shortcuts may change window focus.

Early termination is adaptive: BFS continues past the default candidate cap if no navigation elements (TabItem, MenuItem, TreeItem) have been collected yet, up to a hard cap.

## Scoring System

Elements are ranked by a scoring function that considers:
- **Area** (log scale) — larger elements score higher
- **Name quality** — named elements get a bonus; PUA-only names get a penalty
- **Depth bonus** — shallow elements (depth ≤2: +40, depth ≤5: +20) are prioritized as they're structurally important (navigation, toolbars)
- **Navigation role boost** — TabItem, MenuItem, TreeItem get +35; ListItem with ≤10 siblings gets +25 (likely navigation, not data)
- **Sibling repetition penalty** — elements with >20 same-type siblings under the same parent get -30, deprioritizing data rows in large lists
- **Offscreen/coords_unavailable penalties**
- **Reserved slots** — After scoring, TabItem/MenuItem/TreeItem elements that were pruned are swapped back in (up to 20 slots) by evicting the lowest-scored elements. Guarantees navigation elements survive even in 5000+ element UIs

## Data Collapse Output Optimization

For data-heavy UIs (Task Manager, File Explorer), the tree output collapses Text children of data types (`TreeItem`, `ListItem`, `DataItem`) into a compact `values` array when all children are Text and there are 2+. This reduces context size by 40-60% for data-heavy apps while preserving all label references for targeting.

## Performance

Standard mode uses a CacheRequest-based fast path that batches all UI Automation property reads into a single COM roundtrip, typically 10-20x faster than individual COM calls. Falls back to traditional BFS walk on any COM error. Metadata includes `cache_used: true` when the fast path is active.

## Response Format

**Snapshot (no screenshot):**
```python
{"metadata": {"element_count": N, "window": "...", ...}, "elements": [{tree}]}
```

**Snapshot (with screenshot):**
```python
[Image(png), '{"metadata": {..., "screenshot_path": "..."}, "elements": [...]}']
```

**Action tools (click, scroll, shortcut, type_text):**
```python
{"action": "...", "coordinates": [x, y], "state": "stale", "snapshot": {snapshot result if enabled}}
```

**Errors:** `ToolError("message with cause + recovery action")` — FastMCP converts to `{ isError: true }`.

## Future Enhancements

- Diff mode (return only changed elements since last snapshot)
- Element text content extraction (for EditControl/TextControl)
- Focus state indicator

## Tests

```bash
python tests/test_server.py
```
