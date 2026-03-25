"""Diagnostic benchmark: CacheRequest walk vs BFS walk.

Finds a target editor window, runs each walk strategy 3 times,
and prints a comparison table with candidate counts, timing, and speedup.
Results saved to .temp/diag_cached_vs_bfs.json.
"""

import sys
import os
import time
import json
import logging
from unittest.mock import patch
from pathlib import Path

# Fix Windows console encoding for Unicode window titles
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from windows_native_mcp.core.uia import (
	get_window_list, find_window, _walk_and_rank, _resolve_root,
	INTERACTIVE_TYPES, _ensure_com,
)
from windows_native_mcp.core.cached_walk import collect_candidates
from windows_native_mcp.core.screen import get_dpi_scale

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

RUNS = 3
EDITOR_KEYWORDS = ["Cursor", "Visual Studio Code", "Code", "VS Code", "Notepad", "Sublime"]


def find_target_window() -> tuple[str, object] | None:
	"""Auto-detect an editor window, falling back to any sizable window."""
	_ensure_com()
	windows = get_window_list()
	if not windows:
		return None

	# Try editor keywords first
	for kw in EDITOR_KEYWORDS:
		for w in windows:
			title = w.get("title", "")
			if kw.lower() in title.lower() and not w.get("is_minimized", False):
				ctrl = find_window(title)
				if ctrl is not None:
					return title, ctrl

	# Fallback: largest non-minimized window
	best = None
	best_area = 0
	for w in windows:
		if w.get("is_minimized", False):
			continue
		r = w.get("rect", (0, 0, 0, 0))
		area = abs((r[2] - r[0]) * (r[3] - r[1]))
		if area > best_area:
			best_area = area
			best = w
	if best:
		ctrl = find_window(best["title"])
		if ctrl is not None:
			return best["title"], ctrl

	return None


def run_cached_walk(root, scale_factor, viewport_rect):
	"""Run collect_candidates and return (candidate_count, elapsed_seconds)."""
	t0 = time.perf_counter()
	result = collect_candidates(
		root,
		interactive_types=INTERACTIVE_TYPES,
		viewport_rect=viewport_rect,
		scale_factor=scale_factor,
		max_depth=50,
		max_candidates=1500,
	)
	elapsed = time.perf_counter() - t0
	count = len(result[0]) if result is not None else 0
	return count, elapsed


def run_bfs_walk(root, scale_factor, viewport_rect):
	"""Run _walk_and_rank with cached path disabled (forces BFS)."""
	# Patch the cached_walk import inside _walk_and_rank to force BFS fallback
	def _fail_cached(*args, **kwargs):
		raise ImportError("Forced BFS for benchmark")

	t0 = time.perf_counter()
	with patch("windows_native_mcp.core.cached_walk.collect_candidates", side_effect=_fail_cached):
		elements, ghost, coords_na, was_capped, total, viewport_filt, cache_used = _walk_and_rank(
			root,
			scale_factor=scale_factor,
			limit=500,
			type_filter=None,
			max_depth=50,
			viewport_rect=viewport_rect,
		)
	elapsed = time.perf_counter() - t0
	return len(elements), elapsed, cache_used


def main():
	_ensure_com()
	scale_factor = get_dpi_scale()

	print("=" * 70)
	print("  Diagnostic: CacheRequest walk vs BFS walk")
	print("=" * 70)
	print(f"  DPI scale factor: {scale_factor}")
	print()

	target = find_target_window()
	if target is None:
		print("ERROR: No suitable window found. Open an editor or any application.")
		sys.exit(1)

	title, root = target
	print(f"  Target window: {title}")

	# Get viewport rect from the window's bounding rect
	try:
		rect = root.BoundingRectangle
		viewport_rect = (rect.left, rect.top, rect.right, rect.bottom)
	except Exception:
		viewport_rect = None
	print(f"  Viewport rect: {viewport_rect}")
	print()

	# --- Cached walk benchmark ---
	print("Running cached walk...")
	cached_counts = []
	cached_times = []
	for i in range(RUNS):
		count, elapsed = run_cached_walk(root, scale_factor, viewport_rect)
		cached_counts.append(count)
		cached_times.append(elapsed)
		print(f"  Run {i + 1}: {count:>5} candidates in {elapsed:.4f}s")

	# --- BFS walk benchmark ---
	print()
	print("Running BFS walk (cached path disabled)...")
	bfs_counts = []
	bfs_times = []
	bfs_cache_flags = []
	for i in range(RUNS):
		count, elapsed, cache_used = run_bfs_walk(root, scale_factor, viewport_rect)
		bfs_counts.append(count)
		bfs_times.append(elapsed)
		bfs_cache_flags.append(cache_used)
		print(f"  Run {i + 1}: {count:>5} candidates in {elapsed:.4f}s (cache_used={cache_used})")

	# --- Results ---
	avg_cached_time = sum(cached_times) / len(cached_times)
	avg_bfs_time = sum(bfs_times) / len(bfs_times)
	avg_cached_count = sum(cached_counts) / len(cached_counts)
	avg_bfs_count = sum(bfs_counts) / len(bfs_counts)
	speedup = avg_bfs_time / avg_cached_time if avg_cached_time > 0 else float("inf")

	print()
	print("=" * 70)
	print(f"  {'Metric':<30} {'Cached Walk':>15} {'BFS Walk':>15}")
	print("-" * 70)
	print(f"  {'Avg candidates':<30} {avg_cached_count:>15.1f} {avg_bfs_count:>15.1f}")
	print(f"  {'Avg time (s)':<30} {avg_cached_time:>15.4f} {avg_bfs_time:>15.4f}")
	print(f"  {'Min time (s)':<30} {min(cached_times):>15.4f} {min(bfs_times):>15.4f}")
	print(f"  {'Max time (s)':<30} {max(cached_times):>15.4f} {max(bfs_times):>15.4f}")
	print("-" * 70)
	print(f"  {'Speedup (BFS / Cached)':<30} {speedup:>15.2f}x")
	print("=" * 70)

	# --- Save results ---
	results = {
		"target_window": title,
		"dpi_scale": scale_factor,
		"viewport_rect": viewport_rect,
		"runs": RUNS,
		"cached_walk": {
			"counts": cached_counts,
			"times_s": [round(t, 6) for t in cached_times],
			"avg_count": round(avg_cached_count, 1),
			"avg_time_s": round(avg_cached_time, 6),
		},
		"bfs_walk": {
			"counts": bfs_counts,
			"times_s": [round(t, 6) for t in bfs_times],
			"avg_count": round(avg_bfs_count, 1),
			"avg_time_s": round(avg_bfs_time, 6),
			"cache_used_flags": bfs_cache_flags,
		},
		"speedup": round(speedup, 2),
	}

	out_dir = Path(__file__).resolve().parent.parent / ".temp"
	out_dir.mkdir(exist_ok=True)
	out_path = out_dir / "diag_cached_vs_bfs.json"
	with open(out_path, "w") as f:
		json.dump(results, f, indent="\t")

	print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
	main()
