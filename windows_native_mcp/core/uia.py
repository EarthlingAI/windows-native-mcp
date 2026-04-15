"""UI Automation wrapper — element discovery, window lookup, tree walking.

Wraps the `uiautomation` package to provide structured element discovery
for the Windows Desktop MCP server. All coordinates are in logical pixels.
"""
import ctypes
import ctypes.wintypes
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import uiautomation

from windows_native_mcp.core.state import ElementInfo

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

# Prevent COM hangs on unresponsive elements
uiautomation.SetGlobalSearchTimeout(10)

# Disable the @AutomationLog.txt file that uiautomation creates in CWD
uiautomation.Logger.SetLogFile("")

# Set DPI awareness so BoundingRectangle returns logical pixels
try:
	ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
	try:
		ctypes.windll.user32.SetProcessDPIAware()
	except (AttributeError, OSError):
		pass

# 64-bit pointer safety for IsIconic
ctypes.windll.user32.IsIconic.argtypes = [ctypes.c_void_p]
ctypes.windll.user32.IsIconic.restype = ctypes.c_int

# Track which threads have COM initialized
_com_initialized: set[int] = set()
_com_lock = threading.Lock()


def _ensure_com():
	"""Ensure COM is initialized for the current thread.

	FastMCP runs tool functions in a thread pool. Each thread needs
	COM initialized before making UIA (COM-based) calls.
	"""
	tid = threading.current_thread().ident
	if tid in _com_initialized:
		return
	with _com_lock:
		if tid in _com_initialized:
			return
		try:
			ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
		except OSError:
			pass  # Already initialized — fine
		_com_initialized.add(tid)

# Control types to collect in "standard" detail mode
INTERACTIVE_TYPES = {
	"ButtonControl",
	"EditControl",
	"ComboBoxControl",
	"CheckBoxControl",
	"RadioButtonControl",
	"ListItemControl",
	"MenuItemControl",
	"TabItemControl",
	"HyperlinkControl",
	"SliderControl",
	"SpinnerControl",
	"TreeItemControl",
	"DataItemControl",
	"HeaderItemControl",
	"ScrollBarControl",
	"ToolBarControl",
	"MenuBarControl",
	"TextControl",
}

_MAX_COORD = 65536


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_desktop_elements(
	detail: str = "standard",
	window_name: str | None = None,
	scale_factor: float = 1.0,
	limit: int = 500,
	type_filter: set[str] | None = None,
	screen_size: tuple[int, int] = (1920, 1080),
	viewport_only: bool = True,
	screen_origin: tuple[int, int] = (0, 0),
) -> tuple[dict[str, ElementInfo], dict]:
	"""Walk the UI tree and return discovered elements with metadata.

	Args:
		detail: Discovery depth — "minimal", "standard", or "full".
		window_name: Scope to a specific window (exact then substring match).
		scale_factor: DPI scale factor for coordinate conversion.
		limit: Max elements for standard mode (scored ranking).
		type_filter: Override interactive types for standard mode.
		screen_size: Screen dimensions for scoring offscreen elements.
		screen_origin: Top-left of active monitor in logical pixels (for scoring).

	Returns:
		Tuple of (elements_dict keyed by sequential label, metadata_dict).
	"""
	_ensure_com()
	start = time.perf_counter()
	elements: dict[str, ElementInfo] = {}
	ghost_filtered = 0
	coords_unavailable = 0
	viewport_filtered = 0
	capped = False
	total_candidates = 0

	# Determine root control to walk
	cache_used = False

	root = _resolve_root(window_name)
	if root is None:
		elapsed = time.perf_counter() - start
		logging.info(f"UIA: No root control found ({elapsed:.2f}s)")
		return {}, _build_metadata(
			0, detail, window_name, 0, 0, capped=False, elapsed=elapsed,
		)

	# Capture window handle for scoped snapshots (used for auto-foreground)
	window_handle = None
	window_minimized = False
	if window_name:
		window_handle = _get_native_handle(root)
		if window_handle:
			window_minimized = _is_window_minimized(window_handle)

	if detail == "minimal":
		elements, ghost_filtered, coords_unavailable = _collect_minimal(
			root, window_name, scale_factor,
		)
	elif detail == "full":
		elements, ghost_filtered, coords_unavailable = _walk_tree(
			root, scale_factor, filter_interactive=False, max_elements=0,
		)
	else:
		# standard (default)
		viewport_rect = None
		if viewport_only and root is not None:
			try:
				vr = root.BoundingRectangle
				viewport_rect = (vr.left, vr.top, vr.right, vr.bottom)
			except Exception:
				pass  # Fall back to no viewport filtering
		try:
			elements, ghost_filtered, coords_unavailable, capped, total_candidates, viewport_filtered, cache_used = _walk_and_rank(
				root, scale_factor, limit=limit, type_filter=type_filter,
				screen_size=screen_size, viewport_rect=viewport_rect,
				screen_origin=screen_origin,
			)
		except OverflowError:
			logging.warning("UIA: OverflowError during standard walk — returning partial results")

	elapsed = time.perf_counter() - start
	logging.info(
		f"UIA: Collected {len(elements)} elements "
		f"(detail={detail}, ghosts={ghost_filtered}, "
		f"coords_unavail={coords_unavailable}, {elapsed:.2f}s)"
	)

	scoring = detail == "standard"
	metadata = _build_metadata(
		len(elements), detail, window_name,
		coords_unavailable, ghost_filtered,
		capped=capped, elapsed=elapsed,
		limit=limit, total_candidates=total_candidates,
		scoring=scoring,
		viewport_filtered_count=viewport_filtered,
		cache_used=cache_used,
		window_handle=window_handle,
		window_minimized=window_minimized,
	)
	return elements, metadata


def find_window(name: str) -> uiautomation.WindowControl | None:
	"""Find a top-level window by title. Exact match first, then substring.

	Args:
		name: Window title to search for.

	Returns:
		The matching WindowControl, or None if not found.
	"""
	_ensure_com()
	# Exact match
	try:
		win = uiautomation.WindowControl(searchDepth=1, Name=name)
		if win.Exists(maxSearchSeconds=2):
			return win
	except Exception:
		pass

	# Substring match — iterate top-level windows
	try:
		root = uiautomation.GetRootControl()
		if root is None:
			return None
		for child in root.GetChildren():
			try:
				title = child.Name or ""
				if name.lower() in title.lower() and title:
					ctrl_type = child.ControlTypeName
					if ctrl_type == "WindowControl":
						return child
			except Exception:
				continue
	except Exception:
		logging.warning("UIA: Failed to iterate top-level windows for substring match")

	return None


def get_window_list() -> list[dict]:
	"""Return all visible top-level windows with metadata.

	Returns:
		List of dicts with keys: handle, title, rect, pid, is_minimized.
	"""
	_ensure_com()
	windows: list[dict] = []

	try:
		root = uiautomation.GetRootControl()
		if root is None:
			logging.warning("UIA: Cannot get root control for window list")
			return windows
	except Exception:
		logging.warning("UIA: Exception getting root control for window list")
		return windows

	for child in _safe_get_children(root):
		try:
			ctrl_type = child.ControlTypeName
			if ctrl_type != "WindowControl":
				continue

			title = child.Name or ""
			# Skip empty-title windows (system chrome, ghost windows)
			if not title.strip():
				continue

			handle = _get_native_handle(child)
			rect = _get_bounding_dict(child)
			pid = _get_process_id(child)
			is_minimized = _is_window_minimized(handle) if handle else False

			# Skip windows with zero-size rects that aren't minimized
			if not is_minimized and rect["left"] == 0 and rect["top"] == 0 \
				and rect["right"] == 0 and rect["bottom"] == 0:
				continue

			windows.append({
				"handle": handle,
				"title": title,
				"rect": rect,
				"pid": pid,
				"is_minimized": is_minimized,
			})
		except Exception:
			continue

	return windows


# ---------------------------------------------------------------------------
# Tree walking internals
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
	"""Pre-label element data from Pass 1 BFS."""
	control_type: str          # Full name for matching (e.g. "ButtonControl")
	name: str
	automation_id: str
	is_enabled: bool
	bounding_rect: tuple[int, int, int, int]
	center: tuple[int, int]
	coords_unavailable: bool
	depth: int
	parent_idx: int            # Index of nearest interactive ancestor in candidates list (-1 = root)
	area: int                  # (right-left) * (bottom-top)
	bfs_order: int             # Original BFS position (for stable sort tiebreaker)
	checked: bool | None = None    # True/False for checkboxes/toggles, None if N/A
	selected: bool | None = None   # True/False for radio buttons, list items, tab items
	sibling_same_type_count: int = 0  # Count of same-type siblings under same parent


_CONTAINER_TYPES_FULL = {"ToolBarControl", "MenuBarControl", "ScrollBarControl"}

_NAV_TYPES = {"TabItemControl", "MenuItemControl", "TreeItemControl"}

# Control types that need class_name/rect for ghost or tab filtering
_NEEDS_FILTER_CHECK = {"PaneControl", "WindowControl", "CustomControl", "GroupControl"}


def _is_pua_only(name: str) -> bool:
	"""Check if a string contains only Private Use Area Unicode characters."""
	stripped = name.strip()
	if not stripped:
		return False
	return all(0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0x10FFFF for c in stripped)


def _score_candidate(c: _Candidate, screen_w: int, screen_h: int, screen_origin: tuple[int, int] = (0, 0)) -> float:
	"""Score a candidate element for ranking. Higher = more important."""
	# Area (log scale so giant text areas don't dominate)
	score = math.log2(max(c.area, 1) + 1) * 10  # ~200 for 9.4M, ~100 for 1000, ~0 for 0

	# Name quality
	if c.name.strip():
		if _is_pua_only(c.name):
			score -= 50
		else:
			score += 30

	# Empty-name container penalty
	if not c.name.strip() and c.control_type in _CONTAINER_TYPES_FULL:
		score -= 40

	# Offscreen penalty (monitor-relative bounds via screen_origin)
	screen_left, screen_top = screen_origin
	screen_right = screen_left + screen_w
	screen_bottom = screen_top + screen_h
	if (c.center[0] < screen_left or c.center[1] < screen_top
			or c.center[0] > screen_right or c.center[1] > screen_bottom):
		score -= 100

	# Coords unavailable penalty
	if c.coords_unavailable:
		score -= 20

	# Depth bonus: shallow elements are structurally more important (nav, toolbars)
	if c.depth <= 2:
		score += 40
	elif c.depth <= 5:
		score += 20

	# Sibling repetition penalty: data rows in large lists
	if c.sibling_same_type_count > 20:
		score -= 30

	# Navigation role boost: tabs, menus, tree items are structurally important
	if c.control_type in _NAV_TYPES:
		score += 35

	# Few-sibling ListItem is likely navigation, not data
	if c.control_type == "ListItemControl" and c.sibling_same_type_count <= 10:
		score += 25

	return score


def _walk_and_rank(
	root: uiautomation.Control,
	scale_factor: float,
	limit: int = 500,
	type_filter: set[str] | None = None,
	screen_size: tuple[int, int] = (1920, 1080),
	max_depth: int = 50,
	viewport_rect: tuple[int, int, int, int] | None = None,
	screen_origin: tuple[int, int] = (0, 0),
) -> tuple[dict[str, ElementInfo], int, int, bool, int, int, bool]:
	"""Standard mode: two-pass BFS walk with scoring and ranking.

	Pass 1: Full BFS collecting all interactive candidates (no cap).
	Pass 2: Score, rank, take top `limit`, assign labels and parent_label.

	Returns:
		(elements, ghost_filtered_count, coords_unavailable_count, was_capped, total_candidates, viewport_filtered_count, cache_used)
	"""
	candidates: list[_Candidate] = []
	ghost_filtered = 0
	coords_unavailable_count = 0
	viewport_filtered_count = 0
	bfs_counter = 0
	explorer_tab_cache: dict[int, set[str] | None] = {}
	interactive_types = type_filter if type_filter else INTERACTIVE_TYPES
	cache_used = False

	# --- Fast path: CacheRequest-based walk (single COM roundtrip) ---
	try:
		from windows_native_mcp.core.cached_walk import collect_candidates as _cached_collect
		result = _cached_collect(
			root, interactive_types, viewport_rect,
			scale_factor, max_depth=max_depth,
			max_candidates=limit * 3,
		)
		if result is not None:
			cached_candidates, ghost_filtered, coords_unavailable_count, viewport_filtered_count = result
			# Convert cached_walk._Candidate to uia._Candidate (same fields)
			for cc in cached_candidates:
				candidates.append(_Candidate(
					control_type=cc.control_type, name=cc.name,
					automation_id=cc.automation_id, is_enabled=cc.is_enabled,
					bounding_rect=cc.bounding_rect, center=cc.center,
					coords_unavailable=cc.coords_unavailable, depth=cc.depth,
					parent_idx=cc.parent_idx, area=cc.area,
					bfs_order=cc.bfs_order, checked=cc.checked,
					selected=cc.selected, sibling_same_type_count=cc.sibling_same_type_count,
				))
			bfs_counter = len(candidates)
			cache_used = True
			logging.info(f"UIA: Cached walk collected {len(candidates)} candidates")
	except Exception as e:
		logging.info(f"UIA: Cached walk unavailable ({e}), using BFS")

	if not cache_used:
		# --- Slow path: Pass 1 BFS walk (no cap) ---
		# Queue entries: (control, depth, parent_candidate_idx)
		queue: deque[tuple[uiautomation.Control, int, int]] = deque()
		for child in _safe_get_children(root):
			queue.append((child, 0, -1))

	_sibling_counter: dict[tuple[int, str], int] = {}

	# BFS loop — only entered when cache_used is False
	while not cache_used and queue:
		control, depth, parent_candidate_idx = queue.popleft()

		try:
			ctrl_type = control.ControlTypeName or ""
		except Exception:
			continue

		# Lazy reads: only fetch class_name/rect when needed for filtering or interactive
		need_full = ctrl_type in interactive_types or ctrl_type in _NEEDS_FILTER_CHECK
		class_name = ""
		rect = (0, 0, 0, 0)
		if need_full:
			try:
				class_name = control.ClassName or ""
				rect = _get_raw_rect(control)
			except Exception:
				if ctrl_type in interactive_types:
					# Can't read props for interactive — skip element, still walk children
					try:
						for child in _safe_get_children(control):
							queue.append((child, depth + 1, parent_candidate_idx))
					except OverflowError:
						pass
					continue
				continue

			# Ghost filter: (0,0,0,0) + PopupHost → Win11 ghost duplicate
			if rect == (0, 0, 0, 0) and "PopupHost" in class_name:
				ghost_filtered += 1
				continue

			# File Explorer tab filter: skip inactive tab panes
			if _should_skip_inactive_tab(control, class_name, explorer_tab_cache):
				continue

		my_idx = parent_candidate_idx  # Default: pass-through parent

		# Check if interactive
		if ctrl_type in interactive_types:
			try:
				name = control.Name or ""
				automation_id = control.AutomationId or ""
				is_enabled = True
				try:
					is_enabled = control.IsEnabled
				except Exception:
					pass
			except Exception:
				# Can't read properties — skip this element but continue walking
				for child in _safe_get_children(control):
					queue.append((child, depth + 1, parent_candidate_idx))
				continue

			# Build bounding rect and center (similar to _build_element_info but inline)
			left, top, right, bottom = rect
			c_coords_unavailable = False

			if rect == (0, 0, 0, 0):
				cx, cy = _try_clickable_point(control)
				if cx is not None and cy is not None:
					left = cx - 5
					top = cy - 5
					right = cx + 5
					bottom = cy + 5
				else:
					c_coords_unavailable = True

			if scale_factor > 1.0 and not c_coords_unavailable:
				left = int(left / scale_factor)
				top = int(top / scale_factor)
				right = int(right / scale_factor)
				bottom = int(bottom / scale_factor)

			if c_coords_unavailable:
				center = (0, 0)
			else:
				center = ((left + right) // 2, (top + bottom) // 2)

			# Viewport filter: skip elements that don't intersect the visible area.
			# Uses AABB intersection test on element bounding rect vs window rect.
			# Also filter coords_unavailable elements — in virtualized lists
			# (e.g. File Explorer Home), offscreen items report (0,0,0,0)
			if viewport_rect:
				if c_coords_unavailable:
					viewport_filtered_count += 1
					if depth < max_depth:
						try:
							for child in _safe_get_children(control):
								queue.append((child, depth + 1, parent_candidate_idx))
						except OverflowError:
							pass
					continue
				vl, vt, vr, vb = viewport_rect
				if right < vl or left > vr or bottom < vt or top > vb:
					viewport_filtered_count += 1
					if depth < max_depth:
						try:
							for child in _safe_get_children(control):
								queue.append((child, depth + 1, parent_candidate_idx))
						except OverflowError:
							pass
					continue

			area = max(0, (right - left)) * max(0, (bottom - top))

			# Read checked/selected state for applicable types
			checked = None
			selected = None
			if ctrl_type in ("CheckBoxControl", "ButtonControl"):
				# ButtonControl included: WinUI ToggleSwitch exposes as Button with TogglePattern
				try:
					pattern = control.GetTogglePattern()
					if pattern:
						checked = pattern.ToggleState != 0
				except Exception:
					pass
			elif ctrl_type in ("RadioButtonControl", "ListItemControl", "TabItemControl"):
				try:
					pattern = control.GetSelectionItemPattern()
					if pattern:
						selected = pattern.IsSelected
				except Exception:
					pass

			candidate = _Candidate(
				control_type=ctrl_type,
				name=name,
				automation_id=automation_id,
				is_enabled=is_enabled,
				bounding_rect=(left, top, right, bottom),
				center=center,
				coords_unavailable=c_coords_unavailable,
				depth=depth,
				parent_idx=parent_candidate_idx,
				area=area,
				bfs_order=bfs_counter,
				checked=checked,
				selected=selected,
			)
			candidates.append(candidate)
			sib_key = (parent_candidate_idx, ctrl_type)
			_sibling_counter[sib_key] = _sibling_counter.get(sib_key, 0) + 1
			candidate.sibling_same_type_count = _sibling_counter[sib_key]
			my_idx = len(candidates) - 1
			bfs_counter += 1

			if c_coords_unavailable:
				coords_unavailable_count += 1

		# Early termination: enough candidates for scoring
		# Continue past limit*3 if no nav types found yet (dense UIs like Task Manager)
		if len(candidates) >= limit * 3:
			if len(candidates) >= limit * 5:
				break  # Hard cap
			has_nav = any(
				c.control_type in _NAV_TYPES
				or (c.control_type == "ListItemControl" and c.sibling_same_type_count <= 10)
				for c in candidates
			)
			if has_nav:
				break

		# Descend into children (respecting depth limit)
		if depth < max_depth:
			try:
				for child in _safe_get_children(control):
					queue.append((child, depth + 1, my_idx))
			except OverflowError:
				continue  # Skip this subtree

	# --- Pass 2: Score, rank, select top `limit` ---
	# total_candidates = all interactive elements encountered (passed filters + viewport filtered)
	# This makes the relationship clear: total_candidates - viewport_filtered_count = candidates passed
	total_candidates = len(candidates) + viewport_filtered_count
	capped = len(candidates) > limit

	screen_w, screen_h = screen_size
	scored = [(i, _score_candidate(c, screen_w, screen_h, screen_origin)) for i, c in enumerate(candidates)]
	scored.sort(key=lambda x: (-x[1], candidates[x[0]].bfs_order))
	selected_indices = [i for i, _ in scored[:limit]]

	# --- Pass 2.5: Reserved slots for navigation types ---
	# Ensure nav elements aren't completely pruned in dense UIs.
	# Covers: TabItem/MenuItem/TreeItem AND ListItems with ≤10 siblings (nav-like, not data rows).
	# Scan rejected candidates; swap them in by evicting lowest-scored.
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

	# Build mapping: original_candidate_idx → assigned_label
	selected_set = set(selected_indices)
	idx_to_label: dict[int, str] = {}
	for label_num, orig_idx in enumerate(selected_indices, start=1):
		idx_to_label[orig_idx] = str(label_num)

	# Recompute coords_unavailable from selected elements only (not full candidate set)
	coords_unavailable_count = sum(1 for i in selected_indices if candidates[i].coords_unavailable)

	# Resolve parent_label for each selected candidate
	elements: dict[str, ElementInfo] = {}
	for orig_idx in selected_indices:
		c = candidates[orig_idx]
		ctrl_type_clean = c.control_type.removesuffix("Control")

		# Walk up parent chain to find nearest selected ancestor
		parent_label = None
		walk_idx = c.parent_idx
		while walk_idx >= 0:
			if walk_idx in selected_set:
				parent_label = idx_to_label[walk_idx]
				break
			walk_idx = candidates[walk_idx].parent_idx

		label = idx_to_label[orig_idx]
		elements[label] = ElementInfo(
			label=label,
			name=c.name,
			control_type=ctrl_type_clean,
			bounding_rect=c.bounding_rect,
			center=c.center,
			automation_id=c.automation_id,
			is_enabled=c.is_enabled,
			coords_unavailable=c.coords_unavailable,
			parent_label=parent_label,
			depth=c.depth,
			checked=c.checked,
			selected=c.selected,
		)

	return elements, ghost_filtered, coords_unavailable_count, capped, total_candidates, viewport_filtered_count, cache_used


def _walk_tree(
	root: uiautomation.Control,
	scale_factor: float,
	filter_interactive: bool,
	max_elements: int,
) -> tuple[dict[str, ElementInfo], int, int]:
	"""General BFS tree walk. Used by "full" mode.

	Args:
		root: Root control to walk from.
		scale_factor: DPI scale.
		filter_interactive: If True, only collect INTERACTIVE_TYPES.
		max_elements: Cap (0 = unlimited).

	Returns:
		(elements, ghost_filtered_count, coords_unavailable_count)
	"""
	elements: dict[str, ElementInfo] = {}
	ghost_filtered = 0
	coords_unavailable = 0
	label_counter = 0
	explorer_tab_cache: dict[int, set[str] | None] = {}

	# Queue: (control, depth, parent_label)
	queue: deque[tuple[uiautomation.Control, int, str | None]] = deque()
	for child in _safe_get_children(root):
		queue.append((child, 0, None))

	while queue:
		if max_elements > 0 and label_counter >= max_elements:
			break

		control, depth, parent_label = queue.popleft()

		try:
			ctrl_type = control.ControlTypeName or ""
			class_name = control.ClassName or ""
			rect = _get_raw_rect(control)
		except Exception:
			continue

		# Ghost filter
		if rect == (0, 0, 0, 0) and "PopupHost" in class_name:
			ghost_filtered += 1
			continue

		# File Explorer tab filter: skip inactive tab panes
		if _should_skip_inactive_tab(control, class_name, explorer_tab_cache):
			continue

		# Collect based on filter
		current_label = parent_label  # For children to inherit
		should_collect = (not filter_interactive) or (ctrl_type in INTERACTIVE_TYPES)
		if should_collect:
			info, is_coords_unavail = _build_element_info(
				control, ctrl_type, rect, class_name, label_counter + 1, scale_factor,
			)
			if info is not None:
				label_counter += 1
				info.control_type = ctrl_type.removesuffix("Control")
				info.parent_label = parent_label
				info.depth = depth
				# Add checked/selected state for applicable types
				if ctrl_type in ("CheckBoxControl", "ButtonControl"):
					try:
						pattern = control.GetTogglePattern()
						if pattern:
							info.checked = pattern.ToggleState != 0
					except Exception:
						pass
				elif ctrl_type in ("RadioButtonControl", "ListItemControl", "TabItemControl"):
					try:
						pattern = control.GetSelectionItemPattern()
						if pattern:
							info.selected = pattern.IsSelected
					except Exception:
						pass
				elements[info.label] = info
				current_label = info.label
				if is_coords_unavail:
					coords_unavailable += 1

		# Descend
		for child in _safe_get_children(control):
			queue.append((child, depth + 1, current_label))

	return elements, ghost_filtered, coords_unavailable


def _collect_minimal(
	root: uiautomation.Control,
	window_name: str | None,
	scale_factor: float,
) -> tuple[dict[str, ElementInfo], int, int]:
	"""Minimal mode: top-level windows only (no tree walk).

	If window_name was provided and root is already scoped to that window,
	collect just that window's immediate children as pseudo-windows.
	"""
	elements: dict[str, ElementInfo] = {}
	ghost_filtered = 0
	coords_unavailable = 0
	label_counter = 0

	# If scoped to a window, the root IS that window — list its direct children
	# If not scoped, root is the desktop — list top-level windows
	children = _safe_get_children(root)

	for child in children:
		try:
			ctrl_type = child.ControlTypeName or ""
			class_name = child.ClassName or ""
			rect = _get_raw_rect(child)
			name = child.Name or ""

			# In non-scoped mode, only collect windows
			if window_name is None and ctrl_type != "WindowControl":
				continue

			# Skip empty-title windows at desktop level
			if window_name is None and not name.strip():
				continue

			# Ghost filter
			if rect == (0, 0, 0, 0) and "PopupHost" in class_name:
				ghost_filtered += 1
				continue

			info, is_coords_unavail = _build_element_info(
				child, ctrl_type, rect, class_name, label_counter + 1, scale_factor,
			)
			if info is not None:
				label_counter += 1
				elements[info.label] = info
				if is_coords_unavail:
					coords_unavailable += 1
		except Exception:
			continue

	return elements, ghost_filtered, coords_unavailable


# ---------------------------------------------------------------------------
# Element construction
# ---------------------------------------------------------------------------

def _build_element_info(
	control: uiautomation.Control,
	ctrl_type: str,
	rect: tuple[int, int, int, int],
	class_name: str,
	label_num: int,
	scale_factor: float,
) -> tuple[ElementInfo | None, bool]:
	"""Build an ElementInfo from a UIA control.

	Args:
		control: The UIA control.
		ctrl_type: Pre-fetched ControlTypeName.
		rect: Pre-fetched raw bounding rectangle.
		class_name: Pre-fetched ClassName.
		label_num: Sequential label number.
		scale_factor: DPI scale for coordinate conversion.

	Returns:
		(ElementInfo or None, coords_unavailable flag)
	"""
	try:
		name = control.Name or ""
		automation_id = control.AutomationId or ""
		is_enabled = True
		try:
			is_enabled = control.IsEnabled
		except Exception:
			pass
	except Exception:
		return None, False

	coords_unavailable = False
	left, top, right, bottom = rect

	if rect == (0, 0, 0, 0):
		# UWP (0,0) handling — try GetClickablePoint
		cx, cy = _try_clickable_point(control)
		if cx is not None and cy is not None:
			# Synthesize a small rect around the clickable point
			left = cx - 5
			top = cy - 5
			right = cx + 5
			bottom = cy + 5
		else:
			coords_unavailable = True

	# Apply scale factor to convert physical → logical if needed.
	# With PROCESS_PER_MONITOR_DPI_AWARE, BoundingRectangle already returns
	# logical pixels on most systems. But if scale_factor != 1.0 and the
	# caller indicates conversion is needed, divide through.
	if scale_factor > 1.0 and not coords_unavailable:
		left = int(left / scale_factor)
		top = int(top / scale_factor)
		right = int(right / scale_factor)
		bottom = int(bottom / scale_factor)

	# Compute center
	if coords_unavailable:
		center = (0, 0)
	else:
		center = ((left + right) // 2, (top + bottom) // 2)

	info = ElementInfo(
		label=str(label_num),
		name=name,
		control_type=ctrl_type,
		bounding_rect=(left, top, right, bottom),
		center=center,
		automation_id=automation_id,
		is_enabled=is_enabled,
		coords_unavailable=coords_unavailable,
	)
	return info, coords_unavailable


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def _resolve_root(
	window_name: str | None,
) -> uiautomation.Control | None:
	"""Determine the root control for tree walking.

	If window_name is provided, scope to that window.
	Otherwise return the desktop root.
	"""
	if window_name:
		win = find_window(window_name)
		if win is None:
			logging.warning(f"UIA: Window '{window_name}' not found")
			return None
		return win

	try:
		root = uiautomation.GetRootControl()
		if root is None:
			logging.warning("UIA: Cannot obtain desktop root control")
		return root
	except Exception:
		logging.warning("UIA: Exception obtaining desktop root control")
		return None


# ---------------------------------------------------------------------------
# Metadata builder
# ---------------------------------------------------------------------------

def _build_metadata(
	element_count: int,
	detail: str,
	window_name: str | None,
	coords_unavailable_count: int,
	ghost_filtered_count: int,
	capped: bool,
	elapsed: float,
	limit: int = 500,
	total_candidates: int = 0,
	scoring: bool = False,
	viewport_filtered_count: int = 0,
	cache_used: bool = False,
	window_handle: int | None = None,
	window_minimized: bool = False,
) -> dict:
	"""Build the metadata dict returned alongside elements."""
	meta = {
		"element_count": element_count,
		"detail": detail,
		"window_scoped": window_name,
		"coords_unavailable_count": coords_unavailable_count,
		"coords_available_count": element_count - coords_unavailable_count,
		"ghost_filtered_count": ghost_filtered_count,
		"elapsed_seconds": round(elapsed, 3),
	}
	if cache_used:
		meta["cache_used"] = True
	if window_handle:
		meta["window_handle"] = window_handle
	if window_minimized:
		meta["window_minimized"] = True
	if viewport_filtered_count > 0:
		meta["viewport_filtered_count"] = viewport_filtered_count
	if scoring:
		meta["scoring"] = True
		if total_candidates > 0:
			meta["total_candidates"] = total_candidates
	if capped:
		meta["capped_at"] = limit
		meta["note"] = (
			f"Element limit ({limit}) reached. "
			"Scope to a specific window for complete results."
		)
	# Diagnostic note when all elements were filtered out
	if element_count == 0 and total_candidates > 0 and not capped:
		if viewport_filtered_count == total_candidates:
			meta["note"] = (
				"All interactive elements were outside the viewport. "
				"The window may be minimized or obscured."
			)
		else:
			meta["note"] = "All candidates were filtered during scoring."
	return meta


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------

def _get_native_handle(control: uiautomation.Control) -> int:
	"""Get the native window handle (HWND) from a control."""
	try:
		return control.NativeWindowHandle
	except Exception:
		return 0


def _get_process_id(control: uiautomation.Control) -> int:
	"""Get the process ID owning a control."""
	try:
		return control.ProcessId
	except Exception:
		return 0


def _get_raw_rect(
	control: uiautomation.Control,
) -> tuple[int, int, int, int]:
	"""Get the BoundingRectangle as a (left, top, right, bottom) tuple."""
	try:
		rect = control.BoundingRectangle
		left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
		# Filter sentinel/garbage values from UIA (Rect.Empty → INT32_MAX,
		# Chromium unclipped offscreen coords). 65536 covers 16K multi-monitor.
		if (abs(left) > _MAX_COORD or abs(top) > _MAX_COORD
				or abs(right) > _MAX_COORD or abs(bottom) > _MAX_COORD):
			return (0, 0, 0, 0)
		return (left, top, right, bottom)
	except Exception:
		return (0, 0, 0, 0)


def _get_bounding_dict(control: uiautomation.Control) -> dict:
	"""Get BoundingRectangle as a dict with named keys."""
	left, top, right, bottom = _get_raw_rect(control)
	return {"left": left, "top": top, "right": right, "bottom": bottom}


def _is_window_minimized(handle: int) -> bool:
	"""Check if a window is minimized via Win32 IsIconic."""
	if not handle:
		return False
	try:
		return bool(ctypes.windll.user32.IsIconic(handle))
	except (AttributeError, OSError, OverflowError):
		return False


def _try_clickable_point(
	control: uiautomation.Control,
) -> tuple[int | None, int | None]:
	"""Try to get a clickable point for elements with (0,0,0,0) rects.

	Returns (x, y) in raw coordinates, or (None, None) on failure.
	"""
	try:
		point = control.GetClickablePoint()
		if point and hasattr(point, "x") and hasattr(point, "y"):
			if point.x != 0 or point.y != 0:
				return (point.x, point.y)
		return (None, None)
	except Exception:
		return (None, None)


def _get_active_tab_names(
	parent_control: uiautomation.Control,
) -> set[str] | None:
	"""Get names of selected tabs from a TabControl in the parent's subtree.

	Used to filter inactive File Explorer tab panes (ShellTabWindowClass).
	Searches up to 4 levels deep (Explorer's TabControl is at depth 2-3
	inside DesktopChildSiteBridge > InputSiteWindowClass > TabControl).
	Returns set of active tab names, or None if no TabControl found.
	"""
	active_names = set()

	# Shallow BFS to find TabControl (max depth 4)
	search_queue: deque[tuple[uiautomation.Control, int]] = deque()
	for child in _safe_get_children(parent_control):
		search_queue.append((child, 0))

	while search_queue:
		ctrl, depth = search_queue.popleft()
		if depth > 4:
			continue
		try:
			if ctrl.ControlTypeName == "TabControl":
				# TabItemControl may be direct children or nested inside
				# a ListControl (Explorer: TabControl > ListControl > TabItemControl)
				tab_items_queue = deque(_safe_get_children(ctrl))
				for _ in range(200):  # Safety limit
					if not tab_items_queue:
						break
					item = tab_items_queue.popleft()
					try:
						item_type = item.ControlTypeName
						if item_type == "TabItemControl":
							pattern = item.GetSelectionItemPattern()
							if pattern and pattern.IsSelected:
								name = item.Name or ""
								if name:
									active_names.add(name)
						elif item_type in ("ListControl", "GroupControl"):
							# Descend into containers that wrap TabItemControls
							tab_items_queue.extend(_safe_get_children(item))
					except Exception:
						continue
				if active_names:
					return active_names
			# Descend further (but not into ShellTabWindowClass panes)
			if depth < 4:
				for child in _safe_get_children(ctrl):
					search_queue.append((child, depth + 1))
		except Exception:
			continue

	return active_names if active_names else None


def _should_skip_inactive_tab(
	control: uiautomation.Control,
	class_name: str,
	cache: dict[int, set[str] | None],
) -> bool:
	"""Check if a ShellTabWindowClass pane belongs to an inactive Explorer tab.

	Caches active tab lookups per parent handle to avoid repeated COM calls.
	Returns True if the control should be skipped (inactive tab pane).
	"""
	if class_name != "ShellTabWindowClass":
		return False

	try:
		parent = control.GetParentControl()
		if parent is None:
			return False
		parent_handle = _get_native_handle(parent)
		if parent_handle not in cache:
			cache[parent_handle] = _get_active_tab_names(parent)
		active_names = cache[parent_handle]
		if active_names is None:
			return False  # No TabControl found — don't skip
		control_name = control.Name or ""
		return control_name not in active_names
	except Exception:
		return False  # On any error, don't skip (safe fallback)


def _safe_get_children(
	control: uiautomation.Control,
) -> list[uiautomation.Control]:
	"""Safely get children of a control, returning empty list on COM errors."""
	try:
		children = control.GetChildren()
		return children if children else []
	except Exception:  # Includes OverflowError from 64-bit handle reads
		return []
