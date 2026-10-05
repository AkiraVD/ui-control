"""The high-level desktop automation API.

Ties together the three layers: uinput for real input events, AT-SPI for
finding controls by name, and whichever capture backend this
session allows for pixels.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from . import a11y, keymap
from .a11y import A11yError, Element
from .capture import open_session
from .typing import type_text
from .uinput import Keyboard, Pointer, UInputError


@dataclass
class ClickResult:
    method: str
    element: Element | None
    x: int
    y: int

    def __str__(self) -> str:
        if self.element is None:
            return f"clicked point ({self.x},{self.y})"
        what = f"{self.element.role} {self.element.name!r}"
        if self.method == "action":
            return f"clicked {what} via its accessible action"
        return f"clicked {what} at ({self.x},{self.y}) with the pointer"


def screen_size() -> tuple[int, int]:
    """Total desktop size, from Mutter when available."""
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.DisplayConfig",
             "--object-path", "/org/gnome/Mutter/DisplayConfig",
             "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"],
            capture_output=True, text=True, timeout=5)
        import re
        sizes = re.findall(r"'(\d+)x(\d+)@", out.stdout)
        if sizes:
            w, h = sizes[0]
            return int(w), int(h)
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    if shutil.which("xdotool"):
        try:
            out = subprocess.run(["xdotool", "getdisplaygeometry"],
                                 capture_output=True, text=True, timeout=5)
            w, h = out.stdout.split()
            return int(w), int(h)
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    return 1920, 1080


def notify(summary: str, body: str = "", urgency: str = "normal") -> None:
    """Pop a desktop notification, if the desktop can show one."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "--app-name=uictl", f"--urgency={urgency}",
             "--icon=input-mouse", summary, body],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        pass


class Desktop:
    """One automation session.

    Devices and the capture pipeline are created on first use and reused, so
    holding a single Desktop makes repeated actions fast.

    Because this moves the real cursor and types on the real keyboard, the
    first time a session takes over input it warns on screen and pauses, so
    whoever is sitting at the machine can stop what they are doing.  Set
    UICTL_WARN=0 to skip that, or UICTL_WARN_DELAY to change the pause.
    """

    def __init__(self, width: int | None = None, height: int | None = None,
                 warn: bool | None = None, warn_delay: float | None = None):
        if width is None or height is None:
            width, height = screen_size()
        self.width, self.height = width, height
        self._pointer: Pointer | None = None
        self._keyboard: Keyboard | None = None
        self._capture = None
        self.warn = (os.environ.get("UICTL_WARN", "1") != "0"
                     if warn is None else warn)
        self.warn_delay = (float(os.environ.get("UICTL_WARN_DELAY", "3"))
                           if warn_delay is None else warn_delay)
        self._warned = False

    def _warn_once(self) -> None:
        """Announce that automation is about to drive the real input devices."""
        if self._warned or not self.warn:
            return
        self._warned = True
        notify("uictl is taking over mouse and keyboard",
               f"Automation starts in {self.warn_delay:.0f}s. "
               "Stop typing or moving the mouse.", urgency="critical")
        if self.warn_delay > 0:
            time.sleep(self.warn_delay)

    # -- lazily created backends --
    @property
    def pointer(self) -> Pointer:
        if self._pointer is None:
            self._warn_once()
            self._pointer = Pointer(self.width, self.height)
        return self._pointer

    @property
    def keyboard(self) -> Keyboard:
        if self._keyboard is None:
            self._warn_once()
            self._keyboard = Keyboard()
        return self._keyboard

    @property
    def capture(self):
        """The live capture session -- portal or X11, whichever works here."""
        if self._capture is None:
            self._capture = open_session()
        return self._capture

    # -- pointer --
    def move(self, x: int, y: int) -> None:
        self.pointer.move_to(x, y)

    def click_at(self, x: int, y: int, button: str = "left", count: int = 1,
                 settle: float = 0.15) -> ClickResult:
        self.pointer.move_to(x, y)
        time.sleep(settle)
        self.pointer.click(button, count)
        return ClickResult("pointer", None, x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
             steps: int = 25, settle: float = 0.15) -> None:
        self.pointer.move_to(x1, y1)
        time.sleep(settle)
        self.pointer.button(button, True)
        time.sleep(0.08)
        for i in range(1, steps + 1):
            self.pointer.move_to(
                round(x1 + (x2 - x1) * i / steps),
                round(y1 + (y2 - y1) * i / steps))
            time.sleep(0.012)
        time.sleep(0.08)
        self.pointer.button(button, False)

    def scroll(self, dy: int = 0, dx: int = 0, at: tuple[int, int] | None = None) -> None:
        if at:
            self.pointer.move_to(*at)
            time.sleep(0.12)
        self.pointer.scroll(dy=dy, dx=dx)

    # -- keyboard --
    def type(self, text: str, strategy: str = "auto", paste_combo: str = "ctrl+v",
             delay: float = 0.012) -> str:
        return type_text(self.keyboard, text, strategy, paste_combo, delay)

    def key(self, combo: str, count: int = 1, delay: float = 0.05) -> None:
        mods, code = keymap.parse_combo(combo)
        for i in range(count):
            if i:
                time.sleep(delay)
            if mods:
                self.keyboard.chord(mods, code)
            else:
                self.keyboard.tap(code)

    # -- finding and clicking controls --
    def find(self, name=None, role=None, app=None, **kw) -> list[Element]:
        return a11y.find(name=name, role=role, app=app, **kw)

    def wait_for(self, name=None, role=None, app=None, timeout=10.0, **kw) -> Element:
        return a11y.wait_for(name=name, role=role, app=app, timeout=timeout, **kw)

    def click(self, name: str | None = None, role: str | None = None,
              app: str | None = None, method: str = "auto", index: int = 0,
              button: str = "left", count: int = 1, exact: bool = False) -> ClickResult:
        """Click a control found by name/role.

        method 'pointer' aims the real cursor (faithful: triggers hover, works
        on any toolkit), 'action' invokes the accessible action (reaches
        controls that are covered or scrolled off screen), 'auto' prefers the
        pointer when the control is on screen.
        """
        hits = a11y.find(name=name, role=role, app=app, exact=exact)
        if not hits:
            raise A11yError(
                f"no control matching name={name!r} role={role!r} app={app!r}")
        if index >= len(hits):
            raise A11yError(f"only {len(hits)} matches; index {index} out of range")
        el = hits[index]
        on_screen = (el.coords_trusted
                     and 0 <= el.center[0] < self.width
                     and 0 <= el.center[1] < self.height)
        chosen = method
        if method == "auto":
            # Wayland hides surface positions from applications, so many
            # controls have no usable rectangle; those must be invoked through
            # their accessible action rather than aimed at.
            chosen = "pointer" if (on_screen and el.showing) else "action"
        if chosen == "pointer" and not el.coords_trusted:
            raise A11yError(
                f"{el.role} {el.name!r} does not report its screen position "
                "(a Wayland application that is not told where its window is), "
                "so it cannot be clicked by coordinate. Use method='action', "
                "or take a screenshot and click explicit x/y.")
        if chosen == "pointer":
            cx, cy = el.center
            self.pointer.move_to(cx, cy)
            time.sleep(0.15)
            self.pointer.click(button, count)
            return ClickResult("pointer", el, cx, cy)
        a11y.do_action(el, "click")
        return ClickResult("action", el, *el.center)

    def focus(self, name=None, role=None, app=None) -> bool:
        hits = a11y.find(name=name, role=role, app=app)
        if not hits:
            raise A11yError(f"no control matching name={name!r} role={role!r}")
        return a11y.grab_focus(hits[0])

    # -- windows and apps --
    def windows(self, app: str | None = None) -> list[Element]:
        return a11y.windows(app)

    def focus_window(self, pattern: str) -> str:
        """Best-effort raise of a window whose title or app matches.

        Wayland gives no general "activate this window" call, so this tries
        the accessible focus first and falls back to xdotool, which reaches
        real X11 clients and XWayland ones.
        """
        wins = [w for w in a11y.windows()
                if pattern.lower() in w.name.lower()
                or pattern.lower() in w.app.lower()]
        if not wins:
            raise A11yError(f"no window matching {pattern!r}")
        win = wins[0]
        if a11y.grab_focus(win):
            return f"focused {win.name!r} via accessibility"
        if shutil.which("xdotool"):
            found = subprocess.run(
                ["xdotool", "search", "--name", pattern],
                capture_output=True, text=True)
            ids = found.stdout.split()
            if ids:
                subprocess.run(["xdotool", "windowactivate", ids[-1]],
                               capture_output=True)
                return f"focused {win.name!r} via xdotool"
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11":
            raise A11yError(
                f"could not raise {win.name!r}; install xdotool for window "
                "activation on X11, or click the window or use alt+tab")
        raise A11yError(
            f"could not raise {win.name!r}; Wayland restricts window "
            "activation, so click the window or use alt+tab")

    def launch(self, target: str, wait: float = 0.0) -> str:
        """Start an application by .desktop id or command line."""
        if target.endswith(".desktop") and shutil.which("gtk-launch"):
            subprocess.Popen(["gtk-launch", target],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = f"launched {target} via gtk-launch"
        else:
            subprocess.Popen(target, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = f"launched {target!r}"
        if wait:
            time.sleep(wait)
        return result

    # -- pixels --
    def screenshot(self, path: str | None = None,
                   region: tuple[int, int, int, int] | None = None,
                   window: str | None = None):
        """Capture the screen, a region, or the rectangle of a named window."""
        if window:
            wins = [w for w in a11y.windows()
                    if window.lower() in w.name.lower()
                    or window.lower() in w.app.lower()]
            if not wins:
                raise A11yError(f"no window matching {window!r}")
            w = wins[0]
            region = (w.x, w.y, w.width, w.height)
        img = self.capture.grab()
        if region:
            x, y, rw, rh = region
            x, y = max(0, x), max(0, y)
            img = img.crop((x, y, min(x + rw, img.width), min(y + rh, img.height)))
        if path:
            img.save(path)
        return img

    def close(self) -> None:
        for attr in ("_pointer", "_keyboard", "_capture"):
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
