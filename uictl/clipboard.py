"""The clipboard, through whichever tool this desktop provides.

Characters the keyboard layout cannot produce -- Vietnamese diacritics, CJK,
emoji -- can only be entered by putting them on the clipboard and pasting,
and the command that owns the clipboard depends on the session: wl-clipboard
talks to a Wayland compositor, xclip and xsel talk to an X server, and
neither reaches the other's session.

A missing tool has to be an error.  Pasting without having set the clipboard
enters whatever the user copied earlier, which looks like success and is not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class ClipboardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tool:
    """One clipboard command pair.  Text goes in and out over stdio."""
    name: str
    session: str                 # the session type it can talk to
    package: str                 # what to apt install for it
    copy: tuple[str, ...]
    paste: tuple[str, ...]


TOOLS = (
    Tool("wl-copy", "wayland", "wl-clipboard",
         ("wl-copy",), ("wl-paste", "--no-newline")),
    Tool("xclip", "x11", "xclip",
         ("xclip", "-selection", "clipboard"),
         ("xclip", "-selection", "clipboard", "-o")),
    Tool("xsel", "x11", "xsel",
         ("xsel", "--clipboard", "--input"),
         ("xsel", "--clipboard", "--output")),
)


def session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "").lower()


def installed() -> list[Tool]:
    return [t for t in TOOLS if shutil.which(t.name)]


def package_for_session() -> str:
    """The package to suggest installing on this kind of session."""
    here = session_type()
    for tool_ in TOOLS:
        if tool_.session == here:
            return tool_.package
    return TOOLS[0].package


def tool() -> Tool:
    """The clipboard tool to use here, matching the session first.

    Installed is not enough: on Wayland xclip only reaches XWayland clients,
    and on X11 wl-copy has no compositor to talk to.
    """
    present = installed()
    here = session_type()
    for tool_ in present:
        if tool_.session == here:
            return tool_
    if present and here not in ("wayland", "x11"):
        return present[0]        # unrecognised session: better to try than refuse
    if present:
        raise ClipboardError(
            f"{present[0].name} is installed but only works on a "
            f"{present[0].session} session, and this is {here or 'unknown'}; "
            f"install {package_for_session()}")
    raise ClipboardError(
        "no clipboard tool installed, so text the keyboard layout cannot "
        f"type has no way in; install {package_for_session()}")


def get_text() -> str | None:
    """The current clipboard contents, or None if they cannot be read."""
    try:
        got = subprocess.run(tool().paste, capture_output=True, timeout=3,
                             stdin=subprocess.DEVNULL)
    except (ClipboardError, subprocess.SubprocessError, OSError):
        return None
    if got.returncode != 0:
        return None
    return got.stdout.decode("utf-8", "replace")


def set_text(text: str) -> None:
    """Put text on the clipboard, or raise ClipboardError saying why not.

    Both wl-copy and xclip fork a daemon that keeps serving the selection
    after the command returns, and it inherits our file descriptors.  Detach
    it from our stdio, or a caller piping our output waits forever for EOF.
    """
    cmd = tool().copy
    try:
        subprocess.run(cmd, input=text.encode(), check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       start_new_session=True)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ClipboardError(
            f"{cmd[0]} could not set the clipboard: {exc}") from None
