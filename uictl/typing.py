"""Turning text into keystrokes."""
from __future__ import annotations

import time

from . import clipboard, keymap
from .uinput import Keyboard


def type_keystrokes(kbd: Keyboard, text: str, delay: float = 0.012) -> None:
    """Type text one keycode at a time. US-layout characters only."""
    shift = keymap.KEY["leftshift"]
    for ch in text:
        try:
            name, needs_shift = keymap.CHARS[ch]
        except KeyError:
            raise ValueError(
                f"character {ch!r} cannot be typed on a US layout; "
                "use the clipboard strategy instead"
            ) from None
        code = keymap.KEY[name]
        if needs_shift:
            kbd.key(shift, True)
            kbd.tap(code)
            kbd.key(shift, False)
        else:
            kbd.tap(code)
        time.sleep(delay)


def paste_text(kbd: Keyboard, text: str, combo: str = "ctrl+v",
               restore_clipboard: bool = True) -> None:
    """Type text by putting it on the clipboard and pasting.

    This is the only way to enter characters the keyboard layout cannot
    produce (Vietnamese diacritics, CJK, emoji).  Terminals usually need
    ctrl+shift+v instead of ctrl+v.

    Raises ClipboardError when no clipboard tool can serve this session
    rather than pasting, which would enter whatever was copied earlier.
    """
    previous = clipboard.get_text()
    clipboard.set_text(text)
    time.sleep(0.12)  # let the clipboard manager pick up the new offer
    mods, code = keymap.parse_combo(combo)
    kbd.chord(mods, code)
    if restore_clipboard and previous:
        time.sleep(0.25)
        try:
            clipboard.set_text(previous)
        except clipboard.ClipboardError:
            pass


def type_text(kbd: Keyboard, text: str, strategy: str = "auto",
              paste_combo: str = "ctrl+v", delay: float = 0.012) -> str:
    """Enter text, choosing keystrokes or clipboard paste.

    strategy: 'auto' (keystrokes when the layout allows, else paste),
              'keys' (force keystrokes), 'paste' (force clipboard).
    Returns the strategy actually used.
    """
    if strategy == "keys":
        type_keystrokes(kbd, text, delay)
        return "keys"
    if strategy == "paste":
        paste_text(kbd, text, paste_combo)
        return "paste"
    if strategy != "auto":
        raise ValueError(f"unknown strategy {strategy!r}")
    if keymap.typeable(text):
        type_keystrokes(kbd, text, delay)
        return "keys"
    paste_text(kbd, text, paste_combo)
    return "paste"
