"""MCP server exposing the desktop to Claude, over stdio.

Speaks JSON-RPC directly so the package has no dependency on an MCP SDK.
stdout carries the protocol and nothing else; diagnostics go to stderr.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import traceback

from . import a11y
from .session import Desktop

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "uictl", "version": "0.1.0"}

_desktop: Desktop | None = None


def desktop() -> Desktop:
    global _desktop
    if _desktop is None:
        _desktop = Desktop()
    return _desktop


def log(*parts) -> None:
    print("[uictl]", *parts, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
def _element_lines(hits, limit=40):
    return "\n".join(str(h) for h in hits[:limit]) or "no matches"


def tool_screenshot(max_width: int = 1568, region: list | None = None,
                    window: str | None = None, cursor: bool = True):
    desk = desktop()
    img = desk.screenshot(region=tuple(region) if region else None, window=window)
    full_w, full_h = img.size
    scale = 1.0
    if max_width and img.width > max_width:
        from PIL import Image
        scale = max_width / img.width
        img = img.resize((max_width, round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    note = (f"Screen is {desk.width}x{desk.height}. "
            f"This image covers {full_w}x{full_h}")
    if region:
        note += f" starting at ({region[0]},{region[1]})"
    if scale != 1.0:
        note += (f", shown at {img.width}x{img.height} (scaled {scale:.3f}); "
                 f"divide image coordinates by {scale:.3f} before clicking")
    note += ". Click using screen coordinates."
    return [
        {"type": "text", "text": note},
        {"type": "image",
         "data": base64.b64encode(buf.getvalue()).decode(),
         "mimeType": "image/png"},
    ]


def tool_find_elements(name=None, role=None, app=None, exact=False, limit=40):
    hits = desktop().find(name=name, role=role, app=app, exact=exact, limit=limit)
    return (f"{len(hits)} match(es)\n" + _element_lines(hits, limit)
            + "\n\nClick one with click(name=..., role=...), or use its centre "
              "coordinates.")


def tool_click(name=None, role=None, app=None, x=None, y=None, method="auto",
               index=0, button="left", count=1, exact=False):
    desk = desktop()
    if x is not None and y is not None:
        return str(desk.click_at(int(x), int(y), button=button, count=count))
    if not (name or role):
        return "error: pass name/role to find a control, or x and y to click a point"
    return str(desk.click(name=name, role=role, app=app, method=method,
                          index=index, button=button, count=count, exact=exact))


def tool_type_text(text: str, strategy: str = "auto", paste_combo: str = "ctrl+v"):
    used = desktop().type(text, strategy=strategy, paste_combo=paste_combo)
    extra = ("" if used == "keys" else
             " (via the clipboard, because the layout cannot type those "
             "characters directly)")
    return f"typed {len(text)} character(s) using {used}{extra}"


def tool_press_key(combo: str, count: int = 1):
    desktop().key(combo, count=count)
    return f"pressed {combo}" + (f" x{count}" if count > 1 else "")


def tool_scroll(dy: int = 0, dx: int = 0, x=None, y=None):
    at = (int(x), int(y)) if x is not None and y is not None else None
    desktop().scroll(dy=dy, dx=dx, at=at)
    return f"scrolled dy={dy} dx={dx}" + (f" at {at}" if at else "")


def tool_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left"):
    desktop().drag(int(x1), int(y1), int(x2), int(y2), button=button)
    return f"dragged ({x1},{y1}) -> ({x2},{y2})"


def tool_move_mouse(x: int, y: int):
    desktop().move(int(x), int(y))
    return f"moved pointer to ({x},{y})"


def tool_list_windows(app=None):
    wins = desktop().windows(app)
    return _element_lines(wins)


def tool_list_apps():
    names = sorted({n for n, _ in a11y.applications() if n})
    return "applications on the accessibility bus:\n" + "\n".join(names)


def tool_ui_tree(app: str, depth: int = 8, limit: int = 150,
                 interesting_only: bool = True):
    name, node = a11y.find_app(app)
    rows = []
    for el in a11y.walk(node, name, max_depth=depth):
        if interesting_only and el.role not in a11y.INTERACTIVE_ROLES:
            continue
        if not el.showing or el.width <= 0:
            continue
        rows.append(el)
        if len(rows) >= limit:
            break
    body = "\n".join(f"{'  ' * (len(r.path) - 1)}{r}" for r in rows)
    return f"{name}: {len(rows)} control(s)\n{body}" if rows else f"{name}: nothing visible"


def tool_wait_for_element(name=None, role=None, app=None, timeout: float = 10.0):
    el = desktop().wait_for(name=name, role=role, app=app, timeout=timeout)
    return f"appeared: {el}"


def tool_focus_window(pattern: str):
    return desktop().focus_window(pattern)


def tool_launch_app(target: str, wait: float = 1.5):
    return desktop().launch(target, wait=wait)


TOOLS = [
    {
        "name": "screenshot",
        "description": "Capture the screen and look at it. Optionally crop to a "
                       "region [x,y,w,h] or to a window by name. Returns the "
                       "image plus the coordinate space to click in.",
        "inputSchema": {"type": "object", "properties": {
            "max_width": {"type": "integer", "description": "downscale to this width (default 1568)"},
            "region": {"type": "array", "items": {"type": "integer"},
                       "description": "[x, y, width, height]"},
            "window": {"type": "string", "description": "crop to this window's rectangle"},
        }},
        "handler": tool_screenshot,
    },
    {
        "name": "find_elements",
        "description": "Find on-screen controls by accessible name and/or role "
                       "(push button, entry, check box, menu item, link...). "
                       "Prefer this over guessing pixel positions from a "
                       "screenshot: it returns exact rectangles and centres.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "substring of the control's label"},
            "role": {"type": "string", "description": "e.g. 'push button', 'entry'"},
            "app": {"type": "string", "description": "limit to one application"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer"},
        }},
        "handler": tool_find_elements,
    },
    {
        "name": "click",
        "description": "Click a control by name/role, or a point with x and y. "
                       "method 'pointer' moves the real cursor, 'action' invokes "
                       "the control directly (works even when covered by another "
                       "window), 'auto' picks sensibly.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"},
            "app": {"type": "string"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "method": {"type": "string", "enum": ["auto", "pointer", "action"]},
            "index": {"type": "integer", "description": "which match, when several"},
            "button": {"type": "string", "enum": ["left", "right", "middle", "back", "forward"]},
            "count": {"type": "integer", "description": "2 for a double click"},
            "exact": {"type": "boolean"},
        }},
        "handler": tool_click,
    },
    {
        "name": "type_text",
        "description": "Type text into whatever has keyboard focus. Characters "
                       "the layout cannot produce (accents, CJK, emoji) go "
                       "through the clipboard automatically.",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string"},
            "strategy": {"type": "string", "enum": ["auto", "keys", "paste"]},
            "paste_combo": {"type": "string",
                            "description": "ctrl+shift+v for terminals"},
        }, "required": ["text"]},
        "handler": tool_type_text,
    },
    {
        "name": "press_key",
        "description": "Press a key combination such as 'enter', 'ctrl+s', "
                       "'alt+F4', 'super', 'ctrl+shift+t'.",
        "inputSchema": {"type": "object", "properties": {
            "combo": {"type": "string"}, "count": {"type": "integer"},
        }, "required": ["combo"]},
        "handler": tool_press_key,
    },
    {
        "name": "scroll",
        "description": "Scroll the wheel. dy is positive up, negative down; "
                       "give x and y to scroll over a particular place.",
        "inputSchema": {"type": "object", "properties": {
            "dy": {"type": "integer"}, "dx": {"type": "integer"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
        }},
        "handler": tool_scroll,
    },
    {
        "name": "drag",
        "description": "Press at one point, move, and release at another.",
        "inputSchema": {"type": "object", "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
            "button": {"type": "string"},
        }, "required": ["x1", "y1", "x2", "y2"]},
        "handler": tool_drag,
    },
    {
        "name": "move_mouse",
        "description": "Move the pointer without clicking, e.g. to reveal a "
                       "hover state or a tooltip.",
        "inputSchema": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
        }, "required": ["x", "y"]},
        "handler": tool_move_mouse,
    },
    {
        "name": "list_windows",
        "description": "List open windows with their titles and rectangles.",
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string"}}},
        "handler": tool_list_windows,
    },
    {
        "name": "list_apps",
        "description": "List applications reachable on the accessibility bus.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_apps,
    },
    {
        "name": "ui_tree",
        "description": "Dump one application's visible controls as an indented "
                       "tree. Use it to learn what can be clicked before acting.",
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string"}, "depth": {"type": "integer"},
            "limit": {"type": "integer"},
            "interesting_only": {"type": "boolean",
                                 "description": "only clickable/editable roles"},
        }, "required": ["app"]},
        "handler": tool_ui_tree,
    },
    {
        "name": "wait_for_element",
        "description": "Block until a control appears, for waiting on a dialog "
                       "or a page that is still loading.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"},
            "app": {"type": "string"}, "timeout": {"type": "number"},
        }},
        "handler": tool_wait_for_element,
    },
    {
        "name": "focus_window",
        "description": "Best-effort raise of a window by title or application "
                       "name. Wayland restricts this, so it can fail; clicking "
                       "the window is the reliable fallback.",
        "inputSchema": {"type": "object", "properties": {
            "pattern": {"type": "string"}}, "required": ["pattern"]},
        "handler": tool_focus_window,
    },
    {
        "name": "launch_app",
        "description": "Start an application by .desktop id (firefox.desktop) "
                       "or by command line.",
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string"}, "wait": {"type": "number"},
        }, "required": ["target"]},
        "handler": tool_launch_app,
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------
def handle(request: dict) -> dict | None:
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{k: v for k, v in t.items() if k != "handler"}
                          for t in TOOLS]}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = BY_NAME.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}],
                    "isError": True}
        try:
            result = tool["handler"](**args)
        except TypeError as exc:
            return {"content": [{"type": "text",
                                 "text": f"bad arguments for {name}: {exc}"}],
                    "isError": True}
        except Exception as exc:
            log("tool failed:", traceback.format_exc())
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True}
        if isinstance(result, list):
            return {"content": result}
        return {"content": [{"type": "text", "text": str(result)}]}

    raise LookupError(f"method not found: {method}")


def serve() -> int:
    log("ready on stdio")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            log("bad json:", exc)
            continue
        req_id = request.get("id")
        try:
            result = handle(request)
        except LookupError as exc:
            response = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32601, "message": str(exc)}}
        except Exception as exc:
            log("server error:", traceback.format_exc())
            response = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32603, "message": str(exc)}}
        else:
            if result is None or req_id is None:
                continue          # notification: nothing to reply
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0
