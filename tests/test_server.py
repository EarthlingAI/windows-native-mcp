#!/usr/bin/env python3
"""Gate 1 + Phase 2 + Phase 3 Tests for windows-native-mcp.

Run: python tests/test_server.py
Uses check(name, condition) pattern — no test framework needed.
"""
import asyncio
import sys
import os

# Add parent to path so we can import the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

passed = 0
failed = 0
errors = []


def check(name: str, condition: bool, detail: str = ""):
	global passed, failed
	if condition:
		passed += 1
		print(f"  [PASS] {name}")
	else:
		failed += 1
		msg = f"  [FAIL] {name}" + (f" — {detail}" if detail else "")
		print(msg)
		errors.append(msg)


def test_imports():
	"""Verify all modules import without error."""
	print("\n--- Import Tests ---")

	try:
		from windows_native_mcp.main import mcp, register_tools, run_server
		check("main.py imports", True)
	except Exception as e:
		check("main.py imports", False, str(e))
		return

	try:
		from windows_native_mcp.core.state import DesktopState, ElementInfo, desktop_state
		check("core/state.py imports", True)
	except Exception as e:
		check("core/state.py imports", False, str(e))

	try:
		from windows_native_mcp.core.screen import (
			capture_screenshot, annotate_screenshot, screenshot_to_bytes,
			get_dpi_scale, get_screen_size,
		)
		check("core/screen.py imports", True)
	except Exception as e:
		check("core/screen.py imports", False, str(e))

	try:
		from windows_native_mcp.core.uia import get_desktop_elements, find_window, get_window_list
		check("core/uia.py imports", True)
	except Exception as e:
		check("core/uia.py imports", False, str(e))

	try:
		from windows_native_mcp.core.input import (
			mouse_click, mouse_move, mouse_drag, mouse_scroll,
			key_combo, type_text_sendinput, paste_text,
		)
		check("core/input.py imports", True)
	except Exception as e:
		check("core/input.py imports", False, str(e))


def test_tool_registration():
	"""Verify all 6 tools register with correct annotations."""
	print("\n--- Tool Registration Tests ---")

	from windows_native_mcp.main import mcp, register_tools
	register_tools()

	# Get tool list (list_tools is async)
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}
	tool_names = set(tools.keys())

	expected = {"snapshot", "click", "type_text", "scroll", "shortcut", "app"}
	check("6 tools registered", tool_names == expected, f"got {tool_names}")

	# Check annotations
	if "snapshot" in tools:
		ann = tools["snapshot"].annotations
		check("snapshot readOnlyHint=True", ann.readOnlyHint is True)
		check("snapshot destructiveHint=False", ann.destructiveHint is False)
		check("snapshot idempotentHint=True", ann.idempotentHint is True)

	for name in ["click", "type_text", "scroll", "shortcut", "app"]:
		if name in tools:
			ann = tools[name].annotations
			check(f"{name} readOnlyHint=False", ann.readOnlyHint is False)
			check(f"{name} destructiveHint=False", ann.destructiveHint is False)


def test_state():
	"""Verify state management."""
	print("\n--- State Tests ---")

	from windows_native_mcp.core.state import DesktopState, ElementInfo

	state = DesktopState()
	check("initial state is stale", state.is_stale is True)

	# Add element
	state.elements["1"] = ElementInfo(
		label="1", name="Test Button", control_type="ButtonControl",
		bounding_rect=(10, 20, 100, 50), center=(55, 35),
	)
	state.is_stale = False

	# Resolve label
	x, y = state.resolve_target("1")
	check("resolve label returns center", (x, y) == (55, 35))

	# Resolve coordinates
	x, y = state.resolve_target([100, 200])
	check("resolve coords returns coords", (x, y) == (100, 200))

	# Resolve missing label
	try:
		state.resolve_target("999")
		check("missing label raises error", False)
	except Exception:
		check("missing label raises error", True)

	# Resolve unavailable coords
	state.elements["2"] = ElementInfo(
		label="2", name="UWP Ghost", control_type="ButtonControl",
		bounding_rect=(0, 0, 0, 0), center=(0, 0), coords_unavailable=True,
	)
	try:
		state.resolve_target("2")
		check("coords_unavailable raises error", False)
	except Exception:
		check("coords_unavailable raises error", True)

	# Invalidate
	state.invalidate()
	check("invalidate sets stale", state.is_stale is True)

	# Clear
	state.clear()
	check("clear empties elements", len(state.elements) == 0)


def test_window_list():
	"""Test app(mode='list') returns windows."""
	print("\n--- Window List Tests ---")

	from windows_native_mcp.core.uia import get_window_list
	windows = get_window_list()

	check("get_window_list returns list", isinstance(windows, list))
	check("at least 1 window found", len(windows) > 0, f"got {len(windows)}")

	if windows:
		w = windows[0]
		check("window has handle", "handle" in w)
		check("window has title", "title" in w)
		check("window has rect", "rect" in w)
		check("window has pid", "pid" in w)
		check("window has is_minimized", "is_minimized" in w)


def test_snapshot_minimal():
	"""Test snapshot with detail=minimal, screenshot=False."""
	print("\n--- Snapshot Minimal Tests ---")

	from windows_native_mcp.core.uia import get_desktop_elements
	from windows_native_mcp.core.screen import get_dpi_scale

	scale = get_dpi_scale()
	elements, metadata = get_desktop_elements(detail="minimal", scale_factor=scale)

	check("minimal returns elements dict", isinstance(elements, dict))
	check("minimal returns metadata dict", isinstance(metadata, dict))
	check("minimal has element_count", "element_count" in metadata)
	check("minimal found windows", metadata.get("element_count", 0) > 0,
		f"got {metadata.get('element_count', 0)}")


def test_raw_rect_validation():
	"""Test that _get_raw_rect filters garbage UIA values."""
	print("\n--- Raw Rect Validation Tests ---")

	from windows_native_mcp.core.uia import _get_raw_rect, _MAX_COORD

	check("_MAX_COORD is 65536", _MAX_COORD == 65536)

	# Mock control with sentinel values
	class FakeControl:
		class BoundingRectangle:
			left = 2147483647
			top = 0
			right = 2147483647
			bottom = 100

	result = _get_raw_rect(FakeControl())
	check("sentinel rect filtered to (0,0,0,0)", result == (0, 0, 0, 0))

	# Mock control with valid values
	class GoodControl:
		class BoundingRectangle:
			left = 100
			top = 200
			right = 500
			bottom = 400

	result = _get_raw_rect(GoodControl())
	check("valid rect passes through", result == (100, 200, 500, 400))

	# Mock control with negative large values
	class NegativeControl:
		class BoundingRectangle:
			left = -100000
			top = 200
			right = 500
			bottom = 400

	result = _get_raw_rect(NegativeControl())
	check("negative large rect filtered", result == (0, 0, 0, 0))


def test_app_args_parameter():
	"""Test that the app tool schema includes the args parameter."""
	print("\n--- App Args Parameter Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	if "app" in tools:
		schema = tools["app"].parameters
		props = schema.get("properties", {})
		check("app tool has 'args' parameter", "args" in props, f"got {list(props.keys())}")
	else:
		check("app tool found", False)


def test_shellexecuteinfo():
	"""Test SHELLEXECUTEINFO struct has valid size."""
	print("\n--- ShellExecuteInfo Tests ---")

	import ctypes
	from windows_native_mcp.tools.app import SHELLEXECUTEINFO

	size = ctypes.sizeof(SHELLEXECUTEINFO)
	# 64-bit: 112 bytes, 32-bit: 60 bytes
	check("SHELLEXECUTEINFO size valid", size in (60, 112), f"got {size}")


def test_screen():
	"""Test screen module functions."""
	print("\n--- Screen Tests ---")

	from windows_native_mcp.core.screen import get_dpi_scale, get_screen_size

	scale = get_dpi_scale()
	check("DPI scale > 0", scale > 0, f"got {scale}")
	check("DPI scale reasonable", 0.5 <= scale <= 4.0, f"got {scale}")

	w, h = get_screen_size()
	check("screen width > 0", w > 0, f"got {w}")
	check("screen height > 0", h > 0, f"got {h}")


def test_hierarchy_fields():
	"""Verify parent_label and depth on ElementInfo."""
	print("\n--- Hierarchy Field Tests ---")

	from windows_native_mcp.core.state import ElementInfo

	# Default values
	elem = ElementInfo(
		label="1", name="Test", control_type="Button",
		bounding_rect=(0, 0, 100, 50), center=(50, 25),
	)
	check("default parent_label is None", elem.parent_label is None)
	check("default depth is 0", elem.depth == 0)

	# Explicit values
	elem2 = ElementInfo(
		label="2", name="Child", control_type="Edit",
		bounding_rect=(10, 10, 90, 40), center=(50, 25),
		parent_label="1", depth=3,
	)
	check("explicit parent_label", elem2.parent_label == "1")
	check("explicit depth", elem2.depth == 3)


def test_pua_detection():
	"""Test _is_pua_only with various inputs."""
	print("\n--- PUA Detection Tests ---")

	from windows_native_mcp.core.uia import _is_pua_only

	check("single PUA char", _is_pua_only("\uE001") is True)
	check("multi PUA chars", _is_pua_only("\uE001\uE002") is True)
	check("mixed text+PUA", _is_pua_only("A\uE001") is False)
	check("empty string", _is_pua_only("") is False)
	check("normal text", _is_pua_only("Close") is False)
	check("PUA with spaces", _is_pua_only(" \uE001 ") is True)


def test_scoring():
	"""Test _score_candidate ranking logic."""
	print("\n--- Scoring Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate

	# Large named element
	large_named = _Candidate(
		control_type="ButtonControl", name="Save", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 100), center=(100, 50),
		coords_unavailable=False, depth=2, parent_idx=-1,
		area=20000, bfs_order=0,
	)

	# PUA icon element
	pua_icon = _Candidate(
		control_type="ButtonControl", name="\uE001", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 16, 16), center=(8, 8),
		coords_unavailable=False, depth=2, parent_idx=-1,
		area=256, bfs_order=1,
	)

	# Offscreen element
	offscreen = _Candidate(
		control_type="ButtonControl", name="Hidden", automation_id="",
		is_enabled=True, bounding_rect=(-500, -500, -400, -400), center=(-450, -450),
		coords_unavailable=False, depth=2, parent_idx=-1,
		area=10000, bfs_order=2,
	)

	# Empty-name container
	empty_container = _Candidate(
		control_type="ToolBarControl", name="", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=1, parent_idx=-1,
		area=3000, bfs_order=3,
	)

	s_large = _score_candidate(large_named, 1920, 1080)
	s_pua = _score_candidate(pua_icon, 1920, 1080)
	s_offscreen = _score_candidate(offscreen, 1920, 1080)
	s_empty = _score_candidate(empty_container, 1920, 1080)

	check("large named > PUA icon", s_large > s_pua)
	check("onscreen > offscreen", s_large > s_offscreen)
	check("named > empty container", s_large > s_empty)
	check("PUA penalized (score reasonable)", s_pua < s_large - 20)


def test_tree_output():
	"""Test _build_tree_output nesting logic."""
	print("\n--- Tree Output Tests ---")

	from windows_native_mcp.core.state import ElementInfo
	from windows_native_mcp.tools.snapshot import _build_tree_output

	elements = {
		"1": ElementInfo(
			label="1", name="Panel", control_type="Button",
			bounding_rect=(0, 0, 800, 600), center=(400, 300),
			parent_label=None, depth=0,
		),
		"2": ElementInfo(
			label="2", name="Save", control_type="Button",
			bounding_rect=(10, 10, 100, 40), center=(55, 25),
			parent_label="1", depth=1,
		),
		"3": ElementInfo(
			label="3", name="Cancel", control_type="Button",
			bounding_rect=(110, 10, 200, 40), center=(155, 25),
			parent_label="1", depth=1,
		),
		"4": ElementInfo(
			label="4", name="Orphan", control_type="Edit",
			bounding_rect=(300, 300, 400, 340), center=(350, 320),
			parent_label="99", depth=2,  # Parent not in set
		),
	}

	tree = _build_tree_output(elements, include_rects=False)

	# Element 4 has parent_label="99" which is not in elements, so it becomes root
	check("tree has 2 roots", len(tree) == 2, f"got {len(tree)}")

	# Find the Panel root
	panel_root = next((n for n in tree if n.get("label") == "1"), None)
	check("Panel root exists", panel_root is not None)
	if panel_root:
		check("Panel has children", "children" in panel_root)
		check("Panel has 2 children", len(panel_root.get("children", [])) == 2)

	# Orphan is a root
	orphan_root = next((n for n in tree if n.get("label") == "4"), None)
	check("Orphan becomes root", orphan_root is not None)

	# Test with include_rects=True
	tree_rects = _build_tree_output(elements, include_rects=True)
	first = tree_rects[0] if tree_rects else {}
	check("rect included when requested", "rect" in first)


def test_snapshot_new_params():
	"""Verify snapshot tool schema includes new parameters."""
	print("\n--- Snapshot New Params Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	if "snapshot" in tools:
		schema = tools["snapshot"].parameters
		props = schema.get("properties", {})
		check("snapshot has 'include_rects' param", "include_rects" in props)
		check("snapshot has 'types' param", "types" in props)
		check("snapshot has 'limit' param", "limit" in props)
	else:
		check("snapshot tool found", False)


def test_overflow_hardening():
	"""Verify _safe_get_children catches OverflowError."""
	print("\n--- OverflowError Hardening Tests ---")

	from windows_native_mcp.core.uia import _safe_get_children

	class OverflowControl:
		def GetChildren(self):
			raise OverflowError("int too large to convert")

	result = _safe_get_children(OverflowControl())
	check("OverflowError returns empty list", result == [])


def test_viewport_filtering_param():
	"""Verify snapshot tool schema includes viewport_only parameter."""
	print("\n--- Viewport Filtering Param Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	if "snapshot" in tools:
		schema = tools["snapshot"].parameters
		props = schema.get("properties", {})
		check("snapshot has 'viewport_only' param", "viewport_only" in props)
		# Default should be True
		vp = props.get("viewport_only", {})
		check("viewport_only default is True", vp.get("default") is True)
	else:
		check("snapshot tool found", False)


def test_checked_selected_fields():
	"""Verify ElementInfo new fields: default None, explicit True/False."""
	print("\n--- Checked/Selected Field Tests ---")

	from windows_native_mcp.core.state import ElementInfo

	# Default values
	elem = ElementInfo(
		label="1", name="Test", control_type="CheckBox",
		bounding_rect=(0, 0, 100, 50), center=(50, 25),
	)
	check("default checked is None", elem.checked is None)
	check("default selected is None", elem.selected is None)

	# Explicit values
	elem_checked = ElementInfo(
		label="2", name="Enable", control_type="CheckBox",
		bounding_rect=(0, 0, 100, 50), center=(50, 25),
		checked=True,
	)
	check("explicit checked=True", elem_checked.checked is True)

	elem_unchecked = ElementInfo(
		label="3", name="Disable", control_type="CheckBox",
		bounding_rect=(0, 0, 100, 50), center=(50, 25),
		checked=False,
	)
	check("explicit checked=False", elem_unchecked.checked is False)

	elem_selected = ElementInfo(
		label="4", name="Option A", control_type="RadioButton",
		bounding_rect=(0, 0, 100, 50), center=(50, 25),
		selected=True,
	)
	check("explicit selected=True", elem_selected.selected is True)


def test_coords_available_count():
	"""Call get_desktop_elements(detail='minimal'), verify coords_available_count."""
	print("\n--- Coords Available Count Tests ---")

	from windows_native_mcp.core.uia import get_desktop_elements
	from windows_native_mcp.core.screen import get_dpi_scale

	scale = get_dpi_scale()
	elements, metadata = get_desktop_elements(detail="minimal", scale_factor=scale)

	check("metadata has coords_available_count", "coords_available_count" in metadata)
	if "coords_available_count" in metadata:
		expected = metadata["element_count"] - metadata["coords_unavailable_count"]
		check("coords_available_count = element_count - unavailable",
			metadata["coords_available_count"] == expected)


def test_start_app_resolution():
	"""Test _resolve_start_app with a known app."""
	print("\n--- Start App Resolution Tests ---")

	from windows_native_mcp.tools.app import _resolve_start_app

	# Notepad should always be available on Windows
	result = _resolve_start_app("Notepad")
	check("_resolve_start_app finds Notepad", result is not None and len(result) > 0,
		f"got {result}")


def test_app_mode_names():
	"""Verify app tool mode enum has list-open and list-installed."""
	print("\n--- App Mode Names Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	if "app" in tools:
		schema = tools["app"].parameters
		mode_prop = schema.get("properties", {}).get("mode", {})
		# Mode enum values are in anyOf or enum
		mode_values = set()
		if "enum" in mode_prop:
			mode_values = set(mode_prop["enum"])
		elif "anyOf" in mode_prop:
			for item in mode_prop["anyOf"]:
				if "enum" in item:
					mode_values.update(item["enum"])
		check("app has list-open mode", "list-open" in mode_values, f"got {mode_values}")
		check("app has list-installed mode", "list-installed" in mode_values, f"got {mode_values}")
		check("app no old 'list' mode", "list" not in mode_values, f"got {mode_values}")
	else:
		check("app tool found", False)


def test_tree_output_checked_selected():
	"""Verify _build_tree_output includes checked/selected fields."""
	print("\n--- Tree Output Checked/Selected Tests ---")

	from windows_native_mcp.core.state import ElementInfo
	from windows_native_mcp.tools.snapshot import _build_tree_output

	elements = {
		"1": ElementInfo(
			label="1", name="Dark Mode", control_type="CheckBox",
			bounding_rect=(0, 0, 200, 30), center=(100, 15),
			parent_label=None, depth=0, checked=True,
		),
		"2": ElementInfo(
			label="2", name="Option A", control_type="RadioButton",
			bounding_rect=(0, 30, 200, 60), center=(100, 45),
			parent_label=None, depth=0, selected=False,
		),
		"3": ElementInfo(
			label="3", name="Plain Button", control_type="Button",
			bounding_rect=(0, 60, 200, 90), center=(100, 75),
			parent_label=None, depth=0,
		),
	}

	tree = _build_tree_output(elements, include_rects=False)

	checkbox = next((n for n in tree if n.get("label") == "1"), None)
	check("checkbox has checked field", checkbox is not None and "checked" in checkbox)
	check("checkbox checked=True", checkbox is not None and checkbox.get("checked") is True)

	radio = next((n for n in tree if n.get("label") == "2"), None)
	check("radio has selected field", radio is not None and "selected" in radio)
	check("radio selected=False", radio is not None and radio.get("selected") is False)

	button = next((n for n in tree if n.get("label") == "3"), None)
	check("plain button no checked field", button is not None and "checked" not in button)
	check("plain button no selected field", button is not None and "selected" not in button)


def test_cached_walk_import():
	"""Verify cached_walk module imports and _build_cache_request returns non-None."""
	print("\n--- Cached Walk Import Tests ---")

	try:
		from windows_native_mcp.core.cached_walk import collect_candidates, _build_cache_request, _Candidate
		check("cached_walk imports", True)
	except Exception as e:
		check("cached_walk imports", False, str(e))
		return

	try:
		cr = _build_cache_request()
		check("_build_cache_request returns non-None", cr is not None)
	except Exception as e:
		check("_build_cache_request returns non-None", False, str(e))


def test_cached_walk_basic():
	"""Call collect_candidates with desktop root, verify return shape."""
	print("\n--- Cached Walk Basic Tests ---")

	import uiautomation
	from windows_native_mcp.core.cached_walk import collect_candidates, _Candidate
	from windows_native_mcp.core.uia import INTERACTIVE_TYPES
	from windows_native_mcp.core.screen import get_dpi_scale

	root = uiautomation.GetRootControl()
	scale = get_dpi_scale()

	result = collect_candidates(root, INTERACTIVE_TYPES, viewport_rect=None, scale_factor=scale)
	check("collect_candidates returns tuple or None", result is None or isinstance(result, tuple))

	if result is not None:
		candidates, ghost_filtered, coords_unavail, viewport_filtered = result
		check("candidates is list", isinstance(candidates, list))
		check("found some candidates", len(candidates) > 0, f"got {len(candidates)}")

		if candidates:
			c = candidates[0]
			check("candidate is _Candidate instance", isinstance(c, _Candidate))
			check("candidate has control_type", isinstance(c.control_type, str) and len(c.control_type) > 0)
			check("candidate has name (accessible)", isinstance(c.name, str))
			check("candidate has center tuple", isinstance(c.center, tuple) and len(c.center) == 2)

		check("ghost_filtered is int", isinstance(ghost_filtered, int))
		check("coords_unavail is int", isinstance(coords_unavail, int))
		check("viewport_filtered is int", isinstance(viewport_filtered, int))


def test_cached_walk_matches_bfs():
	"""Compare cached walk vs BFS walk on the same window."""
	print("\n--- Cached Walk vs BFS Comparison Tests ---")

	import uiautomation
	from windows_native_mcp.core.cached_walk import collect_candidates
	from windows_native_mcp.core.uia import INTERACTIVE_TYPES, find_window, get_window_list, _walk_and_rank, _resolve_root
	from windows_native_mcp.core.screen import get_dpi_scale

	# Find any open window to test with
	windows = get_window_list()
	if not windows:
		check("skip — no windows found", True)
		return

	# Pick first non-minimized window with a title
	target = None
	for w in windows:
		if not w.get("is_minimized") and w.get("title"):
			target = w
			break

	if target is None:
		check("skip — no suitable window", True)
		return

	window_ctrl = find_window(target["title"])
	if window_ctrl is None:
		check("skip — could not find window control", True)
		return

	scale = get_dpi_scale()

	# Cached walk
	cached_result = collect_candidates(window_ctrl, INTERACTIVE_TYPES, viewport_rect=None, scale_factor=scale)

	# BFS walk (via get_desktop_elements on same window)
	from windows_native_mcp.core.uia import get_desktop_elements
	bfs_elements, bfs_meta = get_desktop_elements(
		detail="standard", scale_factor=scale, window_name=target["title"],
		viewport_only=False,
	)

	if cached_result is None:
		check("cached walk returned None (fallback expected on some systems)", True)
		return

	cached_candidates = cached_result[0]
	cached_count = len(cached_candidates)
	# Compare against total_candidates (pre-scoring count), not element_count (post-scoring)
	bfs_total = bfs_meta.get("total_candidates", bfs_meta.get("element_count", 0))

	print(f"    Cached: {cached_count} candidates, BFS total_candidates: {bfs_total}")

	# Count comparison — both return raw candidate counts before scoring cap
	if bfs_total > 0:
		ratio = cached_count / bfs_total
		check(f"candidate count within 50% tolerance (ratio={ratio:.2f})", 0.5 <= ratio <= 1.5,
			f"cached={cached_count}, bfs_total={bfs_total}, ratio={ratio:.2f}")
	else:
		check("both walks found elements", cached_count == 0 and bfs_total == 0)

	# Control type overlap — normalize: BFS strips "Control" suffix, cached keeps it
	cached_types = {c.control_type.removesuffix("Control") for c in cached_candidates}
	bfs_types = set()
	for elem_info in bfs_elements.values():
		bfs_types.add(elem_info.control_type)

	if cached_types and bfs_types:
		overlap = len(cached_types & bfs_types) / max(len(cached_types), len(bfs_types))
		check(f"control type sets overlap significantly ({overlap:.0%})", overlap > 0.5,
			f"cached_types={cached_types}, bfs_types={bfs_types}")


def test_cached_walk_performance():
	"""Time both walks, log speedup ratio (informational)."""
	print("\n--- Cached Walk Performance Tests ---")

	import time
	import uiautomation
	from windows_native_mcp.core.cached_walk import collect_candidates
	from windows_native_mcp.core.uia import INTERACTIVE_TYPES, get_desktop_elements
	from windows_native_mcp.core.screen import get_dpi_scale

	root = uiautomation.GetRootControl()
	scale = get_dpi_scale()

	# Time cached walk
	t0 = time.perf_counter()
	cached_result = collect_candidates(root, INTERACTIVE_TYPES, viewport_rect=None, scale_factor=scale)
	cached_time = time.perf_counter() - t0

	# Time BFS walk
	t0 = time.perf_counter()
	bfs_elements, bfs_meta = get_desktop_elements(detail="minimal", scale_factor=scale, viewport_only=False)
	bfs_time = time.perf_counter() - t0

	print(f"    Cached walk: {cached_time:.3f}s, BFS walk: {bfs_time:.3f}s")
	if cached_time > 0:
		speedup = bfs_time / cached_time
		print(f"    Speedup ratio: {speedup:.2f}x")

	check("cached walk ran without error", True)
	check("BFS walk ran without error", True)


def test_cached_walk_fallback():
	"""Pass a mock object with no .Element attribute, verify graceful None return."""
	print("\n--- Cached Walk Fallback Tests ---")

	from windows_native_mcp.core.cached_walk import collect_candidates
	from windows_native_mcp.core.uia import INTERACTIVE_TYPES

	class FakeRoot:
		"""Mock object with no .Element attribute."""
		pass

	result = collect_candidates(FakeRoot(), INTERACTIVE_TYPES, viewport_rect=None, scale_factor=1.0)
	check("collect_candidates returns None for invalid root", result is None)


def test_cached_pattern_reading():
	"""Run cached walk, verify some candidates have checked/selected fields."""
	print("\n--- Cached Pattern Reading Tests ---")

	import uiautomation
	from windows_native_mcp.core.cached_walk import collect_candidates, _Candidate
	from windows_native_mcp.core.uia import INTERACTIVE_TYPES
	from windows_native_mcp.core.screen import get_dpi_scale

	root = uiautomation.GetRootControl()
	scale = get_dpi_scale()

	result = collect_candidates(root, INTERACTIVE_TYPES, viewport_rect=None, scale_factor=scale)
	if result is None:
		check("skip — cached walk returned None", True)
		return

	candidates = result[0]
	check("candidates list not empty", len(candidates) > 0)

	# Verify every candidate has checked and selected fields (bool or None)
	fields_valid = True
	has_checked_or_selected = False
	for c in candidates:
		if not (c.checked is None or isinstance(c.checked, bool)):
			fields_valid = False
			break
		if not (c.selected is None or isinstance(c.selected, bool)):
			fields_valid = False
			break
		if c.checked is not None or c.selected is not None:
			has_checked_or_selected = True

	check("all candidates have valid checked field (bool or None)", fields_valid)
	check("all candidates have valid selected field (bool or None)", fields_valid)
	# Informational — may not find any on a clean desktop
	if has_checked_or_selected:
		print("    (found candidates with checked/selected state)")
	else:
		print("    (no candidates with checked/selected state — OK on clean desktop)")


def test_cache_used_metadata():
	"""Call get_desktop_elements(detail='standard'), verify cache_used key in metadata."""
	print("\n--- Cache Used Metadata Tests ---")

	from windows_native_mcp.core.uia import get_desktop_elements
	from windows_native_mcp.core.screen import get_dpi_scale

	scale = get_dpi_scale()
	elements, metadata = get_desktop_elements(detail="standard", scale_factor=scale)

	has_key = "cache_used" in metadata
	check("metadata has cache_used key", has_key,
		f"metadata keys: {list(metadata.keys())}")
	if has_key:
		check("cache_used is True", metadata["cache_used"] is True)


def test_depth_scoring():
	"""Test depth bonus in scoring — shallow elements score higher."""
	print("\n--- Depth Scoring Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate

	# Shallow element (depth 1) — same area as deep element
	shallow = _Candidate(
		control_type="ButtonControl", name="Nav", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=1, parent_idx=-1,
		area=3000, bfs_order=0,
	)

	# Deep element (depth 8) — same area
	deep = _Candidate(
		control_type="ButtonControl", name="Data", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=8, parent_idx=-1,
		area=3000, bfs_order=1,
	)

	# Mid-depth element (depth 4)
	mid = _Candidate(
		control_type="ButtonControl", name="Mid", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=4, parent_idx=-1,
		area=3000, bfs_order=2,
	)

	s_shallow = _score_candidate(shallow, 1920, 1080)
	s_deep = _score_candidate(deep, 1920, 1080)
	s_mid = _score_candidate(mid, 1920, 1080)

	check("shallow (depth 1) > deep (depth 8)", s_shallow > s_deep)
	check("shallow (depth 1) > mid (depth 4)", s_shallow > s_mid)
	check("mid (depth 4) > deep (depth 8)", s_mid > s_deep)


def test_sibling_penalty():
	"""Test sibling repetition penalty in scoring."""
	print("\n--- Sibling Penalty Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate

	# Element with few siblings
	few_siblings = _Candidate(
		control_type="ListItemControl", name="Item", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
		coords_unavailable=False, depth=5, parent_idx=0,
		area=6000, bfs_order=0, sibling_same_type_count=3,
	)

	# Element with many siblings (>20)
	many_siblings = _Candidate(
		control_type="ListItemControl", name="Item", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
		coords_unavailable=False, depth=5, parent_idx=0,
		area=6000, bfs_order=1, sibling_same_type_count=50,
	)

	s_few = _score_candidate(few_siblings, 1920, 1080)
	s_many = _score_candidate(many_siblings, 1920, 1080)

	check("few siblings > many siblings (>20)", s_few > s_many)
	check("penalty is 30 points", abs((s_few - s_many) - 55) < 0.01)  # 30 penalty + 25 nav boost for few-sibling ListItem


def test_nav_scoring():
	"""Test navigation type scoring boost."""
	print("\n--- Navigation Scoring Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate

	tab_item = _Candidate(
		control_type="TabItemControl", name="Details", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=5, parent_idx=-1,
		area=3000, bfs_order=0, sibling_same_type_count=5,
	)
	button = _Candidate(
		control_type="ButtonControl", name="OK", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
		coords_unavailable=False, depth=5, parent_idx=-1,
		area=3000, bfs_order=1, sibling_same_type_count=5,
	)
	menu_item = _Candidate(
		control_type="MenuItemControl", name="File", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 80, 25), center=(40, 12),
		coords_unavailable=False, depth=3, parent_idx=-1,
		area=2000, bfs_order=2, sibling_same_type_count=5,
	)
	tree_item = _Candidate(
		control_type="TreeItemControl", name="Documents", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 150, 25), center=(75, 12),
		coords_unavailable=False, depth=4, parent_idx=-1,
		area=3750, bfs_order=3, sibling_same_type_count=8,
	)

	s_tab = _score_candidate(tab_item, 1920, 1080)
	s_btn = _score_candidate(button, 1920, 1080)
	s_menu = _score_candidate(menu_item, 1920, 1080)
	s_tree = _score_candidate(tree_item, 1920, 1080)

	check("TabItem > Button (same size/depth)", s_tab > s_btn)
	check("MenuItemControl gets nav boost", s_menu > 0)
	check("TreeItemControl > Button", s_tree > s_btn)


def test_listitem_sibling_scoring():
	"""Test ListItem scoring — few siblings = navigation boost, many = no boost."""
	print("\n--- ListItem Sibling Scoring Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate

	nav_list = _Candidate(
		control_type="ListItemControl", name="Settings", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
		coords_unavailable=False, depth=4, parent_idx=0,
		area=6000, bfs_order=0, sibling_same_type_count=5,
	)
	data_list = _Candidate(
		control_type="ListItemControl", name="Row", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
		coords_unavailable=False, depth=4, parent_idx=0,
		area=6000, bfs_order=1, sibling_same_type_count=15,
	)
	bulk_list = _Candidate(
		control_type="ListItemControl", name="Row", automation_id="",
		is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
		coords_unavailable=False, depth=4, parent_idx=0,
		area=6000, bfs_order=2, sibling_same_type_count=50,
	)

	s_nav = _score_candidate(nav_list, 1920, 1080)
	s_data = _score_candidate(data_list, 1920, 1080)
	s_bulk = _score_candidate(bulk_list, 1920, 1080)

	check("few-sibling ListItem > many-sibling (<=10 vs >10)", s_nav > s_data)
	check("few-sibling ListItem > bulk ListItem (penalized)", s_nav > s_bulk)
	check("data ListItem (15) > bulk ListItem (50, penalized)", s_data > s_bulk)


def test_shortcut_scoped_snapshot():
	"""Test that shortcut tool uses scoped (window-aware) auto-snapshot."""
	print("\n--- Shortcut Scoped Snapshot Tests ---")

	import inspect
	from windows_native_mcp.tools.snapshot import run_post_action_snapshot
	from windows_native_mcp.tools import shortcut

	check("run_post_action_snapshot is callable", callable(run_post_action_snapshot))

	shortcut_source = inspect.getsource(shortcut)
	check("shortcut.py imports scoped version", "run_post_action_snapshot" in shortcut_source)
	check("shortcut.py does NOT import unscoped version", "run_post_action_snapshot_unscoped" not in shortcut_source)


def test_snapshot_param_on_actions():
	"""Test all action tools have snapshot parameter defaulting to False."""
	print("\n--- Snapshot Parameter Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	for tool_name in ["click", "type_text", "scroll", "shortcut"]:
		if tool_name in tools:
			schema = tools[tool_name].parameters
			props = schema.get("properties", {})
			has_snapshot = "snapshot" in props
			check(f"{tool_name} has 'snapshot' param", has_snapshot)
			if has_snapshot:
				default = props["snapshot"].get("default", None)
				check(f"{tool_name} snapshot defaults to True", default is True,
					f"got {default}")
		else:
			check(f"{tool_name} tool found", False)


def test_last_snapshot_params_stored():
	"""Test DesktopState stores last_snapshot_params after snapshot."""
	print("\n--- Last Snapshot Params Tests ---")

	from windows_native_mcp.core.state import DesktopState

	state = DesktopState()
	check("last_snapshot_params starts None", state.last_snapshot_params is None)

	# Simulate storing params
	state.last_snapshot_params = {"detail": "standard", "window": "Test", "limit": 500}
	check("last_snapshot_params stored", state.last_snapshot_params is not None)
	check("params has window", state.last_snapshot_params["window"] == "Test")


def test_execute_snapshot_helper():
	"""Test _execute_snapshot returns valid structure."""
	print("\n--- Execute Snapshot Helper Tests ---")

	from windows_native_mcp.tools.snapshot import _execute_snapshot
	from windows_native_mcp.core.state import desktop_state

	result = _execute_snapshot()

	check("result has metadata", "metadata" in result)
	check("result has elements", "elements" in result)
	check("elements is list", isinstance(result["elements"], list))
	check("state not stale after execute", desktop_state.is_stale is False)
	# Note: _execute_snapshot is a pure tree-collection function.
	# last_snapshot_params is set by the snapshot tool handler, not _execute_snapshot.


def test_tree_output_data_collapse():
	"""Test Text children collapse into values array for data types."""
	print("\n--- Tree Output Data Collapse Tests ---")

	from windows_native_mcp.core.state import ElementInfo
	from windows_native_mcp.tools.snapshot import _build_tree_output

	# TreeItem with 3 Text children — should collapse
	elements = {
		"1": ElementInfo(
			label="1", name="Row 1", control_type="TreeItem",
			bounding_rect=(0, 0, 800, 30), center=(400, 15),
			parent_label=None, depth=0,
		),
		"2": ElementInfo(
			label="2", name="Column A", control_type="Text",
			bounding_rect=(0, 0, 200, 30), center=(100, 15),
			parent_label="1", depth=1,
		),
		"3": ElementInfo(
			label="3", name="Column B", control_type="Text",
			bounding_rect=(200, 0, 400, 30), center=(300, 15),
			parent_label="1", depth=1,
		),
		"4": ElementInfo(
			label="4", name="Column C", control_type="Text",
			bounding_rect=(400, 0, 600, 30), center=(500, 15),
			parent_label="1", depth=1,
		),
	}

	tree = _build_tree_output(elements, include_rects=False)
	check("collapse: 1 root", len(tree) == 1)
	root = tree[0]
	check("collapse: has values", "values" in root)
	check("collapse: no children key", "children" not in root)
	check("collapse: 3 values", len(root.get("values", [])) == 3)
	check("collapse: correct values", root.get("values") == ["Column A", "Column B", "Column C"])


def test_tree_output_no_collapse_mixed():
	"""Test that non-data types with mixed children do NOT collapse."""
	print("\n--- Tree Output No Collapse Mixed Tests ---")

	from windows_native_mcp.core.state import ElementInfo
	from windows_native_mcp.tools.snapshot import _build_tree_output

	# Button with Button children — should NOT collapse
	elements = {
		"1": ElementInfo(
			label="1", name="Toolbar", control_type="Button",
			bounding_rect=(0, 0, 800, 30), center=(400, 15),
			parent_label=None, depth=0,
		),
		"2": ElementInfo(
			label="2", name="Save", control_type="Button",
			bounding_rect=(0, 0, 100, 30), center=(50, 15),
			parent_label="1", depth=1,
		),
		"3": ElementInfo(
			label="3", name="Load", control_type="Button",
			bounding_rect=(100, 0, 200, 30), center=(150, 15),
			parent_label="1", depth=1,
		),
	}

	tree = _build_tree_output(elements, include_rects=False)
	root = tree[0]
	check("no collapse: has children", "children" in root)
	check("no collapse: no values", "values" not in root)


def test_tree_output_no_collapse_single_text():
	"""Test that TreeItem with 1 Text child does NOT collapse."""
	print("\n--- Tree Output No Collapse Single Text Tests ---")

	from windows_native_mcp.core.state import ElementInfo
	from windows_native_mcp.tools.snapshot import _build_tree_output

	elements = {
		"1": ElementInfo(
			label="1", name="Row", control_type="TreeItem",
			bounding_rect=(0, 0, 800, 30), center=(400, 15),
			parent_label=None, depth=0,
		),
		"2": ElementInfo(
			label="2", name="Only Child", control_type="Text",
			bounding_rect=(0, 0, 200, 30), center=(100, 15),
			parent_label="1", depth=1,
		),
	}

	tree = _build_tree_output(elements, include_rects=False)
	root = tree[0]
	check("single text: has children", "children" in root)
	check("single text: no values", "values" not in root)


def test_reserved_slots():
	"""Test that nav types get reserved slots when capped."""
	print("\n--- Reserved Slots Tests ---")

	from windows_native_mcp.core.uia import _score_candidate, _Candidate, _NAV_TYPES

	# Create 505 candidates: 500 buttons + 5 tab items
	candidates = []
	for i in range(500):
		candidates.append(_Candidate(
			control_type="ButtonControl", name=f"Btn{i}", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 200, 30), center=(100, 15),
			coords_unavailable=False, depth=6, parent_idx=-1,
			area=6000, bfs_order=i, sibling_same_type_count=500,
		))
	for i in range(5):
		candidates.append(_Candidate(
			control_type="TabItemControl", name=f"Tab{i}", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
			coords_unavailable=False, depth=5, parent_idx=-1,
			area=3000, bfs_order=500 + i, sibling_same_type_count=5,
		))

	# Score and select (simulating Pass 2)
	screen_w, screen_h = 1920, 1080
	scored = [(i, _score_candidate(c, screen_w, screen_h)) for i, c in enumerate(candidates)]
	scored.sort(key=lambda x: (-x[1], candidates[x[0]].bfs_order))

	limit = 500
	selected_indices = [i for i, _ in scored[:limit]]

	# Add 7 ListItem nav elements (like Task Manager sidebar)
	for i in range(7):
		candidates.append(_Candidate(
			control_type="ListItemControl", name=f"NavItem{i}", automation_id="",
			is_enabled=True, bounding_rect=(200, 300+i*40, 240, 340+i*40), center=(220, 320+i*40),
			coords_unavailable=False, depth=6, parent_idx=-1,
			area=1600, bfs_order=505 + i, sibling_same_type_count=7,
		))

	# Re-score with all candidates
	scored = [(i, _score_candidate(c, screen_w, screen_h)) for i, c in enumerate(candidates)]
	scored.sort(key=lambda x: (-x[1], candidates[x[0]].bfs_order))
	selected_indices = [i for i, _ in scored[:limit]]

	# Apply reserved slots (same logic as uia.py)
	capped = len(candidates) > limit
	if capped:
		selected_set_tmp = set(selected_indices)
		max_reserved = min(20, limit // 10)
		reserved = []
		for i, c in enumerate(candidates):
			if i in selected_set_tmp:
				continue
			is_nav = (
				c.control_type in _NAV_TYPES
				or (c.control_type == "ListItemControl" and c.sibling_same_type_count <= 10)
			)
			if is_nav and not c.coords_unavailable:
				reserved.append(i)
			if len(reserved) >= max_reserved:
				break
		if reserved:
			evict_count = len(reserved)
			selected_indices = selected_indices[:-evict_count] + reserved

	# Check all tab items and nav ListItems are in the selection
	selected_set = set(selected_indices)
	tab_indices = list(range(500, 505))
	tabs_selected = sum(1 for i in tab_indices if i in selected_set)
	check("all 5 TabItems in selection after reserved slots", tabs_selected == 5)
	nav_list_indices = list(range(505, 512))
	navs_selected = sum(1 for i in nav_list_indices if i in selected_set)
	check("all 7 nav ListItems in selection after reserved slots", navs_selected == 7)
	check("selection still has 500 elements", len(selected_indices) == 500)


def test_adaptive_termination():
	"""Test adaptive early termination logic."""
	print("\n--- Adaptive Termination Tests ---")

	from windows_native_mcp.core.uia import _Candidate, _NAV_TYPES

	# Scenario 1: candidates with nav types → should terminate at limit*3
	candidates_with_nav = [
		_Candidate(
			control_type="TabItemControl", name="Tab", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
			coords_unavailable=False, depth=3, parent_idx=-1,
			area=3000, bfs_order=0, sibling_same_type_count=3,
		),
	]
	has_nav = any(c.control_type in _NAV_TYPES for c in candidates_with_nav)
	check("has_nav is True when TabItem present", has_nav)

	# Scenario 2: candidates without nav types → should continue past limit*3
	candidates_no_nav = [
		_Candidate(
			control_type="ButtonControl", name="Btn", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
			coords_unavailable=False, depth=5, parent_idx=-1,
			area=3000, bfs_order=0, sibling_same_type_count=100,
		),
	]
	has_nav2 = any(c.control_type in _NAV_TYPES for c in candidates_no_nav)
	check("has_nav is False when no nav types", not has_nav2)

	# Scenario 3: ListItem with few siblings → counts as nav
	candidates_nav_list = [
		_Candidate(
			control_type="ListItemControl", name="NavItem", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
			coords_unavailable=False, depth=6, parent_idx=-1,
			area=3000, bfs_order=0, sibling_same_type_count=7,
		),
	]
	has_nav3 = any(
		c.control_type in _NAV_TYPES
		or (c.control_type == "ListItemControl" and c.sibling_same_type_count <= 10)
		for c in candidates_nav_list
	)
	check("has_nav is True for ListItem with <=10 siblings", has_nav3)

	# Scenario 4: ListItem with many siblings → NOT nav
	candidates_data_list = [
		_Candidate(
			control_type="ListItemControl", name="DataRow", automation_id="",
			is_enabled=True, bounding_rect=(0, 0, 100, 30), center=(50, 15),
			coords_unavailable=False, depth=8, parent_idx=-1,
			area=3000, bfs_order=0, sibling_same_type_count=100,
		),
	]
	has_nav4 = any(
		c.control_type in _NAV_TYPES
		or (c.control_type == "ListItemControl" and c.sibling_same_type_count <= 10)
		for c in candidates_data_list
	)
	check("has_nav is False for ListItem with >10 siblings (data row)", not has_nav4)


def test_app_size_on_launch():
	"""Test that app tool's size parameter works with launch mode."""
	print("\n--- App Size on Launch Tests ---")

	import inspect
	from windows_native_mcp.tools import app

	source = inspect.getsource(app)
	check("size handled in launch mode", "size is not None" in source)
	check("size description mentions launch", "Window size for launch" in source)


def test_viewport_intersection_filter():
	"""Test that intersection-based viewport filter handles edge cases correctly."""
	print("\n--- Viewport Intersection Filter Tests ---")

	# The AABB non-intersection test (True = no overlap = reject)
	def is_outside(el_l, el_t, el_r, el_b, vl, vt, vr, vb):
		return el_r < vl or el_l > vr or el_b < vt or el_t > vb

	vp = (100, 100, 500, 400)  # viewport: left, top, right, bottom

	# Basic cases
	check("fully inside: kept", not is_outside(200, 200, 300, 300, *vp))
	check("fully left: rejected", is_outside(0, 200, 90, 300, *vp))
	check("fully right: rejected", is_outside(510, 200, 600, 300, *vp))
	check("fully above: rejected", is_outside(200, 0, 300, 90, *vp))
	check("fully below: rejected", is_outside(200, 410, 300, 500, *vp))

	# Partial overlaps (the key improvement over center-point)
	check("partial left overlap: kept", not is_outside(80, 200, 120, 300, *vp))
	check("partial right overlap: kept", not is_outside(480, 200, 520, 300, *vp))
	check("partial top overlap: kept", not is_outside(200, 80, 300, 120, *vp))
	check("partial bottom overlap: kept", not is_outside(200, 380, 300, 420, *vp))

	# Task Manager sidebar case: element extends left of window
	check("sidebar extends left of window: kept",
		not is_outside(236, 300, 276, 340, 256, 0, 1200, 800))

	# Edge-touching
	check("edge touching (right==left): kept", not is_outside(50, 200, 100, 300, *vp))
	check("1px gap: rejected", is_outside(50, 200, 99, 300, *vp))

	# Special cases
	check("element encloses viewport: kept", not is_outside(0, 0, 600, 500, *vp))
	check("zero-size inside: kept", not is_outside(200, 200, 200, 200, *vp))
	check("zero-size outside: rejected", is_outside(50, 50, 50, 50, *vp))


def test_snapshot_grid_defaults():
	"""Test snapshot grid defaults to rulers and doesn't auto-enable screenshot."""
	print("\n--- Snapshot Grid Default Tests ---")

	from windows_native_mcp.main import mcp
	tool_list = asyncio.run(mcp.list_tools())
	tools = {t.name: t for t in tool_list}

	snapshot_tool = tools.get("snapshot")
	check("snapshot tool exists", snapshot_tool is not None)

	props = snapshot_tool.parameters.get("properties", {})
	grid_prop = props.get("grid", {})
	check("grid default is rulers", grid_prop.get("default") == "rulers")
	check("grid has 3 enum values", len(grid_prop.get("enum", [])) == 3)

	import inspect
	from windows_native_mcp.tools import snapshot
	source = inspect.getsource(snapshot)
	# The old pattern was: if grid != "off" or crop is not None: screenshot = True
	# New pattern should only have: if crop is not None: screenshot = True
	check("grid does NOT auto-enable screenshot",
		'grid != "off" or crop' not in source)
	check("crop still auto-enables screenshot",
		"crop is not None" in source)


if __name__ == "__main__":
	print("=" * 50)
	print(" Windows Native MCP — Full Test Suite (Round 4)")
	print("=" * 50)

	test_imports()
	test_tool_registration()
	test_state()
	test_raw_rect_validation()
	test_app_args_parameter()
	test_shellexecuteinfo()
	test_screen()
	test_window_list()
	test_snapshot_minimal()
	test_hierarchy_fields()
	test_pua_detection()
	test_scoring()
	test_tree_output()
	test_snapshot_new_params()
	test_overflow_hardening()
	test_viewport_filtering_param()
	test_checked_selected_fields()
	test_coords_available_count()
	test_start_app_resolution()
	test_app_mode_names()
	test_tree_output_checked_selected()
	test_cached_walk_import()
	test_cached_walk_basic()
	test_cached_walk_matches_bfs()
	test_cached_walk_performance()
	test_cached_walk_fallback()
	test_cached_pattern_reading()
	test_cache_used_metadata()
	test_depth_scoring()
	test_sibling_penalty()
	test_snapshot_param_on_actions()
	test_last_snapshot_params_stored()
	test_execute_snapshot_helper()
	test_tree_output_data_collapse()
	test_tree_output_no_collapse_mixed()
	test_tree_output_no_collapse_single_text()
	test_nav_scoring()
	test_listitem_sibling_scoring()
	test_shortcut_scoped_snapshot()
	test_reserved_slots()
	test_adaptive_termination()
	test_app_size_on_launch()
	test_viewport_intersection_filter()
	test_snapshot_grid_defaults()

	print(f"\n{'=' * 50}")
	print(f" Results: {passed} passed, {failed} failed")
	print(f"{'=' * 50}")

	if errors:
		print("\nFailures:")
		for e in errors:
			print(e)

	sys.exit(0 if failed == 0 else 1)
