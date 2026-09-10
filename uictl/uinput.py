"""Virtual mouse + keyboard via /dev/uinput.

Works on Wayland, X11 and the console because the events are injected at the
kernel evdev layer, below the display server.  Needs write access to
/dev/uinput -- on a normal desktop session systemd's uaccess rules grant that
to the logged-in user, so no root is required.
"""
from __future__ import annotations

import fcntl
import os
import struct
import time

# --- ioctl plumbing ---------------------------------------------------------
_IOC_WRITE = 1


def _IOC(direction: int, typ: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _IO(typ: str, nr: int) -> int:
    return _IOC(0, typ, nr, 0)


def _IOW(typ: str, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, typ, nr, size)


UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)
UI_DEV_SETUP = _IOW("U", 3, 92)      # struct uinput_setup
UI_ABS_SETUP = _IOW("U", 4, 28)      # struct uinput_abs_setup
UI_SET_EVBIT = _IOW("U", 100, 4)
UI_SET_KEYBIT = _IOW("U", 101, 4)
UI_SET_RELBIT = _IOW("U", 102, 4)
UI_SET_ABSBIT = _IOW("U", 103, 4)

# --- event codes (linux/input-event-codes.h) --------------------------------
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
ABS_X, ABS_Y = 0x00, 0x01

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_SIDE, BTN_EXTRA = 0x113, 0x114
BUTTONS = {
    "left": BTN_LEFT,
    "right": BTN_RIGHT,
    "middle": BTN_MIDDLE,
    "back": BTN_SIDE,
    "forward": BTN_EXTRA,
}

_EVENT_FMT = "llHHi"          # struct input_event on 64-bit
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)

UINPUT_PATH = "/dev/uinput"


class UInputError(RuntimeError):
    pass


class _Device:
    """One virtual evdev device."""

    def __init__(self, name: str, vendor: int = 0x1234, product: int = 0x5678):
        try:
            self._fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            raise UInputError(
                "/dev/uinput does not exist -- load the module with "
                "'sudo modprobe uinput'"
            ) from None
        except PermissionError:
            raise UInputError(
                "no write permission on /dev/uinput. On a normal desktop login "
                "systemd grants this to the active user; otherwise add a udev "
                "rule putting it in a group you belong to."
            ) from None
        self.name = name
        self._vendor = vendor
        self._product = product

    # -- setup helpers --
    def _enable(self, request: int, value: int) -> None:
        fcntl.ioctl(self._fd, request, value)

    def _setup_abs(self, code: int, minimum: int, maximum: int) -> None:
        # struct uinput_abs_setup { __u16 code; struct input_absinfo absinfo; }
        # input_absinfo = value, minimum, maximum, fuzz, flat, resolution
        blob = struct.pack("Hxx6i", code, 0, minimum, maximum, 0, 0, 0)
        fcntl.ioctl(self._fd, UI_ABS_SETUP, blob)

    def create(self) -> None:
        # struct uinput_setup { input_id id; char name[80]; __u32 ff_effects_max; }
        setup = struct.pack(
            "HHHH80sI", 0x03, self._vendor, self._product, 1,
            self.name.encode()[:79], 0,
        )
        fcntl.ioctl(self._fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self._fd, UI_DEV_CREATE)
        # Give udev/libinput time to notice the new device, otherwise the first
        # events are delivered before the compositor has opened it and are lost.
        time.sleep(0.25)

    # -- emitting --
    def emit(self, etype: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack(_EVENT_FMT, 0, 0, etype, code, value))

    def sync(self) -> None:
        self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self) -> None:
        if self._fd is not None:
            try:
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None


class Pointer(_Device):
    """Absolute-positioning pointer.

    Declares ABS_X/ABS_Y plus the standard mouse buttons and no touch/pen
    codes, which is the shape libinput classifies as an absolute pointer (the
    same shape a QEMU/VMware USB tablet advertises) so the visible cursor
    follows our coordinates.
    """

    def __init__(self, width: int, height: int, name: str = "uictl virtual pointer"):
        super().__init__(name)
        self.width = width
        self.height = height
        self._enable(UI_SET_EVBIT, EV_KEY)
        for code in BUTTONS.values():
            self._enable(UI_SET_KEYBIT, code)
        self._enable(UI_SET_EVBIT, EV_ABS)
        self._enable(UI_SET_ABSBIT, ABS_X)
        self._enable(UI_SET_ABSBIT, ABS_Y)
        self._enable(UI_SET_EVBIT, EV_REL)
        for code in (REL_WHEEL, REL_HWHEEL):
            self._enable(UI_SET_RELBIT, code)
        self._setup_abs(ABS_X, 0, max(width - 1, 1))
        self._setup_abs(ABS_Y, 0, max(height - 1, 1))
        self.create()

    def move_to(self, x: int, y: int) -> None:
        x = max(0, min(int(x), self.width - 1))
        y = max(0, min(int(y), self.height - 1))
        self.emit(EV_ABS, ABS_X, x)
        self.emit(EV_ABS, ABS_Y, y)
        self.sync()

    def button(self, name: str, pressed: bool) -> None:
        try:
            code = BUTTONS[name]
        except KeyError:
            raise UInputError(
                f"unknown button {name!r}; expected one of {', '.join(BUTTONS)}"
            ) from None
        self.emit(EV_KEY, code, 1 if pressed else 0)
        self.sync()

    def click(self, name: str = "left", count: int = 1, interval: float = 0.06) -> None:
        for i in range(count):
            if i:
                time.sleep(interval)
            self.button(name, True)
            time.sleep(0.02)
            self.button(name, False)

    def scroll(self, dy: int = 0, dx: int = 0) -> None:
        """Scroll by notches; positive dy scrolls up, positive dx scrolls right."""
        for _ in range(abs(dy)):
            self.emit(EV_REL, REL_WHEEL, 1 if dy > 0 else -1)
            self.sync()
            time.sleep(0.01)
        for _ in range(abs(dx)):
            self.emit(EV_REL, REL_HWHEEL, 1 if dx > 0 else -1)
            self.sync()
            time.sleep(0.01)


class Keyboard(_Device):
    def __init__(self, name: str = "uictl virtual keyboard"):
        super().__init__(name)
        self._enable(UI_SET_EVBIT, EV_KEY)
        # Claim the whole standard key range so any keycode can be sent.
        for code in range(1, 249):
            self._enable(UI_SET_KEYBIT, code)
        self.create()

    def key(self, code: int, pressed: bool) -> None:
        self.emit(EV_KEY, code, 1 if pressed else 0)
        self.sync()

    def tap(self, code: int, delay: float = 0.012) -> None:
        self.key(code, True)
        time.sleep(delay)
        self.key(code, False)

    def chord(self, modifiers: list[int], code: int) -> None:
        for m in modifiers:
            self.key(m, True)
        time.sleep(0.012)
        self.tap(code)
        for m in reversed(modifiers):
            self.key(m, False)
