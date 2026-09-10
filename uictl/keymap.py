"""Keycode tables and text -> keystroke translation.

uinput sends kernel *keycodes*, which the compositor interprets through the
active XKB layout.  These tables assume a US layout (the layout configured on
this machine).  Text that the layout cannot produce -- accented Vietnamese,
CJK, emoji -- must go through the clipboard instead; see typing.type_text.
"""
from __future__ import annotations

KEY = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11, "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23,
    "o": 24, "p": 25, "leftbrace": 26, "rightbrace": 27, "enter": 28,
    "leftctrl": 29, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38, "semicolon": 39, "apostrophe": 40, "grave": 41,
    "leftshift": 42, "backslash": 43, "z": 44, "x": 45, "c": 46, "v": 47,
    "b": 48, "n": 49, "m": 50, "comma": 51, "dot": 52, "slash": 53,
    "rightshift": 54, "kpasterisk": 55, "leftalt": 56, "space": 57,
    "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "numlock": 69, "scrolllock": 70,
    "f11": 87, "f12": 88,
    "kpenter": 96, "rightctrl": 97, "kpslash": 98, "sysrq": 99, "rightalt": 100,
    "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "pause": 119, "leftmeta": 125, "rightmeta": 126, "compose": 127,
    "f13": 183, "f14": 184, "f15": 185, "f16": 186, "f17": 187, "f18": 188,
    "f19": 189, "f20": 190, "f21": 191, "f22": 192, "f23": 193, "f24": 194,
    "print": 210,
}

# Friendlier aliases accepted in key specs.
ALIAS = {
    "escape": "esc", "return": "enter", "ret": "enter", "del": "delete",
    "ins": "insert", "pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown",
    "ctrl": "leftctrl", "control": "leftctrl", "alt": "leftalt",
    "shift": "leftshift", "meta": "leftmeta", "super": "leftmeta",
    "win": "leftmeta", "cmd": "leftmeta", "spacebar": "space",
    "bs": "backspace", "period": "dot", "-": "minus", "=": "equal",
    "[": "leftbrace", "]": "rightbrace", ";": "semicolon", "'": "apostrophe",
    "`": "grave", "\\": "backslash", ",": "comma", ".": "dot", "/": "slash",
    " ": "space", "prtsc": "print", "printscreen": "print",
}

MODIFIERS = {"leftctrl", "rightctrl", "leftshift", "rightshift", "leftalt",
             "rightalt", "leftmeta", "rightmeta"}

# Characters reachable on a US layout: char -> (key name, needs shift)
_UNSHIFTED = "abcdefghijklmnopqrstuvwxyz0123456789"
CHARS: dict[str, tuple[str, bool]] = {c: (c, False) for c in _UNSHIFTED}
CHARS.update({c.upper(): (c, True) for c in "abcdefghijklmnopqrstuvwxyz"})
CHARS.update({
    " ": ("space", False), "\t": ("tab", False), "\n": ("enter", False),
    "-": ("minus", False), "=": ("equal", False), "[": ("leftbrace", False),
    "]": ("rightbrace", False), ";": ("semicolon", False),
    "'": ("apostrophe", False), "`": ("grave", False),
    "\\": ("backslash", False), ",": ("comma", False), ".": ("dot", False),
    "/": ("slash", False),
    "!": ("1", True), "@": ("2", True), "#": ("3", True), "$": ("4", True),
    "%": ("5", True), "^": ("6", True), "&": ("7", True), "*": ("8", True),
    "(": ("9", True), ")": ("0", True), "_": ("minus", True),
    "+": ("equal", True), "{": ("leftbrace", True), "}": ("rightbrace", True),
    ":": ("semicolon", True), '"': ("apostrophe", True), "~": ("grave", True),
    "|": ("backslash", True), "<": ("comma", True), ">": ("dot", True),
    "?": ("slash", True),
})


class UnknownKey(ValueError):
    pass


def resolve(name: str) -> int:
    """Map a key name (or alias) to its kernel keycode."""
    key = name.strip()
    lowered = key.lower()
    lowered = ALIAS.get(lowered, lowered)
    if lowered in KEY:
        return KEY[lowered]
    raise UnknownKey(f"unknown key {name!r}")


def parse_combo(spec: str) -> tuple[list[int], int]:
    """Parse 'ctrl+shift+t' into ([modifier codes], final keycode)."""
    parts = [p for p in spec.replace(" ", "").split("+") if p]
    if not parts:
        raise UnknownKey("empty key combination")
    # A trailing literal '+' (e.g. 'ctrl++') collapses to nothing above; treat
    # the last empty segment as the plus character.
    if spec.rstrip().endswith("+") and len(parts) >= 1:
        parts.append("=")
    *mods, final = parts
    mod_codes = []
    for m in mods:
        name = ALIAS.get(m.lower(), m.lower())
        if name not in MODIFIERS:
            raise UnknownKey(f"{m!r} is not a modifier")
        mod_codes.append(KEY[name])
    if final in CHARS and CHARS[final][1] and final.lower() not in KEY:
        # e.g. ctrl+? -> needs shift
        keyname, _ = CHARS[final]
        mod_codes.append(KEY["leftshift"])
        return mod_codes, KEY[keyname]
    return mod_codes, resolve(final)


def typeable(text: str) -> bool:
    """True if every character can be produced with the US layout."""
    return all(ch in CHARS for ch in text)
