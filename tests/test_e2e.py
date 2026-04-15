"""End-to-end tests for windows-native-mcp enhancements.

Tests: monitor enumeration, cursor compositing, auto-snapshot replay,
multi-monitor capture, SendInput virtual desktop mapping, delay param,
error handling, and monitor metadata.
"""
import json
import time

from windows_native_mcp.main import mcp, register_tools
register_tools()

from windows_native_mcp.tools.snapshot import (
	_execute_snapshot, _capture_annotated_screenshot,
	run_post_action_snapshot, run_post_action_snapshot_unscoped, _resolve_monitor
)
from windows_native_mcp.core.screen import (
	enumerate_monitors, capture_screenshot, get_virtual_screen_rect,
	get_monitor_by_index, MonitorInfo
)
from windows_native_mcp.core.state import desktop_state
from windows_native_mcp.core.input import _get_virtual_screen, _to_absolute
from fastmcp.exceptions import ToolError

tests_passed = 0
tests_failed = 0


def test(name, condition, detail=""):
	global tests_passed, tests_failed
	if condition:
		tests_passed += 1
		print(f"  PASS: {name}")
	else:
		tests_failed += 1
		print(f"  FAIL: {name} -- {detail}")


def main():
	global tests_passed, tests_failed

	print("=" * 60)
	print("WINDOWS-NATIVE-MCP E2E TEST SUITE")
	print("=" * 60)

	# ===== TEST 1: Monitor Enumeration =====
	print("\n--- Test 1: Monitor Enumeration ---")
	monitors = enumerate_monitors()
	test("Found monitors", len(monitors) >= 1, f"got {len(monitors)}")
	test("Primary monitor exists", any(m.primary for m in monitors))
	primary = [m for m in monitors if m.primary][0]
	test("Primary is index 1", primary.index == 1)
	test("Primary has valid dimensions", primary.width > 0 and primary.height > 0,
		f"{primary.width}x{primary.height}")
	test("Primary DPI > 0", primary.dpi_scale > 0, f"{primary.dpi_scale}")
	has_secondary = len(monitors) > 1
	if has_secondary:
		secondary = monitors[1]
		test("Secondary detected", True, f"{secondary.width}x{secondary.height} @ {secondary.dpi_scale}x")
		test("Secondary rect non-zero", secondary.rect != (0, 0, 0, 0), f"{secondary.rect}")
	else:
		secondary = None

	# ===== TEST 2: Screenshot Capture + Cursor =====
	print("\n--- Test 2: Screenshot Capture + Cursor ---")
	img_primary = capture_screenshot(primary)
	test("Primary screenshot captured", img_primary is not None)
	test("Primary image is RGB", img_primary.mode == "RGB", img_primary.mode)
	expected_w = int(primary.width * primary.dpi_scale)
	expected_h = int(primary.height * primary.dpi_scale)
	test("Primary dimensions match", img_primary.size == (expected_w, expected_h),
		f"got {img_primary.size}, expected ({expected_w},{expected_h})")

	if has_secondary:
		img_secondary = capture_screenshot(secondary)
		test("Secondary screenshot captured", img_secondary is not None)
		exp_sw = int(secondary.width * secondary.dpi_scale)
		exp_sh = int(secondary.height * secondary.dpi_scale)
		test("Secondary dimensions match", img_secondary.size == (exp_sw, exp_sh),
			f"got {img_secondary.size}, expected ({exp_sw},{exp_sh})")

	img_all = capture_screenshot(None)
	test("Virtual desktop captured", img_all is not None)
	test("Virtual desktop wider than primary", img_all.width > img_primary.width,
		f"{img_all.width} vs {img_primary.width}")

	# ===== TEST 3: Basic Snapshot (tree-only) =====
	print("\n--- Test 3: Basic Snapshot (tree-only) ---")
	result = _execute_snapshot(detail="standard", limit=50)
	meta = result["metadata"]
	test("Snapshot returns elements", meta["element_count"] > 0, f"{meta['element_count']}")
	test("Scale factor set", desktop_state.scale_factor > 0, f"{desktop_state.scale_factor}")
	test("State not stale", not desktop_state.is_stale)
	test("last_snapshot_params is None", desktop_state.last_snapshot_params is None,
		"should only be set by tool handler")

	# ===== TEST 4: Auto-Snapshot Full Replay =====
	print("\n--- Test 4: Auto-Snapshot Full Replay ---")
	desktop_state.monitors = monitors
	desktop_state.active_monitor = primary
	result = _execute_snapshot(detail="standard", limit=50)
	desktop_state.last_snapshot_params = {
		"detail": "standard", "window": None, "limit": 50,
		"types": None, "viewport_only": True, "include_rects": False,
		"screenshot": True, "grid": "rulers", "grid_interval": "auto",
		"crop": None, "monitor": None,
	}

	auto = run_post_action_snapshot(delay=0.01)
	test("Auto-snapshot returns dict (always)", isinstance(auto, dict), type(auto).__name__)
	test("Has screenshot_path", "screenshot_path" in auto.get("metadata", {}))
	test("Has elements", len(auto.get("elements", [])) > 0)

	# Switch to tree-only
	desktop_state.last_snapshot_params["screenshot"] = False
	auto2 = run_post_action_snapshot(delay=0.01)
	test("After screenshot=False, returns dict", isinstance(auto2, dict), type(auto2).__name__)
	test("No screenshot_path when screenshot=False", "screenshot_path" not in auto2.get("metadata", {}))

	# ===== TEST 5: Unscoped Replay (retained function) =====
	print("\n--- Test 5: Unscoped Replay (retained function) ---")
	desktop_state.last_snapshot_params["screenshot"] = True
	desktop_state.last_snapshot_params["window"] = "SomeWindow"
	unscoped = run_post_action_snapshot_unscoped(delay=0.01)
	test("Unscoped returns dict (always)", isinstance(unscoped, dict))
	test("Unscoped has screenshot_path", "screenshot_path" in unscoped.get("metadata", {}))

	# ===== TEST 6: Multi-Monitor Snapshot =====
	print("\n--- Test 6: Multi-Monitor Snapshot ---")
	if has_secondary:
		desktop_state.last_snapshot_params = {
			"detail": "standard", "window": None, "limit": 50,
			"types": None, "viewport_only": True, "include_rects": False,
			"screenshot": True, "grid": "rulers", "grid_interval": "auto",
			"crop": None, "monitor": 2,
		}
		desktop_state.active_monitor = secondary
		result2 = _execute_snapshot(
			detail="standard", limit=50,
			scale_factor=secondary.dpi_scale,
			screen_size=(secondary.width, secondary.height),
			screen_origin=(secondary.rect[0], secondary.rect[1]),
		)
		test("Secondary snapshot elements", result2["metadata"]["element_count"] >= 0)
		test("Scale set to secondary DPI", desktop_state.scale_factor == secondary.dpi_scale,
			f"{desktop_state.scale_factor}")

		png, path = _capture_annotated_screenshot(
			metadata=result2["metadata"], grid="rulers",
			monitor_info=secondary,
		)
		test("Secondary screenshot bytes > 0", len(png) > 0, f"{len(png)} bytes")

		auto_m2 = run_post_action_snapshot(delay=0.01)
		test("Auto-snapshot monitor 2 returns dict", isinstance(auto_m2, dict))
		test("Auto-snapshot monitor 2 has screenshot_path", "screenshot_path" in auto_m2.get("metadata", {}))
	else:
		print("  SKIP: Single monitor setup")

	# ===== TEST 7: SendInput Virtual Desktop =====
	print("\n--- Test 7: SendInput Virtual Desktop Mapping ---")
	vs = _get_virtual_screen()
	test("Virtual screen width > 0", vs[2] > 0, f"{vs}")
	if has_secondary:
		test("Virtual screen has negative left", vs[0] < 0, f"left={vs[0]}")

	ax, ay = _to_absolute(960, 600, 1.25)
	test("Primary center in range", 0 <= ax <= 65535 and 0 <= ay <= 65535, f"({ax},{ay})")

	if has_secondary:
		sx, sy = _to_absolute(-1920, 660, 1.0)
		test("Secondary center in range", 0 <= sx <= 65535 and 0 <= sy <= 65535, f"({sx},{sy})")
		test("Secondary X < primary X", sx < ax, f"{sx} vs {ax}")

	# ===== TEST 8: Delay Parameter =====
	print("\n--- Test 8: Configurable Delay ---")
	desktop_state.last_snapshot_params = {
		"detail": "standard", "window": None, "limit": 20,
		"types": None, "viewport_only": True, "include_rects": False,
		"screenshot": False, "grid": "rulers", "grid_interval": "auto",
		"crop": None, "monitor": None,
	}
	desktop_state.active_monitor = primary

	# Warm up the tree walk (first call may be slow due to COM init)
	run_post_action_snapshot(delay=0)

	start = time.perf_counter()
	run_post_action_snapshot(delay=0)
	elapsed_zero = time.perf_counter() - start

	start = time.perf_counter()
	run_post_action_snapshot(delay=0.5)
	elapsed_half = time.perf_counter() - start

	test("delay=0 completes", elapsed_zero < 15.0, f"{elapsed_zero:.2f}s")
	test("delay=0.5 adds measurable time vs delay=0",
		elapsed_half > elapsed_zero + 0.3,
		f"delay=0.5: {elapsed_half:.2f}s vs delay=0: {elapsed_zero:.2f}s")

	# ===== TEST 9: Error Handling =====
	print("\n--- Test 9: Error Handling ---")
	try:
		get_monitor_by_index(99, monitors)
		test("Invalid monitor raises error", False, "no exception")
	except ToolError as e:
		test("Invalid monitor raises ToolError", True)
		test("Error mentions available monitors", "1" in str(e), str(e))

	# ===== TEST 10: Monitor Metadata =====
	print("\n--- Test 10: Monitor Metadata ---")
	desktop_state.monitors = monitors
	meta_list = [{"index": m.index, "rect": list(m.rect), "primary": m.primary} for m in monitors]
	test("Monitor metadata serializable", json.dumps(meta_list) is not None)
	test("Primary flagged correctly", meta_list[0]["primary"] is True)
	if has_secondary:
		test("Secondary flagged correctly", meta_list[1]["primary"] is False)

	# ===== SUMMARY =====
	print("\n" + "=" * 60)
	total = tests_passed + tests_failed
	print(f"RESULTS: {tests_passed}/{total} passed, {tests_failed} failed")
	if tests_failed == 0:
		print("ALL TESTS PASSED!")
	else:
		print(f"WARNING: {tests_failed} test(s) failed")
	print("=" * 60)


if __name__ == "__main__":
	main()
