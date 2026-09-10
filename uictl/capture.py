"""Screen capture through the xdg-desktop-portal ScreenCast API.

GNOME 46 refuses the old org.gnome.Shell.Screenshot D-Bus method, so frames
come from PipeWire via the portal.  The portal asks the user to approve
sharing once; we keep the returned restore token so later runs reconnect
silently.
"""
from __future__ import annotations

import os
import random
import string
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import dbus
from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)
Gst.init(None)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"

TOKEN_PATH = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "uictl" / "screencast.token"

CURSOR_MODES = {"hidden": 1, "embedded": 2, "metadata": 4}


class CaptureError(RuntimeError):
    pass


def _token() -> str:
    return "uictl" + "".join(random.choices(string.ascii_lowercase, k=10))


def _load_restore_token() -> str:
    try:
        return TOKEN_PATH.read_text().strip()
    except OSError:
        return ""


def _save_restore_token(token: str) -> None:
    if not token:
        return
    try:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(token)
        TOKEN_PATH.chmod(0o600)
    except OSError:
        pass


class ScreenCastSession:
    """A live portal screencast you can pull frames from.

    Keep one of these alive to take many screenshots cheaply; creating it is
    the slow part (a D-Bus round trip and a PipeWire connection).
    """

    def __init__(self, cursor: str = "embedded", timeout: float = 120.0):
        self.cursor = CURSOR_MODES.get(cursor, 2)
        self.timeout = timeout
        self._bus = dbus.SessionBus()
        self._portal = self._bus.get_object(PORTAL_BUS, PORTAL_PATH)
        self._screencast = dbus.Interface(self._portal, SCREENCAST_IFACE)
        self._session = None
        self._node_id = None
        self._pw_fd = None
        self._pipeline = None
        self._sink = None
        self.used_restore_token = False

    # -- portal request plumbing --
    def _call(self, method, *args, **kw):
        """Invoke a portal method and block until its Response signal."""
        loop = GLib.MainLoop()
        result = {}

        handle_token = _token()
        options = dict(kw.pop("options", {}))
        options["handle_token"] = handle_token
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

        def on_response(code, results):
            result["code"] = int(code)
            result["results"] = results
            loop.quit()

        match = self._bus.add_signal_receiver(
            on_response, signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=request_path)

        def on_timeout():
            result.setdefault("code", -1)
            loop.quit()
            return False

        source = GLib.timeout_add_seconds(int(self.timeout), on_timeout)
        method(*args, options, **kw)
        loop.run()
        GLib.source_remove(source)
        match.remove()

        if result.get("code") == -1:
            raise CaptureError(
                f"portal did not respond within {self.timeout}s -- was the "
                "screen-share dialog left open?")
        if result.get("code") != 0:
            raise CaptureError(
                "screen capture was denied. Approve the screen-share dialog "
                "to allow it; the choice is remembered afterwards.")
        return result["results"]

    def start(self) -> "ScreenCastSession":
        res = self._call(self._screencast.CreateSession,
                         options={"session_handle_token": _token()})
        self._session = res["session_handle"]

        select_opts = {
            "types": dbus.UInt32(1),          # 1 = monitor
            "multiple": False,
            "cursor_mode": dbus.UInt32(self.cursor),
            "persist_mode": dbus.UInt32(2),   # remember until revoked
        }
        restore = _load_restore_token()
        if restore:
            select_opts["restore_token"] = restore
            self.used_restore_token = True
        self._call(self._screencast.SelectSources, self._session,
                   options=select_opts)

        res = self._call(self._screencast.Start, self._session, "",
                         options={})
        _save_restore_token(str(res.get("restore_token", "")))

        streams = res.get("streams")
        if not streams:
            raise CaptureError("portal returned no video streams")
        self._node_id = int(streams[0][0])

        self._pw_fd = self._screencast.OpenPipeWireRemote(
            self._session, {}, signature="oa{sv}").take()
        self._build_pipeline()
        return self

    def _build_pipeline(self) -> None:
        self._pipeline = Gst.Pipeline.new("uictl-capture")
        src = Gst.ElementFactory.make("pipewiresrc")
        if src is None:
            raise CaptureError(
                "GStreamer is missing pipewiresrc "
                "(install gstreamer1.0-pipewire)")
        src.set_property("fd", self._pw_fd)
        src.set_property("path", str(self._node_id))
        # Always hand back the newest frame rather than a queued stale one.
        src.set_property("always-copy", True)
        convert = Gst.ElementFactory.make("videoconvert")
        sink = Gst.ElementFactory.make("appsink")
        sink.set_property("emit-signals", False)
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)
        sink.set_property("sync", False)
        sink.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBA"))
        for el in (src, convert, sink):
            self._pipeline.add(el)
        src.link(convert)
        convert.link(sink)
        self._sink = sink
        self._pipeline.set_state(Gst.State.PLAYING)
        state = self._pipeline.get_state(10 * Gst.SECOND)[0]
        if state != Gst.StateChangeReturn.SUCCESS:
            raise CaptureError("the PipeWire capture pipeline failed to start")

    def grab(self, settle_frames: int = 2):
        """Pull a frame and return it as a PIL image."""
        from PIL import Image
        if self._sink is None:
            raise CaptureError("session not started")
        sample = None
        # The first buffers can predate whatever we just did on screen, so
        # take a couple and keep the newest.
        for _ in range(max(1, settle_frames)):
            got = self._sink.emit("try-pull-sample", 2 * Gst.SECOND)
            if got is not None:
                sample = got
        if sample is None:
            raise CaptureError("no frame arrived from PipeWire")

        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise CaptureError("could not map the video buffer")
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)

        stride = len(data) // height          # rows are padded for alignment
        expected = width * 4
        if stride != expected:
            rows = [data[r * stride: r * stride + expected] for r in range(height)]
            data = b"".join(rows)
        return Image.frombytes("RGBA", (width, height), data).convert("RGB")

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._pw_fd is not None:
            try:
                os.close(self._pw_fd)
            except OSError:
                pass
            self._pw_fd = None
        if self._session is not None:
            try:
                dbus.Interface(
                    self._bus.get_object(PORTAL_BUS, self._session),
                    "org.freedesktop.portal.Session").Close()
            except dbus.DBusException:
                pass
            self._session = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def screenshot(path: str | None = None, region: tuple[int, int, int, int] | None = None,
               cursor: str = "embedded"):
    """Capture the screen once, optionally cropped to (x, y, w, h)."""
    with ScreenCastSession(cursor=cursor) as session:
        img = session.grab()
    if region:
        x, y, w, h = region
        img = img.crop((x, y, x + w, y + h))
    if path:
        img.save(path)
    return img
