"""Preflight check: can this machine be automated, and what is missing?

Run `uictl doctor` on a new machine before anything else.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status, check, detail, fix=""):
        self.rows.append((status, check, detail, fix))

    def render(self) -> int:
        width = max(len(c) for _, c, _, _ in self.rows) + 2
        for status, check, detail, fix in self.rows:
            print(f"[{MARK[status]}] {check:<{width}} {detail}")
            if fix and status != OK:
                print(f"{'':<{width + 10}}-> {fix}")
        bad = [r for r in self.rows if r[0] == FAIL]
        warned = [r for r in self.rows if r[0] == WARN]
        print()
        if bad:
            print(f"{len(bad)} blocking problem(s); "
                  f"{len(warned)} warning(s).")
            return 1
        print("ready to automate."
              + (f" {len(warned)} warning(s) above." if warned else ""))
        return 0


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, "", str(exc)


def check_session(rep: Report) -> None:
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    if session == "wayland":
        rep.add(OK, "session", f"{session} on {desktop}")
    elif session == "x11":
        rep.add(OK, "session", f"{session} on {desktop} "
                               "(coordinates are reliable everywhere here)")
    else:
        rep.add(FAIL, "session", f"no graphical session ({session})",
                "uictl needs a desktop session; it cannot run over plain SSH")


def check_uinput(rep: Report) -> None:
    path = "/dev/uinput"
    if not os.path.exists(path):
        rep.add(FAIL, "input (/dev/uinput)", "missing",
                "sudo modprobe uinput  (add 'uinput' to /etc/modules to persist)")
        return
    if os.access(path, os.W_OK):
        rep.add(OK, "input (/dev/uinput)", "writable, no root needed")
        return
    rep.add(FAIL, "input (/dev/uinput)", "exists but is not writable",
            "sudo usermod -aG input $USER && echo 'KERNEL==\"uinput\", "
            "GROUP=\"input\", MODE=\"0660\"' | sudo tee "
            "/etc/udev/rules.d/99-uinput.rules && sudo udevadm control "
            "--reload && reboot")


def check_atspi(rep: Report) -> None:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        desktop = Atspi.get_desktop(0)
        n = desktop.get_child_count()
    except Exception as exc:
        rep.add(FAIL, "accessibility (AT-SPI)", f"unavailable: {exc}",
                "sudo apt install gir1.2-atspi-2.0 at-spi2-core")
        return
    if n <= 0:
        rep.add(WARN, "accessibility (AT-SPI)", "bus reachable but no apps attached",
                "open a GTK app, or enable it: gsettings set "
                "org.gnome.desktop.interface toolkit-accessibility true")
        return
    rich = 0
    for i in range(n):
        try:
            if desktop.get_child_at_index(i).get_child_count() > 0:
                rich += 1
        except Exception:
            pass
    rep.add(OK, "accessibility (AT-SPI)",
            f"{n} application(s) attached, {rich} exposing a tree")


def check_portal(rep: Report) -> None:
    code, out, _ = _run([
        "gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
        "--object-path", "/org/freedesktop/portal/desktop",
        "--method", "org.freedesktop.DBus.Properties.Get",
        "org.freedesktop.portal.ScreenCast", "version"])
    if code != 0:
        rep.add(FAIL, "screen capture (portal)", "ScreenCast portal not answering",
                "sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome "
                "(or -kde/-wlr to match your desktop)")
        return
    version = out.strip("(),<>uint32 ") or "?"
    from .capture import TOKEN_PATH
    remembered = TOKEN_PATH.exists()
    rep.add(OK, "screen capture (portal)",
            f"ScreenCast v{version}; consent "
            + ("remembered" if remembered
               else "not yet given (first capture will show a dialog)"))


def check_gstreamer(rep: Report) -> None:
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        if Gst.ElementFactory.make("pipewiresrc") is None:
            raise RuntimeError("pipewiresrc element missing")
    except Exception as exc:
        rep.add(FAIL, "capture pipeline", f"{exc}",
                "sudo apt install gstreamer1.0-pipewire "
                "gir1.2-gst-plugins-base-1.0")
        return
    rep.add(OK, "capture pipeline", f"GStreamer {Gst.version_string().split()[-1]} "
                                    "with pipewiresrc")


def check_python_deps(rep: Report) -> None:
    missing = []
    for module, package in (("dbus", "python3-dbus"), ("PIL", "python3-pil")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        rep.add(FAIL, "python modules", "missing " + ", ".join(missing),
                "sudo apt install " + " ".join(missing))
    else:
        rep.add(OK, "python modules", f"python {sys.version.split()[0]}, "
                                      "dbus and pillow present")


def check_layout(rep: Report) -> None:
    code, out, _ = _run(["gsettings", "get",
                         "org.gnome.desktop.input-sources", "sources"])
    if code != 0 or not out:
        rep.add(WARN, "keyboard layout", "could not determine layout",
                "typing assumes a US layout; use type --strategy paste otherwise")
        return
    if "'us'" in out:
        rep.add(OK, "keyboard layout", "us (matches the built-in keycode table)")
    else:
        rep.add(WARN, "keyboard layout", f"{out} is not us",
                "keystrokes are interpreted by the active layout; prefer "
                "`type --strategy paste` for anything non-trivial")


def check_screen(rep: Report) -> None:
    from .session import screen_size
    w, h = screen_size()
    rep.add(OK, "screen size", f"{w}x{h}")


def check_extras(rep: Report) -> None:
    bits = []
    for tool, why in (("notify-send", "takeover warnings"),
                      ("wl-copy", "clipboard typing on Wayland"),
                      ("xclip", "clipboard typing on X11"),
                      ("xdotool", "raising XWayland windows")):
        if shutil.which(tool):
            bits.append(tool)
    missing = [t for t in ("notify-send", "wl-copy") if not shutil.which(t)]
    if missing:
        rep.add(WARN, "helpers", "present: " + (", ".join(bits) or "none"),
                "sudo apt install libnotify-bin wl-clipboard")
    else:
        rep.add(OK, "helpers", ", ".join(bits))


def doctor() -> int:
    print("uictl doctor -- checking what this machine supports\n")
    rep = Report()
    for check in (check_session, check_uinput, check_atspi, check_portal,
                  check_gstreamer, check_python_deps, check_screen,
                  check_layout, check_extras):
        try:
            check(rep)
        except Exception as exc:
            rep.add(FAIL, check.__name__.replace("check_", ""),
                    f"check itself failed: {exc}")
    return rep.render()
