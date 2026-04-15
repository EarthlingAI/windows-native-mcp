# CLAUDE.md (windows-native-mcp)

Windows 11+ desktop automation MCP server. Six tools for screenshot capture with UI element detection, mouse/keyboard input via Win32 SendInput, and window management.

Update this file when conventions or design principles change. Update `README.md` when the codebase changes (new tools, parameters, files). See `README.md` for tools, parameters, architecture, and setup.

## Design Principles

### Agent-First Tool Design

This server is consumed by AI agents, not humans. Every design decision flows from that:

- **Optimal defaults for zero-config usage.** Parameter defaults are tuned so the most common agent workflow requires no extra parameters. Action tools auto-snapshot after execution (`snapshot=True`). Screenshots include coordinate rulers by default (`grid="rulers"`). An agent can start using the tools with just `snapshot()` → `click(target="5")` — no flags to remember.
- **Observe-act loop as the core abstraction.** `snapshot` produces a numbered UI element tree. Action tools consume labels and auto-refresh the tree. The agent never manages state manually — labels are created by snapshot, consumed by actions, and regenerated automatically.
- **Self-explanatory schemas.** Pydantic `Field(description=...)` strings are the primary documentation agents see. They must be concise, accurate, and sufficient to use the tool without reading server instructions. If an agent needs to read the README to use a tool correctly, the descriptions have failed.
- **Errors that guide recovery.** UIPI warnings name the cause and the fix ("run from admin terminal"). Stale state errors tell the agent to re-snapshot. Missing labels list available labels. Coordinate errors suggest screenshot-based targeting.
- **Geometrically correct filtering.** Viewport filtering uses AABB intersection (does the element rect overlap the window rect?), not center-point testing. Scoring uses log-area. No magic constants — correctness over heuristics.

## Architecture

```
windows_native_mcp/
├── main.py      # FastMCP server + tool registration (thin — no business logic)
├── tools/       # One file per interaction type, each exports register(mcp)
└── core/        # Shared modules: state, UI automation (dual BFS paths), input, screenshot
```

`core/uia.py` and `core/cached_walk.py` are dual code paths for the same operation — changes to one must be mirrored in the other. `core/state.py` holds the shared element registry consumed by all action tools.

## Key Invariants

These must remain true across all changes:

1. **Action tools default `snapshot=True`** — auto-refresh keeps state fresh without agent effort
2. **Snapshot `grid` defaults to `"rulers"`** — applied when screenshot is enabled, ignored otherwise
3. **`crop` auto-enables screenshot; `grid` does not** — decoupled so grid default doesn't force screenshots
4. **Both BFS paths use identical AABB intersection:** `right < vl or left > vr or bottom < vt or top > vb`
5. **`resolve_target` handles string-encoded coordinate lists** — Pydantic union coercion converts `[x, y]` to `"[x, y]"` for `str | list[int] | None` params. The fallback JSON parse in `resolve_target` catches this for all tools.
6. **Navigation heuristic: `_NAV_TYPES` + ListItem sibling count** — TabItem/MenuItem/TreeItem are always nav. ListItem with ≤10 same-type siblings = nav, >10 = data. This drives both reserved slots and adaptive termination.
7. **All action tools use `run_post_action_snapshot()`** (window-scoped replay). `run_post_action_snapshot_unscoped()` remains in the codebase for potential future use but is not called by any current tool.
8. **Auto-snapshot replays ALL stored params** from last explicit `snapshot()` — including `screenshot`, `grid`, `grid_interval`, `crop`, `monitor`. If agent was in screenshot mode, auto-snapshot stays in screenshot mode.
9. **Cursor is always composited onto screenshots** — no parameter, no toggle. Uses Win32 `GetCursorInfo` with synthetic green arrow fallback.
10. **Mouse input always uses `MOUSEEVENTF_VIRTUALDESK`** — coordinates map to full virtual desktop (all monitors), not just primary.
11. **`_score_candidate` offscreen check uses monitor-relative bounds** via `screen_origin` parameter — elements on secondary monitors are scored correctly when that monitor is active.

## Conventions

- **Tabs** for indentation
- **Python 3.12+**, **Pydantic v2** for tool parameter schemas
- **`Annotated[type, Field(description=...)]`** for all tool parameters — descriptions only when name + type aren't self-explanatory
- **`ToolError`** (`fastmcp.exceptions`) for agent-facing errors — include what went wrong and what to do
- **`RuntimeError`** in core modules — FastMCP auto-converts to `{ isError: true }`
- **`logging.info()`** directly (not `getLogger(__name__)`) with tab-indented hierarchy
- **All 4 tool annotations set explicitly** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`)
- **All coordinates are logical pixels** — multiply by `scale_factor` for physical pixels. DPI handling lives in `core/screen.py`
- **`MonitorInfo`** is the canonical monitor representation — used by `capture_screenshot()`, `annotate_screenshot()`, `_score_candidate()`, and stored in `desktop_state`
- **`capture_screenshot()` always composites cursor** before returning — downstream consumers get cursor "for free"
- **No pywin32/comtypes** — ctypes only for Win32 API calls (exception: `cached_walk.py` uses comtypes for UIA CacheRequest)

## Adding a New Tool

1. Create `windows_native_mcp/tools/{name}.py` — export `register(mcp: FastMCP)`
2. Define tool with `@mcp.tool()`, Pydantic `Field()` params, all 4 `ToolAnnotations`
3. Use `desktop_state.resolve_target()` for any target parameter
4. Call `desktop_state.invalidate()` after actions that change the UI
5. Add `snapshot` parameter defaulting to `True` with post-action snapshot call
6. Import and call `{name}.register(mcp)` in `main.py`
7. Add tests in `tests/test_server.py` and register in the `__main__` block
8. Update `README.md` tool table
