"""UI Automation wrapper — element discovery, window lookup, tree walking.

Wraps the `uiautomation` package to provide structured element discovery
for the Windows Desktop MCP server. All coordinates are in logical pixels.
"""
import ctypes
import ctypes.wintypes
import logging
import threading
import time
from collections import deque

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

# Hard cap for standard mode to prevent token explosion
MAX_ELEMENTS_STANDARD = 500
_MAX_COORD = 65536


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_desktop_elements(
	detail: str = "standard",
	window_name: str | None = None,
	scale_factor: float = 1.0,
) -> tuple[dict[str, ElementInfo], dict]:
	"""Walk the UI tree and return discovered elements with metadata.

	Args:
		detail: Discovery depth — "minimal", "standard", or "full".
		window_name: Scope to a specific window (exact then substring match).
		scale_factor: DPI scale factor for coordinate conversion.

	Returns:
		Tuple of (elements_dict keyed by sequential label, metadata_dict).
	"""
	_ensure_com()
	start = time.perf_counter()
	elements: dict[str, ElementInfo] = {}
	ghost_filtered = 0
	coords_unavailable = 0
	capped = False

	# Determine root control to walk
	root = _resolve_root(window_name)
	if root is None:
		elapsed = time.perf_counter() - start
		logging.info(f"UIA: No root control found ({elapsed:.2f}s)")
		return {}, _build_metadata(
			0, detail, window_name, 0, 0, capped=False, elapsed=elapsed,
		)

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
		elements, ghost_filtered, coords_unavailable, capped = _walk_standard(
			root, scale_factor,
		)

	elapsed = time.perf_counter() - start
	logging.info(
		f"UIA: Collected {len(elements)} elements "
		f"(detail={detail}, ghosts={ghost_filtered}, "
		f"coords_unavail={coords_unavailable}, {elapsed:.2f}s)"
	)

	metadata = _build_metadata(
		len(elements), detail, window_name,
		coords_unavailable, ghost_filtered,
		capped=capped, elapsed=elapsed,
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

def _walk_standard(
	root: uiautomation.Control,
	scale_factor: float,
) -> tuple[dict[str, ElementInfo], int, int, bool]:
	"""Standard mode: BFS walk collecting only interactive types, with cap.

	Returns:
		(elements, ghost_filtered_count, coords_unavailable_count, was_capped)
	"""
	elements: dict[str, ElementInfo] = {}
	ghost_filtered = 0
	coords_unavailable = 0
	label_counter = 0
	capped = False
	explorer_tab_cache: dict[int, set[str] | None] = {}

	queue: deque[uiautomation.Control] = deque()
	# Seed with children of root
	for child in _safe_get_children(root):
		queue.append(child)

	while queue:
		if label_counter >= MAX_ELEMENTS_STANDARD:
			capped = True
			break

		control = queue.popleft()

		try:
			ctrl_type = control.ControlTypeName or ""
			class_name = control.ClassName or ""
			rect = _get_raw_rect(control)
		except Exception:
			continue

		# Ghost filter: (0,0,0,0) + PopupHost → Win11 ghost duplicate
		if rect == (0, 0, 0, 0) and "PopupHost" in class_name:
			ghost_filtered += 1
			continue

		# File Explorer tab filter: skip inactive tab panes
		if _should_skip_inactive_tab(control, class_name, explorer_tab_cache):
			continue

		# Collect if interactive
		if ctrl_type in INTERACTIVE_TYPES:
			info, is_coords_unavail = _build_element_info(
				control, ctrl_type, rect, class_name, label_counter + 1, scale_factor,
			)
			if info is not None:
				label_counter += 1
				elements[info.label] = info
				if is_coords_unavail:
					coords_unavailable += 1

		# Always descend into children (interactive elements may be nested)
		for child in _safe_get_children(control):
			queue.append(child)

	return elements, ghost_filtered, coords_unavailable, capped


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

	queue: deque[uiautomation.Control] = deque()
	for child in _safe_get_children(root):
		queue.append(child)

	while queue:
		if max_elements > 0 and label_counter >= max_elements:
			break

		control = queue.popleft()

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
		should_collect = (not filter_interactive) or (ctrl_type in INTERACTIVE_TYPES)
		if should_collect:
			info, is_coords_unavail = _build_element_info(
				control, ctrl_type, rect, class_name, label_counter + 1, scale_factor,
			)
			if info is not None:
				label_counter += 1
				elements[info.label] = info
				if is_coords_unavail:
					coords_unavailable += 1

		# Descend
		for child in _safe_get_children(control):
			queue.append(child)

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
) -> dict:
	"""Build the metadata dict returned alongside elements."""
	meta = {
		"element_count": element_count,
		"detail": detail,
		"window_scoped": window_name,
		"coords_unavailable_count": coords_unavailable_count,
		"ghost_filtered_count": ghost_filtered_count,
		"elapsed_seconds": round(elapsed, 3),
	}
	if capped:
		meta["capped_at"] = MAX_ELEMENTS_STANDARD
		meta["note"] = (
			f"Element limit ({MAX_ELEMENTS_STANDARD}) reached. "
			"Scope to a specific window for complete results."
		)
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
	except (AttributeError, OSError):
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
	except Exception:
		return []
