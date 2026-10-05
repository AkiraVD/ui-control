"""Command line front end: uictl <command> ..."""
from __future__ import annotations

import argparse
import json
import sys

from . import a11y
from .a11y import A11yError
from .capture import CaptureError
from .clipboard import ClipboardError
from .session import Desktop
from .uinput import UInputError


def _coords(value: str) -> tuple[int, ...]:
    try:
        parts = tuple(int(p) for p in value.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} should be comma separated integers") from None
    return parts


def _emit(args, payload, human: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(human)


def cmd_shot(desk: Desktop, args) -> int:
    region = None
    if args.region:
        if len(args.region) != 4:
            raise SystemExit("--region needs x,y,w,h")
        region = args.region
    img = desk.screenshot(path=args.out, region=region, window=args.window)
    _emit(args, {"path": args.out, "size": list(img.size)},
          f"saved {args.out} ({img.width}x{img.height})")
    return 0


def cmd_click(desk: Desktop, args) -> int:
    if args.at:
        if len(args.at) != 2:
            raise SystemExit("--at needs x,y")
        res = desk.click_at(*args.at, button=args.button, count=args.count)
    else:
        if not (args.name or args.role):
            raise SystemExit("give --name/--role to find a control, or --at x,y")
        res = desk.click(name=args.name, role=args.role, app=args.app,
                         method=args.method, index=args.index,
                         button=args.button, count=args.count, exact=args.exact)
    _emit(args, {"method": res.method, "x": res.x, "y": res.y,
                 "element": res.element.to_dict() if res.element else None},
          str(res))
    return 0


def cmd_move(desk: Desktop, args) -> int:
    x, y = args.xy
    desk.move(x, y)
    _emit(args, {"x": x, "y": y}, f"moved to ({x},{y})")
    return 0


def cmd_drag(desk: Desktop, args) -> int:
    x1, y1 = args.start
    x2, y2 = args.end
    desk.drag(x1, y1, x2, y2, button=args.button)
    _emit(args, {"from": [x1, y1], "to": [x2, y2]},
          f"dragged ({x1},{y1}) -> ({x2},{y2})")
    return 0


def cmd_scroll(desk: Desktop, args) -> int:
    at = tuple(args.at) if args.at else None
    desk.scroll(dy=args.dy, dx=args.dx, at=at)
    _emit(args, {"dy": args.dy, "dx": args.dx, "at": at},
          f"scrolled dy={args.dy} dx={args.dx}")
    return 0


def cmd_type(desk: Desktop, args) -> int:
    used = desk.type(args.text, strategy=args.strategy, paste_combo=args.paste_combo)
    _emit(args, {"typed": args.text, "strategy": used},
          f"typed {len(args.text)} chars via {used}")
    return 0


def cmd_key(desk: Desktop, args) -> int:
    for combo in args.combo:
        desk.key(combo, count=args.count)
    _emit(args, {"keys": args.combo, "count": args.count},
          f"sent {' '.join(args.combo)}")
    return 0


def cmd_find(desk: Desktop, args) -> int:
    hits = desk.find(name=args.name, role=args.role, app=args.app,
                     exact=args.exact, limit=args.limit,
                     visible_only=not args.all)
    _emit(args, [h.to_dict() for h in hits],
          "\n".join(str(h) for h in hits) or "no matches")
    return 0 if hits else 1


def cmd_wait(desk: Desktop, args) -> int:
    el = desk.wait_for(name=args.name, role=args.role, app=args.app,
                       timeout=args.timeout)
    _emit(args, el.to_dict(), str(el))
    return 0


def cmd_tree(desk: Desktop, args) -> int:
    name, node = a11y.find_app(args.app)
    rows = []
    for el in a11y.walk(node, name, max_depth=args.depth):
        if args.interesting and el.role not in a11y.INTERACTIVE_ROLES:
            continue
        if not args.all and (not el.showing or el.width <= 0):
            continue
        rows.append(el)
        if len(rows) >= args.limit:
            break
    _emit(args, [r.to_dict() for r in rows],
          "\n".join(f"{'  ' * (len(r.path) - 1)}{r}" for r in rows) or "empty")
    return 0


def cmd_windows(desk: Desktop, args) -> int:
    wins = desk.windows(args.app)
    _emit(args, [w.to_dict() for w in wins],
          "\n".join(str(w) for w in wins) or "no windows")
    return 0


def cmd_apps(desk: Desktop, args) -> int:
    names = [n for n, _ in a11y.applications()]
    _emit(args, names, "\n".join(sorted(names)))
    return 0


def cmd_focus(desk: Desktop, args) -> int:
    if args.window:
        msg = desk.focus_window(args.window)
    else:
        ok = desk.focus(name=args.name, role=args.role, app=args.app)
        msg = f"focus {'granted' if ok else 'refused'} for {args.name!r}"
    _emit(args, {"result": msg}, msg)
    return 0


def cmd_launch(desk: Desktop, args) -> int:
    msg = desk.launch(args.target, wait=args.wait)
    _emit(args, {"result": msg}, msg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="uictl",
        description="Drive Linux desktop applications: click, type and look "
                    "at the screen.")
    p.add_argument("--json", action="store_true", help="machine readable output")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("shot", help="capture the screen")
    s.add_argument("--out", default="screenshot.png")
    s.add_argument("--region", type=_coords, help="x,y,w,h")
    s.add_argument("--window", help="crop to the window matching this name")
    s.set_defaults(func=cmd_shot)

    s = sub.add_parser("click", help="click a control or a point")
    s.add_argument("--name"); s.add_argument("--role"); s.add_argument("--app")
    s.add_argument("--at", type=_coords, help="x,y")
    s.add_argument("--method", choices=("auto", "pointer", "action"), default="auto")
    s.add_argument("--index", type=int, default=0, help="which match to use")
    s.add_argument("--button", default="left",
                   choices=("left", "right", "middle", "back", "forward"))
    s.add_argument("--count", type=int, default=1, help="2 for a double click")
    s.add_argument("--exact", action="store_true")
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("move", help="move the pointer")
    s.add_argument("xy", type=_coords)
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("drag", help="press, move, release")
    s.add_argument("start", type=_coords); s.add_argument("end", type=_coords)
    s.add_argument("--button", default="left")
    s.set_defaults(func=cmd_drag)

    s = sub.add_parser("scroll", help="scroll the wheel")
    s.add_argument("--dy", type=int, default=0, help="+up / -down")
    s.add_argument("--dx", type=int, default=0)
    s.add_argument("--at", type=_coords, help="x,y to scroll over")
    s.set_defaults(func=cmd_scroll)

    s = sub.add_parser("type", help="type text")
    s.add_argument("text")
    s.add_argument("--strategy", choices=("auto", "keys", "paste"), default="auto")
    s.add_argument("--paste-combo", default="ctrl+v",
                   help="terminals usually need ctrl+shift+v")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("key", help="press key combinations, e.g. ctrl+shift+t")
    s.add_argument("combo", nargs="+")
    s.add_argument("--count", type=int, default=1)
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("find", help="search for controls")
    s.add_argument("--name"); s.add_argument("--role"); s.add_argument("--app")
    s.add_argument("--exact", action="store_true")
    s.add_argument("--all", action="store_true", help="include hidden controls")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("wait", help="wait for a control to appear")
    s.add_argument("--name"); s.add_argument("--role"); s.add_argument("--app")
    s.add_argument("--timeout", type=float, default=10.0)
    s.set_defaults(func=cmd_wait)

    s = sub.add_parser("tree", help="dump an application's control tree")
    s.add_argument("app")
    s.add_argument("--depth", type=int, default=8)
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--all", action="store_true")
    s.add_argument("--interesting", action="store_true",
                   help="only clickable/editable roles")
    s.set_defaults(func=cmd_tree)

    s = sub.add_parser("windows", help="list open windows")
    s.add_argument("--app")
    s.set_defaults(func=cmd_windows)

    s = sub.add_parser("apps", help="list applications on the accessibility bus")
    s.set_defaults(func=cmd_apps)

    s = sub.add_parser("focus", help="focus a control or window")
    s.add_argument("--name"); s.add_argument("--role"); s.add_argument("--app")
    s.add_argument("--window")
    s.set_defaults(func=cmd_focus)

    s = sub.add_parser("launch", help="start an application")
    s.add_argument("target", help=".desktop id or a command line")
    s.add_argument("--wait", type=float, default=0.0)
    s.set_defaults(func=cmd_launch)

    s = sub.add_parser("serve", help="run the MCP server on stdio")
    s.set_defaults(func=None)

    s = sub.add_parser("doctor",
                       help="check whether this machine can be automated")
    s.set_defaults(func=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from .mcp_server import serve
        return serve()
    if args.command == "doctor":
        from .doctor import doctor
        return doctor()
    with Desktop() as desk:
        try:
            return args.func(desk, args)
        except (A11yError, UInputError, CaptureError, ClipboardError,
                ValueError) as exc:
            print(f"uictl: {exc}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
