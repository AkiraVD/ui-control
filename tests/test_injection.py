"""Verify injected input lands where we aim it.

Start tests/probe.py first, then run this against the same log file:

    python3 tests/probe.py /tmp/probe.log &
    python3 tests/test_injection.py /tmp/probe.log

Every tile clicked at its centre must report itself as hit, in order.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from uictl import a11y                       # noqa: E402
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


def main(log_path: str) -> int:
    geom, *_ = read_log(log_path)
    if not geom:
        print("no tile geometry in the log -- is tests/probe.py running?")
        return 2

    failures = []
    desk = Desktop()

    # 1. every tile, clicked by coordinate
    order = [f"{c}{r}" for r in (1, 2, 3) for c in "ABCD"]
    for name in order:
        x, y, w, h = geom[name]
        desk.click_at(x + w // 2, y + h // 2)
        time.sleep(0.3)
    _, hits, _, _ = read_log(log_path)
    if hits[:len(order)] != order:
        failures.append(f"coordinate clicks: aimed {order}, hit {hits[:len(order)]}")
    print(f"[{'ok' if not failures else 'FAIL'}] coordinate clicks: "
          f"{len(hits)}/{len(order)} tiles in order")

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
    used_unicode = desk.type(" Xin chào")
    time.sleep(0.8)
    _, _, entries, _ = read_log(log_path)
    typed = entries[-1] if entries else ""
    if "hello uictl 42" not in typed:
        failures.append(f"ascii typing: entry shows {typed}")
    if "chào" not in typed:
        failures.append(f"unicode typing: entry shows {typed} (strategy {used_unicode})")
    print(f"[{'ok' if 'chào' in typed else 'FAIL'}] typing: {typed} "
          f"(ascii via {used_ascii}, unicode via {used_unicode})")

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
