"""Desktop state management — element registry and invalidation.

Snapshot populates state; action tools consume and invalidate it.
"""
import ctypes
import sys
from dataclasses import dataclass, field
from fastmcp.exceptions import ToolError


def _check_admin() -> bool:
	"""Check if the current process has admin privileges (Windows only)."""
	if sys.platform != "win32":
		return False
	try:
		return ctypes.windll.shell32.IsUserAnAdmin() != 0
	except (AttributeError, OSError):
		return False


@dataclass
class ElementInfo:
	"""A single UI element discovered by Snapshot."""
	label: str
	name: str
	control_type: str
	bounding_rect: tuple[int, int, int, int]  # (left, top, right, bottom) logical px
	center: tuple[int, int]  # (x, y) logical px
	automation_id: str = ""
	is_enabled: bool = True
	coords_unavailable: bool = False  # UWP elements reporting (0,0)
	parent_label: str | None = None    # Nearest interactive ancestor's label (None = root child)
	depth: int = 0                     # BFS depth in original tree
	checked: bool | None = None        # True/False for checkboxes/toggles, None if N/A
	selected: bool | None = None       # True/False for radio buttons, list items, tab items


@dataclass
class DesktopState:
	"""Shared mutable desktop state. Singleton per server process."""
	elements: dict[str, ElementInfo] = field(default_factory=dict)
	scale_factor: float = 1.0
	screen_size: tuple[int, int] = (1920, 1080)
	is_stale: bool = True
	window_name: str | None = None      # Window name from last scoped snapshot
	window_handle: int | None = None     # HWND from last scoped snapshot
	last_element_count: int = -1         # Elements found in last snapshot (-1 = no snapshot yet)
	last_snapshot_params: dict | None = None  # Params from last snapshot (for auto-snapshot replay)
	is_admin: bool = field(default_factory=_check_admin)  # Cached at startup

	def uipi_warning(self, window: str | None = None) -> str | None:
		"""Return a warning string if the last snapshot suggests UIPI issues, else None.

		Skipped when running as admin (UIPI only blocks lower→higher integrity).
		"""
		if self.is_admin:
			return None
		if self.window_name is None or self.last_element_count != 0:
			return None
		target = window or self.window_name
		return (
			f"Window '{target}' returned 0 UI elements in the last snapshot. "
			f"If this window is elevated (admin), input will be silently dropped by Windows (UIPI). "
			f"Run from an admin terminal to interact with elevated windows."
		)

	def resolve_target(self, target: str | list[int]) -> tuple[int, int]:
		"""Resolve a target (label string or [x, y] list) to logical pixel coordinates.

		Raises ToolError if label not found or coordinates are unavailable.
		"""
		if isinstance(target, list):
			if len(target) != 2:
				raise ToolError(f"Coordinate target must be [x, y], got {target}")
			return (int(target[0]), int(target[1]))

		label = str(target)
		if label not in self.elements:
			if self.is_stale:
				raise ToolError(
					f"Element label '{label}' not found. State is stale — "
					"call snapshot first to refresh element labels."
				)
			raise ToolError(
				f"Element label '{label}' not found. Available labels: "
				f"{', '.join(sorted(self.elements.keys(), key=lambda x: int(x) if x.isdigit() else float('inf')))}"
			)

		element = self.elements[label]
		if element.coords_unavailable:
			raise ToolError(
				f"Element '{label}' ({element.name}, {element.control_type}) "
				"has unavailable coordinates (UWP element). Use coordinate-based "
				"targeting from the screenshot instead."
			)

		return element.center

	def invalidate(self):
		"""Mark state as stale. Called by action tools after modifying the UI."""
		self.is_stale = True

	def clear(self):
		"""Clear all elements and mark as stale."""
		self.elements.clear()
		self.is_stale = True


# Module-level singleton
desktop_state = DesktopState()
