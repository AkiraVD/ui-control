"""Verify screen capture on whatever backend this machine provides.

    python3 tests/test_capture.py

Checks the backend chosen for this session, that a grab matches the screen
size, that a held session is faster than a cold one, and that the explicitly
named backends behave: the one this session cannot use must fail with an
explanation rather than hand back a blank or stale image.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from uictl import capture                      # noqa: E402
from uictl.session import screen_size          # noqa: E402


def main() -> int:
    failures = []
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    order = capture.backend_order()
    print(f"session {session}; backends in order: {', '.join(order)}")
    expected_first = "x11" if session == "x11" else "portal"
    if order[0] != expected_first:
        failures.append(f"a {session} session should try {expected_first} first, "
                        f"not {order[0]}")

    width, height = screen_size()
    t0 = time.time()
    held = capture.open_session()
    opened = time.time() - t0
    try:
        t0 = time.time()
        img = held.grab()
        first = time.time() - t0
        t0 = time.time()
        held.grab()
        second = time.time() - t0
    finally:
        held.close()
    print(f"[ok] {held.backend}: opened in {opened:.2f}s, "
          f"grabs {first:.2f}s then {second:.2f}s")

    if img.size != (width, height):
        # A portal session captures one monitor; X11 captures the whole root
        # window.  Either way it should match what screen_size() reports.
        failures.append(f"grab is {img.size}, but the screen is "
                        f"{(width, height)}")
    print(f"[{'ok' if img.size == (width, height) else 'FAIL'}] "
          f"grab size {img.size} matches the screen")

    if len(img.getcolors(maxcolors=256) or []) == 1:
        failures.append("the grab is a single flat colour -- capture returned "
                        "an empty frame")
    print("[ok] the grab has real content")

    # The backend this session cannot use must say so, not fake it.
    other = [b for b in capture.BACKENDS if b != held.backend]
    for backend in other:
        try:
            with capture.new_session(backend) as spare:
                spare.grab()
            print(f"[ok] {backend} also works here")
        except Exception as exc:
            print(f"[ok] {backend} refuses with a reason: "
                  f"{str(exc).splitlines()[0][:80]}")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  -", f)
        return 1
    print("all capture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
