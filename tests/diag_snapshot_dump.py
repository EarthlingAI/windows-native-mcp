"""Diagnostic: capture real snapshot data from Cursor window and dump to JSON.

Runs get_desktop_elements() in both "standard" and "full" detail modes,
saving complete element data for offline analysis.
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from windows_native_mcp.core.uia import _ensure_com, get_desktop_elements
from windows_native_mcp.core.screen import get_dpi_scale


TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".temp")
WINDOW_NAME = "Cursor"


def elements_to_serializable(elements, metadata):
	"""Convert elements dict + metadata to a JSON-serializable structure."""
	records = []
	for label, elem in elements.items():
		records.append({
			"label": elem.label,
			"name": elem.name,
			"control_type": elem.control_type,
			"bounding_rect": list(elem.bounding_rect),
			"center": list(elem.center),
			"automation_id": elem.automation_id,
			"is_enabled": elem.is_enabled,
			"coords_unavailable": elem.coords_unavailable,
		})
	return {
		"metadata": metadata,
		"elements": records,
	}


def print_summary(elements, metadata, detail_mode):
	"""Print summary stats for a snapshot."""
	print(f"\n{'=' * 60}")
	print(f"  {detail_mode.upper()} mode — {WINDOW_NAME}")
	print(f"{'=' * 60}")
	print(f"Total elements:       {metadata['element_count']}")
	print(f"Coords unavailable:   {metadata['coords_unavailable_count']}")
	print(f"Ghost filtered:       {metadata['ghost_filtered_count']}")
	print(f"Elapsed:              {metadata['elapsed_seconds']}s")
	if "capped_at" in metadata:
		print(f"CAPPED at:            {metadata['capped_at']}")

	# Type distribution
	type_counts = Counter()
	empty_name_count = 0
	for elem in elements.values():
		type_counts[elem.control_type] += 1
		if not elem.name.strip():
			empty_name_count += 1

	print(f"Empty names:          {empty_name_count}")
	print(f"\nType distribution:")
	for ctrl_type, count in type_counts.most_common():
		print(f"  {ctrl_type:<30s} {count}")


def main():
	os.makedirs(TEMP_DIR, exist_ok=True)
	_ensure_com()
	scale = get_dpi_scale()
	print(f"DPI scale factor: {scale}")

	# Standard mode
	print(f"\nCollecting STANDARD elements for '{WINDOW_NAME}'...")
	try:
		elements_std, meta_std = get_desktop_elements(
			detail="standard", window_name=WINDOW_NAME, scale_factor=scale,
		)
		print_summary(elements_std, meta_std, "standard")
		out_path = os.path.join(TEMP_DIR, "diag_cursor_standard.json")
		with open(out_path, "w", encoding="utf-8") as f:
			json.dump(elements_to_serializable(elements_std, meta_std), f, indent=2)
		print(f"\nSaved to: {out_path}")
	except Exception as e:
		print(f"ERROR (standard): {e}")

	# Full mode
	print(f"\nCollecting FULL elements for '{WINDOW_NAME}'...")
	try:
		elements_full, meta_full = get_desktop_elements(
			detail="full", window_name=WINDOW_NAME, scale_factor=scale,
		)
		print_summary(elements_full, meta_full, "full")
		out_path = os.path.join(TEMP_DIR, "diag_cursor_full.json")
		with open(out_path, "w", encoding="utf-8") as f:
			json.dump(elements_to_serializable(elements_full, meta_full), f, indent=2)
		print(f"\nSaved to: {out_path}")
	except Exception as e:
		print(f"ERROR (full): {e}")


if __name__ == "__main__":
	main()
