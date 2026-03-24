#!/usr/bin/env python3
"""Gate 1 tests for windows-native-mcp.

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


if __name__ == "__main__":
	print("=" * 50)
	print(" Windows Native MCP — Gate 1 Tests")
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

	print(f"\n{'=' * 50}")
	print(f" Results: {passed} passed, {failed} failed")
	print(f"{'=' * 50}")

	if errors:
		print("\nFailures:")
		for e in errors:
			print(e)

	sys.exit(0 if failed == 0 else 1)
