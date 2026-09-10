"""uictl -- drive Linux desktop applications from code or from Claude.

Built for Wayland (GNOME) but the input layer works anywhere Linux evdev does.
"""
from .session import Desktop, ClickResult, screen_size
from .a11y import Element, A11yError, find, wait_for, windows, applications
from .uinput import UInputError

__version__ = "0.1.0"
__all__ = [
    "Desktop", "ClickResult", "Element", "A11yError", "UInputError",
    "screen_size", "find", "wait_for", "windows", "applications",
]
