"""CacheRequest-based fast-path tree walk.

Batches all UI Automation property reads into a single COM roundtrip
via IUIAutomationCacheRequest + BuildUpdatedCache. Falls back silently
to None on any failure, allowing the caller to use the traditional BFS.
"""
import logging
import threading
from collections import deque
from dataclasses import dataclass

import uiautomation

# Re-use _Candidate from uia.py (imported by caller), but define locally
# to keep this module self-contained and avoid circular imports.
@dataclass
class _Candidate:
	"""Pre-label element data from cached walk."""
	control_type: str
	name: str
	automation_id: str
	is_enabled: bool
	bounding_rect: tuple[int, int, int, int]
	center: tuple[int, int]
	coords_unavailable: bool
	depth: int
	parent_idx: int
	area: int
	bfs_order: int
	checked: bool | None = None
	selected: bool | None = None


# ---------------------------------------------------------------------------
# UIA property/pattern IDs
# ---------------------------------------------------------------------------
UIA_ControlTypePropertyId = 30003
UIA_NamePropertyId = 30005
UIA_ClassNamePropertyId = 30012
UIA_BoundingRectanglePropertyId = 30001
UIA_AutomationIdPropertyId = 30011
UIA_IsEnabledPropertyId = 30010
UIA_NativeWindowHandlePropertyId = 30020

UIA_TogglePatternId = 10015
UIA_SelectionItemPatternId = 10010

_MAX_COORD = 65536

# ---------------------------------------------------------------------------
# Lazy COM interface access
# ---------------------------------------------------------------------------
_iua = None
_iua_lock = threading.Lock()

# Track COM init per thread (same pattern as uia.py)
_com_initialized: set[int] = set()
_com_lock = threading.Lock()


def _ensure_com():
	"""Ensure COM is initialized for the current thread."""
	tid = threading.current_thread().ident
	if tid in _com_initialized:
		return
	with _com_lock:
		if tid in _com_initialized:
			return
		import ctypes
		try:
			ctypes.windll.ole32.CoInitializeEx(None, 0)
		except OSError:
			pass
		_com_initialized.add(tid)


def _get_iua():
	"""Get the IUIAutomation COM object (lazy, thread-safe)."""
	global _iua
	if _iua is not None:
		return _iua
	with _iua_lock:
		if _iua is not None:
			return _iua
		import comtypes
		from comtypes.gen import UIAutomationClient
		CLSID_CUIAutomation = comtypes.GUID('{FF48DBA4-60EF-4201-AA87-54103EEF594E}')
		_iua = comtypes.CoCreateInstance(
			CLSID_CUIAutomation,
			interface=UIAutomationClient.IUIAutomation,
		)
		return _iua


def _get_uia_module():
	"""Get the UIAutomationClient comtypes module for interface types."""
	from comtypes.gen import UIAutomationClient
	return UIAutomationClient


# ---------------------------------------------------------------------------
# CacheRequest builder
# ---------------------------------------------------------------------------

def _build_cache_request():
	"""Create and configure an IUIAutomationCacheRequest."""
	iua = _get_iua()
	cr = iua.CreateCacheRequest()

	# 7 properties
	cr.AddProperty(UIA_ControlTypePropertyId)
	cr.AddProperty(UIA_NamePropertyId)
	cr.AddProperty(UIA_ClassNamePropertyId)
	cr.AddProperty(UIA_BoundingRectanglePropertyId)
	cr.AddProperty(UIA_AutomationIdPropertyId)
	cr.AddProperty(UIA_IsEnabledPropertyId)
	cr.AddProperty(UIA_NativeWindowHandlePropertyId)

	# 2 patterns
	cr.AddPattern(UIA_TogglePatternId)
	cr.AddPattern(UIA_SelectionItemPatternId)

	# TreeScope_Subtree = 7 (element + descendants)
	cr.TreeScope = 7

	# Use RawViewCondition to match RawViewWalker used by existing BFS
	cr.TreeFilter = iua.RawViewCondition

	# AutomationElementMode_Full = 1 (keep live refs as fallback)
	cr.AutomationElementMode = 1

	return cr


# ---------------------------------------------------------------------------
# Cached element helpers
# ---------------------------------------------------------------------------

def _iter_cached_children(element):
	"""Yield cached children of a cached element."""
	try:
		children = element.GetCachedChildren()
		if children is None:
			return
		length = children.Length
		for i in range(length):
			yield children.GetElement(i)
	except Exception:
		return


def _cached_rect(element) -> tuple[int, int, int, int]:
	"""Extract cached bounding rect, applying sentinel filter."""
	try:
		rect = element.CachedBoundingRectangle
		left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
		if (abs(left) > _MAX_COORD or abs(top) > _MAX_COORD
				or abs(right) > _MAX_COORD or abs(bottom) > _MAX_COORD):
			return (0, 0, 0, 0)
		return (left, top, right, bottom)
	except Exception:
		return (0, 0, 0, 0)


def _read_toggle_state(element) -> bool | None:
	"""Read TogglePattern state from a cached element.

	Uses GetCachedPattern to retrieve the pattern (no COM roundtrip for
	pattern lookup), then reads CurrentToggleState (live property read).
	CachedToggleState throws E_INVALIDARG due to a Windows UIA/comtypes
	marshalling issue, but CurrentToggleState works because we cache with
	AutomationElementMode_Full (live element refs preserved).
	"""
	try:
		uia_mod = _get_uia_module()
		pattern_unk = element.GetCachedPattern(UIA_TogglePatternId)
		if pattern_unk is None:
			return None
		toggle = pattern_unk.QueryInterface(uia_mod.IUIAutomationTogglePattern)
		return toggle.CurrentToggleState != 0
	except Exception:
		return None


def _read_is_selected(element) -> bool | None:
	"""Read SelectionItemPattern state from a cached element.

	Same approach as _read_toggle_state: cached pattern lookup,
	live property read via CurrentIsSelected.
	"""
	try:
		uia_mod = _get_uia_module()
		pattern_unk = element.GetCachedPattern(UIA_SelectionItemPatternId)
		if pattern_unk is None:
			return None
		sel = pattern_unk.QueryInterface(uia_mod.IUIAutomationSelectionItemPattern)
		return bool(sel.CurrentIsSelected)
	except Exception:
		return None


def _ctrl_type_name(ctrl_type_id: int) -> str:
	"""Map control type ID to name string (e.g. 50000 → 'ButtonControl')."""
	return uiautomation.ControlTypeNames.get(ctrl_type_id, "")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def collect_candidates(
	root: uiautomation.Control,
	interactive_types: set[str],
	viewport_rect: tuple[int, int, int, int] | None,
	scale_factor: float,
	max_depth: int = 50,
	max_candidates: int = 1500,
) -> tuple[list[_Candidate], int, int, int] | None:
	"""CacheRequest-based tree walk returning candidates compatible with Pass 2.

	Returns (candidates, ghost_filtered, coords_unavailable, viewport_filtered)
	or None on failure (caller should fall back to BFS).
	"""
	try:
		_ensure_com()

		# Get raw COM element from uiautomation Control
		root_element = root.Element

		# Build and execute CacheRequest — single COM roundtrip
		cache_request = _build_cache_request()
		cached_root = root_element.BuildUpdatedCache(cache_request)

		candidates: list[_Candidate] = []
		ghost_filtered = 0
		coords_unavailable_count = 0
		viewport_filtered_count = 0
		bfs_counter = 0

		# BFS using cached elements (zero COM calls from here)
		# Queue: (cached_element, depth, parent_candidate_idx)
		queue: deque[tuple] = deque()
		for child in _iter_cached_children(cached_root):
			queue.append((child, 0, -1))

		while queue:
			element, depth, parent_candidate_idx = queue.popleft()

			try:
				ctrl_type_id = element.CachedControlType
			except Exception:
				continue

			ctrl_type = _ctrl_type_name(ctrl_type_id)
			if not ctrl_type:
				# Unknown type — still descend
				if depth < max_depth:
					for child in _iter_cached_children(element):
						queue.append((child, depth + 1, parent_candidate_idx))
				continue

			# Lazy reads for filtering
			class_name = ""
			rect = (0, 0, 0, 0)
			need_filter = ctrl_type in interactive_types or ctrl_type in _NEEDS_FILTER_CHECK
			if need_filter:
				try:
					class_name = element.CachedClassName or ""
				except Exception:
					class_name = ""
				rect = _cached_rect(element)

				# Ghost filter
				if rect == (0, 0, 0, 0) and "PopupHost" in class_name:
					ghost_filtered += 1
					continue

			my_idx = parent_candidate_idx

			if ctrl_type in interactive_types:
				try:
					name = element.CachedName or ""
				except Exception:
					name = ""
				try:
					automation_id = element.CachedAutomationId or ""
				except Exception:
					automation_id = ""
				try:
					is_enabled = bool(element.CachedIsEnabled)
				except Exception:
					is_enabled = True

				left, top, right, bottom = rect
				c_coords_unavailable = False

				if rect == (0, 0, 0, 0):
					# No GetClickablePoint fallback in cached path
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

				# Viewport filter
				if viewport_rect:
					if c_coords_unavailable:
						viewport_filtered_count += 1
						if depth < max_depth:
							for child in _iter_cached_children(element):
								queue.append((child, depth + 1, parent_candidate_idx))
						continue
					vl, vt, vr, vb = viewport_rect
					cx, cy = center
					if cx < vl or cx > vr or cy < vt or cy > vb:
						viewport_filtered_count += 1
						if depth < max_depth:
							for child in _iter_cached_children(element):
								queue.append((child, depth + 1, parent_candidate_idx))
						continue

				area = max(0, (right - left)) * max(0, (bottom - top))

				# Checked/selected state via cached patterns
				checked = None
				selected = None
				if ctrl_type in ("CheckBoxControl", "ButtonControl"):
					checked = _read_toggle_state(element)
				elif ctrl_type in ("RadioButtonControl", "ListItemControl", "TabItemControl"):
					selected = _read_is_selected(element)

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
				my_idx = len(candidates) - 1
				bfs_counter += 1

				if c_coords_unavailable:
					coords_unavailable_count += 1

			# Early termination
			if len(candidates) >= max_candidates:
				break

			# Descend
			if depth < max_depth:
				for child in _iter_cached_children(element):
					queue.append((child, depth + 1, my_idx))

		return (candidates, ghost_filtered, coords_unavailable_count, viewport_filtered_count)

	except Exception as e:
		logging.info(f"UIA: CacheRequest walk failed ({type(e).__name__}: {e})")
		return None


# Control types that need class_name/rect for ghost filtering
_NEEDS_FILTER_CHECK = {"PaneControl", "WindowControl", "CustomControl", "GroupControl"}
