"""Screen capture on X11, straight from the root window.

Gdk's pixbuf_get_from_window is an XGetImage underneath -- around 40 ms for
1920x1080, with no consent dialog and no PipeWire -- and it works on every
X11 desktop, including the ones whose xdg-desktop-portal has no ScreenCast
backend to offer.

Two honest differences from the portal route: the cursor is not in the image,
because X11 keeps it out of window contents, and the root window spans every
monitor, so a multi-head setup returns the whole desktop rather than one
screen.  Crop to a window rectangle (Desktop.screenshot(window=...)) when
that matters.
"""
from __future__ import annotations

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk                   # noqa: E402

from .capture import CaptureError               # noqa: E402


class RootWindowSession:
    """The X11 side of the capture interface.

    There is nothing to hold open -- every grab is a fresh XGetImage -- but
    it carries the same start/grab/close shape as the portal session so
    Desktop never has to ask which backend it is holding.
    """

    backend = "x11"

    def __init__(self, cursor: str = "embedded"):
        # Accepted for interface parity: X11 cannot put the cursor in the
        # image, so every mode behaves as 'hidden'.
        self.cursor = cursor
        self._root = None

    def start(self) -> "RootWindowSession":
        if Gdk.Display.get_default() is None:
            raise CaptureError(
                "no X display to read; set DISPLAY, or run inside a "
                "graphical session (capture cannot work over plain SSH)")
        self._root = Gdk.get_default_root_window()
        if self._root is None:
            raise CaptureError("this X display has no root window")
        return self

    def grab(self, settle_frames: int = 1):
        """Return what is on screen now as a PIL image.

        settle_frames is ignored: there is no stream to flush, so the grab is
        already the current contents.
        """
        from PIL import Image
        if self._root is None:
            raise CaptureError("session not started")
        pb = Gdk.pixbuf_get_from_window(
            self._root, 0, 0, self._root.get_width(), self._root.get_height())
        if pb is None:
            raise CaptureError(
                "the X server would not hand over the root window contents")
        mode = "RGBA" if pb.get_has_alpha() else "RGB"
        img = Image.frombytes(mode, (pb.get_width(), pb.get_height()),
                              pb.get_pixels(), "raw", mode, pb.get_rowstride())
        return img.convert("RGB")

    def close(self) -> None:
        self._root = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
