"""Semantic view of the desktop through AT-SPI.

Lets callers find a control by what it *is* -- "the button called Send" --
instead of guessing pixels from a screenshot.  Every hit carries its screen
rectangle, so it can be clicked either by invoking its accessible action or by
aiming the pointer at it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Iterator

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

# Don't let a wedged application hang us forever.
Atspi.set_timeout(800, 3000)

# Roles that are worth showing when summarising a window.
INTERACTIVE_ROLES = {
    "push button", "toggle button", "check box", "radio button", "link",
    "menu item", "check menu item", "radio menu item", "menu", "text",
    "entry", "password text", "combo box", "list item", "tab", "slider",
    "spin button", "tree item", "table cell", "document web", "heading",
}


class A11yError(RuntimeError):
    pass


@dataclass
class Element:
    name: str
    role: str
    x: int
    y: int
    width: int
    height: int
    path: list[int] = field(default_factory=list)
    app: str = ""
    actions: list[str] = field(default_factory=list)
    text: str = ""
    enabled: bool = True
    showing: bool = True
    focused: bool = False
    coords_trusted: bool = True

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def to_dict(self) -> dict:
        d = asdict(self)
        d["center"] = list(self.center)
        return d

    def __str__(self) -> str:
        flags = "".join([
            "" if self.enabled else "!disabled ",
            "" if self.showing else "!hidden ",
            "*focused " if self.focused else "",
        ])
        label = f"{self.role} {self.name!r}"
        where = (f"[{self.x},{self.y} {self.width}x{self.height}]"
                 if self.coords_trusted
                 else f"[position unknown, {self.width}x{self.height}]")
        return f"{label:<48} {flags}{where} @{self.app}"


def _safe(fn, default=None):
    try:
        return fn()
    except (GLib.Error, Exception):
        return default


def desktop() -> Atspi.Accessible:
    return Atspi.get_desktop(0)


def _extents(node) -> tuple[int, int, int, int]:
    def get():
        comp = node.get_component_iface()
        if comp is None:
            return (0, 0, 0, 0)
        e = comp.get_extents(Atspi.CoordType.SCREEN)
        return (e.x, e.y, e.width, e.height)
    return _safe(get, (0, 0, 0, 0))


def _actions(node) -> list[str]:
    def get():
        ai = node.get_action_iface()
        if ai is None:
            return []
        return [Atspi.Action.get_action_name(ai, i)
                for i in range(Atspi.Action.get_n_actions(ai))]
    return _safe(get, []) or []


def _states(node) -> set[str]:
    def get():
        s = node.get_state_set()
        return {st.value_nick for st in s.get_states()}
    return _safe(get, set()) or set()


def _node_text(node, limit: int = 200) -> str:
    def get():
        ti = node.get_text_iface()
        if ti is None:
            return ""
        n = Atspi.Text.get_character_count(ti)
        if not n:
            return ""
        return Atspi.Text.get_text(ti, 0, min(n, limit))
    return _safe(get, "") or ""


TOPLEVEL_ROLES = ("frame", "window", "dialog", "alert")


def coords_trusted(role: str, x: int, y: int, w: int, h: int) -> bool:
    """Whether an accessible's screen rectangle can be believed.

    Wayland clients are never told where their surface sits, so GTK4
    applications report every control at 0,0.  A real control at exactly the
    screen origin is vanishingly rare, so treat that as "position unknown"
    and click such controls through their accessible action instead.
    """
    if w <= 0 or h <= 0:
        return False
    if x == 0 and y == 0 and role not in TOPLEVEL_ROLES:
        return False
    return True


def to_element(node, path: list[int], app: str, with_text: bool = False) -> Element:
    x, y, w, h = _extents(node)
    states = _states(node)
    return Element(
        name=_safe(node.get_name, "") or "",
        role=_safe(node.get_role_name, "unknown") or "unknown",
        x=x, y=y, width=w, height=h,
        path=list(path),
        app=app,
        actions=_actions(node),
        text=_node_text(node) if with_text else "",
        enabled="enabled" in states or "sensitive" in states,
        showing="showing" in states,
        focused="focused" in states,
        coords_trusted=coords_trusted(
            _safe(node.get_role_name, "") or "", x, y, w, h),
    )


def applications() -> list[tuple[str, Atspi.Accessible]]:
    d = desktop()
    out = []
    for i in range(_safe(d.get_child_count, 0) or 0):
        app = _safe(lambda: d.get_child_at_index(i))
        if app is None:
            continue
        out.append((_safe(app.get_name, "") or f"app{i}", app))
    return out


def find_app(pattern: str) -> tuple[str, Atspi.Accessible]:
    rx = re.compile(pattern, re.I)
    matches = [(n, a) for n, a in applications() if rx.search(n)]
    if not matches:
        names = ", ".join(sorted(n for n, _ in applications()))
        raise A11yError(f"no application matching {pattern!r}. Running: {names}")
    return matches[0]


def walk(node, app: str, path: list[int] | None = None, max_depth: int = 12,
         _depth: int = 0, budget: list[int] | None = None) -> Iterator[Element]:
    """Depth-first walk yielding every accessible under node."""
    path = path or []
    if budget is None:
        budget = [20000]
    if _depth > max_depth or budget[0] <= 0:
        return
    count = _safe(node.get_child_count, 0) or 0
    for i in range(count):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        child = _safe(lambda: node.get_child_at_index(i))
        if child is None:
            continue
        child_path = path + [i]
        yield to_element(child, child_path, app)
        yield from walk(child, app, child_path, max_depth, _depth + 1, budget)


def node_at_path(app_node, path: list[int]):
    node = app_node
    for idx in path:
        node = node.get_child_at_index(idx)
        if node is None:
            raise A11yError(f"path {path} no longer resolves")
    return node


def find(name: str | None = None, role: str | None = None,
         app: str | None = None, exact: bool = False,
         visible_only: bool = True, limit: int = 40,
         max_depth: int = 12) -> list[Element]:
    """Search the accessible tree for matching controls."""
    name_rx = None
    if name:
        name_rx = re.compile(f"^{re.escape(name)}$" if exact else re.escape(name), re.I)
    role_rx = re.compile(role, re.I) if role else None

    apps = [find_app(app)] if app else applications()
    hits: list[Element] = []
    for app_name, node in apps:
        for el in walk(node, app_name, max_depth=max_depth):
            if name_rx and not name_rx.search(el.name):
                continue
            if role_rx and not role_rx.search(el.role):
                continue
            if visible_only and (not el.showing or el.width <= 0 or el.height <= 0):
                continue
            hits.append(el)
            if len(hits) >= limit:
                return hits
    return hits


def wait_for(name: str | None = None, role: str | None = None,
             app: str | None = None, timeout: float = 10.0,
             interval: float = 0.4, **kw) -> Element:
    """Poll until a matching element shows up, or raise."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        hits = find(name=name, role=role, app=app, **kw)
        if hits:
            return hits[0]
        last = hits
        time.sleep(interval)
    raise A11yError(
        f"timed out after {timeout}s waiting for "
        f"name={name!r} role={role!r} app={app!r}"
    )


def do_action(el: Element, action: str = "click") -> str:
    """Invoke an accessible action directly -- no pointer involved.

    This reaches controls that are scrolled out of view or covered by another
    window, which a coordinate click cannot do.
    """
    _, app_node = find_app(re.escape(el.app))
    node = node_at_path(app_node, el.path)
    ai = node.get_action_iface()
    if ai is None:
        raise A11yError(f"{el.role} {el.name!r} exposes no actions")
    names = [Atspi.Action.get_action_name(ai, i)
             for i in range(Atspi.Action.get_n_actions(ai))]
    for i, candidate in enumerate(names):
        if candidate.lower() == action.lower():
            Atspi.Action.do_action(ai, i)
            return candidate
    # Fall back to the conventional primary action.
    for i, candidate in enumerate(names):
        if candidate.lower() in ("click", "press", "activate", "jump", "open"):
            Atspi.Action.do_action(ai, i)
            return candidate
    raise A11yError(
        f"{el.role} {el.name!r} has no {action!r} action (has: {', '.join(names[:8])})"
    )


def grab_focus(el: Element) -> bool:
    _, app_node = find_app(re.escape(el.app))
    node = node_at_path(app_node, el.path)
    comp = node.get_component_iface()
    if comp is None:
        return False
    return bool(_safe(lambda: comp.grab_focus(), False))


def windows(app: str | None = None) -> list[Element]:
    """Top-level windows, as frame/dialog/window accessibles."""
    apps = [find_app(app)] if app else applications()
    out = []
    for app_name, node in apps:
        for i in range(_safe(node.get_child_count, 0) or 0):
            child = _safe(lambda: node.get_child_at_index(i))
            if child is None:
                continue
            role = _safe(child.get_role_name, "") or ""
            if role in ("frame", "window", "dialog", "alert"):
                el = to_element(child, [i], app_name)
                el.actions = [a for a in el.actions if a.startswith("window.")]
                out.append(el)
    return out
