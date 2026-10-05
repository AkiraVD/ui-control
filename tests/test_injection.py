"""Verify injected input lands where we aim it.

Start tests/probe.py first, then run this against the same log file:

    python3 tests/probe.py /tmp/probe.log &
    python3 tests/test_injection.py /tmp/probe.log

Every tile clicked at its centre must report itself as hit, in order.

The probe has to be the frontmost window.  Injected events go to whoever has
focus, so a probe sitting behind a terminal swallows nothing and reports
nothing -- which reads as twelve failed clicks when the injection path is
perfectly healthy.  Wayland offers no way to raise a window, so this checks
before testing and says what to do rather than blaming the code.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from uictl import a11y                       # noqa: E402
from uictl.clipboard import ClipboardError   # noqa: E402
from uictl.session import Desktop            # noqa: E402


def read_log(path):
    geom, tiles, entries, scrolls = {}, [], [], []
    for line in open(path):
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "GEOM":
            geom[parts[1]] = tuple(map(int, parts[2:6]))
        elif parts[0] == "TILE":
            tiles.append(parts[1])
        elif parts[0] == "ENTRY":
            entries.append(line.split(" ", 1)[1].strip())
        elif parts[0] == "SCROLL":
            scrolls.append(parts[1])
    return geom, tiles, entries, scrolls


PROBE_TITLE = "uictl probe"

NOT_FRONTMOST = """\
the probe is not receiving injected input, because it is not the frontmost
window{whose}.  Every event below would land in whatever is, so these checks
would report a dozen false failures while injection is in fact fine.

Wayland has no call to raise a window -- `uictl focus_window` refuses for the
same reason -- so pick one:

  * click the probe window once to focus it, then re-run this; or
  * restart the probe under XWayland, which can then be raised automatically:

        GDK_BACKEND=x11 python3 tests/probe.py {log} &
        python3 tests/test_injection.py {log}
"""


def active_window_name() -> str | None:
    """Title of the focused window, or None when that cannot be determined.

    xdotool only sees X11 and XWayland clients, so a native Wayland probe is
    simply invisible to it.  None means "cannot tell", not "not focused".
    """
    if not shutil.which("xdotool"):
        return None
    try:
        got = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return None
    return got.stdout.strip() if got.returncode == 0 else None


def raise_probe() -> bool:
    """Best effort: bring the probe to the front.  Only XWayland/X11 can."""
    if not shutil.which("xdotool"):
        return False
    try:
        found = subprocess.run(["xdotool", "search", "--name", PROBE_TITLE],
                               capture_output=True, text=True, timeout=5)
        ids = found.stdout.split()
        if not ids:
            return False
        subprocess.run(["xdotool", "windowactivate", ids[-1]],
                       capture_output=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return False
    time.sleep(0.6)
    return active_window_name() == PROBE_TITLE


def receives_input(desk, geom, log_path) -> bool:
    """Click one tile and see whether the probe logged it.

    The authoritative check: window-manager queries cannot answer this on
    Wayland, but a tile that lights up proves the whole path end to end.  It
    costs one stray click when the probe is not focused, which is the point --
    one is cheap, the twelve that follow are not.
    """
    before = len(read_log(log_path)[1])
    x, y, w, h = geom["A1"]
    desk.click_at(x + w // 2, y + h // 2)
    time.sleep(0.4)
    return len(read_log(log_path)[1]) > before


def probe_is_ready(desk, geom, log_path) -> tuple[bool, str]:
    """Confirm the probe is frontmost, raising it if this session allows.

    Returns (ready, note describing who holds focus instead).
    """
    active = active_window_name()
    if active == PROBE_TITLE:
        return True, ""
    if active is None and receives_input(desk, geom, log_path):
        # Nothing could tell us who is focused, but the probe answered.
        return True, ""
    if raise_probe():
        return True, ""
    whose = f" ({active!r} is)" if active else ""
    return False, whose


def main(log_path: str) -> int:
    geom, *_ = read_log(log_path)
    if not geom:
        print("no tile geometry in the log -- is tests/probe.py running?")
        return 2

    failures = []
    desk = Desktop()

    # 0. the probe must own the focus, or everything below tests the wrong app
    ready, whose = probe_is_ready(desk, geom, log_path)
    if not ready:
        desk.close()
        print(NOT_FRONTMOST.format(whose=whose, log=log_path))
        return 2

    # 1. every tile, clicked by coordinate
    order = [f"{c}{r}" for r in (1, 2, 3) for c in "ABCD"]
    baseline = len(read_log(log_path)[1])   # the preflight may have hit a tile
    for name in order:
        x, y, w, h = geom[name]
        desk.click_at(x + w // 2, y + h // 2)
        time.sleep(0.3)
    _, hits, _, _ = read_log(log_path)
    clicked = hits[baseline:baseline + len(order)]
    if clicked != order:
        failures.append(f"coordinate clicks: aimed {order}, hit {clicked}")
    print(f"[{'ok' if clicked == order else 'FAIL'}] coordinate clicks: "
          f"{len(clicked)}/{len(order)} tiles in order")

    # 2. clicking by accessible name, without the pointer
    before = len(hits)
    matches = a11y.find(name="B2", role="push button", app="probe", exact=True)
    if not matches:
        failures.append("accessibility: probe exposed no button named B2")
    else:
        a11y.do_action(matches[0])
        time.sleep(0.5)
        _, hits, _, _ = read_log(log_path)
        if len(hits) <= before or hits[-1] != "B2":
            failures.append(f"action click: expected B2, log ends {hits[-3:]}")
        print(f"[{'ok' if hits[-1] == 'B2' else 'FAIL'}] action click reached B2 "
              "without moving the cursor")

    # 3. typing, including text the layout cannot produce
    entry = a11y.find(role="^text$", app="probe")
    if entry:
        a11y.grab_focus(entry[0])
        time.sleep(0.3)
    used_ascii = desk.type("hello uictl 42")
    time.sleep(0.4)
    try:
        used_unicode = desk.type(" Xin chào")
    except ClipboardError as exc:
        # No clipboard tool for this session.  That is a missing package, not
        # a broken injection path, and refusing beats pasting stale text.
        used_unicode = None
        print(f"[ skip ] unicode typing: {exc}")
    time.sleep(0.8)
    _, _, entries, _ = read_log(log_path)
    typed = entries[-1] if entries else ""
    if "hello uictl 42" not in typed:
        failures.append(f"ascii typing: entry shows {typed}")
    if used_unicode is not None and "chào" not in typed:
        failures.append(f"unicode typing: entry shows {typed} (strategy {used_unicode})")
    print(f"[{'ok' if 'hello uictl 42' in typed else 'FAIL'}] typing: {typed} "
          f"(ascii via {used_ascii}, unicode via {used_unicode or 'skipped'})")

    # 4. scrolling
    desk.scroll(dy=-2, at=(960, 540))
    time.sleep(0.4)
    _, _, _, scrolls = read_log(log_path)
    if "down" not in scrolls:
        failures.append(f"scroll: expected a 'down' event, saw {scrolls[-4:]}")
    print(f"[{'ok' if 'down' in scrolls else 'FAIL'}] scroll direction")

    desk.close()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  -", f)
        return 1
    print("all injection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/probe.log"))
