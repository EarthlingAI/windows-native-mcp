# windows-native-mcp

MCP server for Windows 11+ desktop automation. Provides 6 tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management. Supports multi-monitor setups with per-monitor DPI.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot` | Screenshot + hierarchical UI tree + scored element labels + viewport filtering. `detail: "full"` includes `checked`/`selected` fields. Window-scoped snapshots store the window handle for auto-foreground. Supports coordinate grid overlay (`grid`), region zoom (`crop`), and multi-monitor (`monitor`) |
| `click` | Mouse click/hover/drag via SendInput. Auto-refreshes element labels after action (default). Auto-focuses window from last scoped snapshot. Configurable `delay` for settling |
| `type_text` | Text input via SendInput or clipboard paste. Auto-refreshes after action (default). Configurable `delay` |
| `scroll` | Mouse wheel via SendInput. Auto-refreshes after action (default). Configurable `delay` |
| `shortcut` | Keyboard combos via SendInput. Auto-refreshes after action (default). Configurable `delay` |
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
    ├── state.py     # Element registry + invalidation + MonitorInfo storage
    ├── screen.py    # MSS screenshot + cursor composite + DPI + annotation + monitor enumeration
    ├── uia.py       # UI Automation wrapper + scoring
    ├── input.py     # SendInput (mouse + keyboard + clipboard) with virtual desktop mapping
    └── cached_walk.py  # CacheRequest fast-path walk
```

- **`main.py` is a thin dispatcher** — server construction with `instructions=`, tool registration via module `register()` functions. No business logic.
- **`core/state.py` is the shared state contract** — `DesktopState` singleton holds the element registry populated by snapshot and consumed by action tools. `resolve_target()` converts labels or `[x, y]` coordinates to pixel coords. Also stores `active_monitor`, `monitors`, and `last_snapshot_params` for auto-snapshot replay.
- **`core/screen.py` is the visual pipeline** — monitor enumeration (`MonitorInfo`), per-monitor screenshot capture, cursor compositing (always on), DPI, annotation, grid overlay, crop. `capture_screenshot()` returns images with cursor already composited.
- **`core/uia.py` and `core/cached_walk.py` are dual code paths** — the cached walk batches all COM property reads into a single roundtrip (faster). BFS walk is the fallback. Both must produce identical filtering, scoring, and termination behavior.
- **`core/input.py` uses virtual desktop mapping** — `MOUSEEVENTF_VIRTUALDESK` ensures mouse coordinates work correctly across all monitors.
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

### Multi-Monitor Support

The `snapshot` tool supports a `monitor` parameter for working across multiple displays:

| Value | Behavior |
|---|---|
| `None` (default) | Primary monitor. Auto-detected from window when `window` is set |
| `1`, `2`, ... | Specific monitor by index (1 = primary, 2+ = others sorted left-to-right) |
| `"all"` | Full virtual desktop (stitched screenshot, all monitors' elements) |

Monitor metadata is included in every snapshot response (`monitors` array with index, rect, primary flag; `active_monitor` index). DPI scaling, viewport filtering, and element scoring are per-monitor.

To place windows on a secondary monitor via the `app` tool (launch or resize), use the monitor's virtual-desktop coordinates in `position`. Monitors to the left of the primary have negative x values (visible in snapshot metadata). `position` works with both explicit `[w, h]` sizes and size presets — when a preset and position are both given, the preset supplies dimensions and the user-supplied position overrides placement.

### Cursor Compositing

The system cursor is always composited onto screenshots via Win32 `GetCursorInfo`. No parameter or toggle — the cursor appears whenever it's visible on screen. Falls back to a synthetic bright green arrow if bitmap extraction fails. Cursor is drawn before annotations/grid/crop.

### Screenshot Output

When `screenshot=True`, annotated screenshots are saved to the `outputs/` directory (git-ignored) with timestamped filenames. The file path is included in the response metadata as `screenshot_path`.

## Auto-Snapshot on Action Tools

All action tools (`click`, `type_text`, `scroll`, `shortcut`) accept `snapshot` (default `true`) and `delay` parameters. After the action, the tool waits `delay` seconds (default 0.15s) then re-snapshots using ALL settings from the last explicit `snapshot()` call — including `screenshot`, `grid`, `grid_interval`, `crop`, and `monitor`. This means if the agent entered "screenshot mode", auto-snapshots also include screenshots. Pass `snapshot=false` to skip. For focus-changing shortcuts (Alt+Tab, Win+D), call `snapshot()` afterward to re-orient.

Early termination is adaptive: BFS continues past the default candidate cap if no navigation elements (TabItem, MenuItem, TreeItem) have been collected yet, up to a hard cap.

## Scoring System

Elements are ranked by a scoring function that considers:
- **Area** (log scale) — larger elements score higher
- **Name quality** — named elements get a bonus; PUA-only names get a penalty
- **Depth bonus** — shallow elements (depth ≤2: +40, depth ≤5: +20) are prioritized as they're structurally important (navigation, toolbars)
- **Navigation role boost** — TabItem, MenuItem, TreeItem get +35; ListItem with ≤10 siblings gets +25 (likely navigation, not data)
- **Sibling repetition penalty** — elements with >20 same-type siblings under the same parent get -30, deprioritizing data rows in large lists
- **Offscreen penalties** — uses monitor-relative bounds (via `screen_origin`) so elements on the active monitor are scored correctly
- **Reserved slots** — After scoring, TabItem/MenuItem/TreeItem elements that were pruned are swapped back in (up to 20 slots) by evicting the lowest-scored elements. Guarantees navigation elements survive even in 5000+ element UIs

## Data Collapse Output Optimization

For data-heavy UIs (Task Manager, File Explorer), the tree output collapses Text children of data types (`TreeItem`, `ListItem`, `DataItem`) into a compact `values` array when all children are Text and there are 2+. This reduces context size by 40-60% for data-heavy apps while preserving all label references for targeting.

## Performance

Standard mode uses a CacheRequest-based fast path that batches all UI Automation property reads into a single COM roundtrip, typically 10-20x faster than individual COM calls. Falls back to traditional BFS walk on any COM error. Metadata includes `cache_used: true` when the fast path is active.

## Response Format

**Snapshot (no screenshot):**
```python
{"metadata": {"element_count": N, "window": "...", "monitors": [...], ...}, "elements": [{tree}]}
```

**Snapshot (with screenshot):**
```python
[Image(png), '{"metadata": {..., "screenshot_path": "...", "monitors": [...]}, "elements": [...]}']
```

**Action tools (click, scroll, shortcut, type_text):**
```python
# click:
{"action": "...", "coordinates": [x, y], "state": "stale", "snapshot": {...}}
# type_text:
{"typed": N, "method": "type|paste", "submitted": bool, "state": "stale", "snapshot": {...}}
# scroll:
{"direction": "...", "amount": N, "coordinates": [x, y], "state": "stale", "snapshot": {...}}
# shortcut:
{"keys": "...", "state": "stale", "snapshot": {...}}
# All include optional "warning" key. "snapshot" key omitted when snapshot=false.
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
