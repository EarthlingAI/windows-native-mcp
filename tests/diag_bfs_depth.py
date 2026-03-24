"""Diagnostic: BFS walk of Cursor window tracking depth per element.

Walks the entire UI tree (no cap) recording depth, type, name, class,
bounding rect, center, and parent class for every element. Prints
detailed depth-level statistics and boundary analysis around element 500.
"""
import json
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uiautomation

from windows_native_mcp.core.uia import (
	_ensure_com, find_window, _get_raw_rect, _safe_get_children,
)


TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".temp")
WINDOW_NAME = "Cursor"


def main():
	os.makedirs(TEMP_DIR, exist_ok=True)
	_ensure_com()

	print(f"Finding window '{WINDOW_NAME}'...")
	root = find_window(WINDOW_NAME)
	if root is None:
		print(f"ERROR: Window '{WINDOW_NAME}' not found.")
		return

	print("Starting full BFS walk (no cap)...")
	records = []
	# BFS queue entries: (control, depth, parent_class_name)
	queue: deque[tuple[uiautomation.Control, int, str]] = deque()
	for child in _safe_get_children(root):
		try:
			root_class = root.ClassName or ""
		except Exception:
			root_class = ""
		queue.append((child, 0, root_class))

	while queue:
		control, depth, parent_class = queue.popleft()

		try:
			ctrl_type = control.ControlTypeName or ""
			name = control.Name or ""
			class_name = control.ClassName or ""
			rect = _get_raw_rect(control)
		except Exception:
			continue

		left, top, right, bottom = rect
		center_x = (left + right) // 2 if rect != (0, 0, 0, 0) else 0
		center_y = (top + bottom) // 2 if rect != (0, 0, 0, 0) else 0

		records.append({
			"index": len(records),
			"depth": depth,
			"control_type": ctrl_type,
			"name": name[:50],
			"class_name": class_name,
			"bounding_rect": [left, top, right, bottom],
			"center": [center_x, center_y],
			"parent_class_name": parent_class,
		})

		# Enqueue children
		for child in _safe_get_children(control):
			queue.append((child, depth + 1, class_name))

	# Save full data
	out_path = os.path.join(TEMP_DIR, "diag_cursor_bfs_depth.json")
	with open(out_path, "w", encoding="utf-8") as f:
		json.dump(records, f, indent=2)
	print(f"Saved {len(records)} elements to: {out_path}")

	# --- Summary stats ---
	print(f"\n{'=' * 60}")
	print(f"  BFS DEPTH ANALYSIS — {WINDOW_NAME}")
	print(f"{'=' * 60}")
	print(f"Total elements found: {len(records)}")

	if not records:
		return

	max_depth = max(r["depth"] for r in records)
	print(f"Max depth reached:    {max_depth}")

	# Elements per depth level
	depth_counter = Counter(r["depth"] for r in records)
	print(f"\nElements per depth level:")
	for d in sorted(depth_counter.keys()):
		print(f"  Depth {d:>3d}: {depth_counter[d]} elements")

	# Type distribution at each depth (top 3)
	print(f"\nTop 3 types per depth level:")
	depth_type_counters: dict[int, Counter] = {}
	for r in records:
		d = r["depth"]
		if d not in depth_type_counters:
			depth_type_counters[d] = Counter()
		depth_type_counters[d][r["control_type"]] += 1

	for d in sorted(depth_type_counters.keys()):
		top3 = depth_type_counters[d].most_common(3)
		top3_str = ", ".join(f"{t}({c})" for t, c in top3)
		print(f"  Depth {d:>3d}: {top3_str}")

	# Boundary analysis: elements 490-510 (0-indexed: 489-509)
	print(f"\n{'=' * 60}")
	print(f"  BOUNDARY ANALYSIS — elements 490-510 (near 500 cap)")
	print(f"{'=' * 60}")
	start_idx = 489
	end_idx = min(510, len(records))
	if start_idx >= len(records):
		print(f"  Only {len(records)} elements — cap boundary not reached.")
	else:
		boundary = records[start_idx:end_idx]
		for r in boundary:
			print(
				f"  #{r['index']+1:>4d}  depth={r['depth']}  "
				f"{r['control_type']:<28s}  "
				f"name={r['name'][:30]!r}"
			)
		# Summary of boundary region
		boundary_depths = [r["depth"] for r in boundary]
		boundary_types = Counter(r["control_type"] for r in boundary)
		print(f"\n  Depth range at boundary: {min(boundary_depths)}-{max(boundary_depths)}")
		print(f"  Types at boundary: {dict(boundary_types.most_common())}")


if __name__ == "__main__":
	main()
