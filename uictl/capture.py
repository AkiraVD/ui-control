"""Screen capture, by whichever route this session allows.

Two mechanisms, because the two display servers draw the privacy line in
different places:

  x11     Gdk reads the root window directly.  An X server hands any client
          the whole screen on request, so there is nothing to negotiate: no
          portal, no consent dialog, no PipeWire.  That also makes it the
          only way in on desktops whose portal has no ScreenCast backend at
          all -- Cinnamon, XFCE and MATE ship none.
  portal  xdg-desktop-portal ScreenCast over PipeWire.  On Wayland the
          compositor owns the pixels and this is the only way to ask.

The session type picks the order; UICTL_CAPTURE=x11|portal forces one.  Both
backends present the same start/grab/close shape, so nothing above here has
to know which one it got.
"""
from __future__ import annotations

import os

BACKENDS = ("x11", "portal")


class CaptureError(RuntimeError):
    pass


def _session_prefers_x11() -> bool:
    kind = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if kind in ("x11", "wayland"):
        return kind == "x11"
    # No session type set (a bare login shell, a cron job): trust the display
    # variables.  XWayland sets DISPLAY too, so Wayland has to win there.
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")


def backend_order() -> tuple[str, ...]:
    """The capture backends to try here, best first."""
    forced = os.environ.get("UICTL_CAPTURE", "").strip().lower()
    if forced and forced != "auto":
        if forced not in BACKENDS:
            raise CaptureError(
                f"UICTL_CAPTURE={forced!r} is not one of: auto, "
                + ", ".join(BACKENDS))
        return (forced,)
    return ("x11", "portal") if _session_prefers_x11() else ("portal", "x11")


def new_session(backend: str, cursor: str = "embedded"):
    """Build, but do not start, one named backend."""
    if backend == "x11":
        from .capture_x11 import RootWindowSession
        return RootWindowSession(cursor=cursor)
    if backend == "portal":
        from .capture_portal import ScreenCastSession
        return ScreenCastSession(cursor=cursor)
    raise CaptureError(f"unknown capture backend {backend!r}")


def open_session(cursor: str = "embedded"):
    """Start a capture session, trying each backend this session allows.

    A backend that cannot run here -- no X display, no ScreenCast interface,
    a missing GStreamer element -- is not a failure as long as another one
    works, so the errors are collected and only reported if none does.
    """
    problems = []
    for backend in backend_order():
        try:
            return new_session(backend, cursor).start()
        except Exception as exc:
            problems.append(f"{backend}: {exc}")
    raise CaptureError("no screen capture available on this machine -- "
                       + "; ".join(problems))


def screenshot(path: str | None = None,
               region: tuple[int, int, int, int] | None = None,
               cursor: str = "embedded"):
    """Capture the screen once, optionally cropped to (x, y, w, h)."""
    session = open_session(cursor=cursor)
    try:
        img = session.grab()
    finally:
        session.close()
    if region:
        x, y, w, h = region
        img = img.crop((x, y, x + w, y + h))
    if path:
        img.save(path)
    return img
