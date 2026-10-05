# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`uictl` drives Linux desktop applications — click, type, screenshot — from the
CLI, from Python, or from Claude over MCP. README.md is the user-facing
reference (install, per-desktop support matrix, known limits); this file covers
what only shows up when reading several modules together.

## Commands

```bash
./bin/uictl <command>          # run from the checkout, no install needed
./bin/uictl doctor             # ALWAYS run first on a new machine or after env changes
./bin/uictl serve              # MCP server on stdio
python3 -m uictl <command>     # equivalent entry point
```

There are no third-party Python dependencies and no virtualenv: the code
imports system GI bindings (`Atspi`, `Gdk`, `Gst`), `dbus` and `PIL`.
`pip install -e .` only adds a `uictl` on PATH. Mint 22 ships no pip for the
system Python, so `bin/uictl` (which just prepends the repo to `sys.path`) is
the portable way in.

### Tests

Plain scripts, not pytest — each returns a non-zero exit code and prints
`N FAILURE(S)`. There is no lint or typecheck configured.

```bash
python3 tests/test_capture.py                     # safe: no input takeover

python3 tests/probe.py /tmp/probe.log &           # fullscreen click-target grid
python3 tests/test_injection.py /tmp/probe.log    # TAKES OVER mouse + keyboard
```

`test_injection.py` injects real clicks and keystrokes at the real desktop.
Tell the user before running it and let them stop touching the machine. To run
a subset, edit the numbered sections in its `main()` — there is no selection
flag. `probe.py` must already be running; the test reads its log to confirm
each injected event landed on the tile it aimed at.

**The probe must be frontmost** — injected events follow focus, so a probe
behind the terminal makes every check fail while injection is fine. The test's
section 0 preflight catches that and exits 2 with instructions rather than
reporting false failures: it asks `xdotool` who has focus, falls back to a
single canary click when that cannot answer (native Wayland is invisible to
xdotool), and tries `xdotool windowactivate` before giving up. Because the
canary may itself register a tile hit, the coordinate assertion is
baseline-relative — keep it that way when editing section 1.

Env overrides useful while testing: `UICTL_CAPTURE=x11|portal` forces a
capture backend, `UICTL_WARN=0` skips the takeover warning,
`UICTL_WARN_DELAY=8` lengthens the grace period.

## Architecture

Three independent capability layers, composed in exactly one place:

- **input** — `uinput.py` writes `struct input_event` to `/dev/uinput` through
  raw `fcntl.ioctl`, so events enter at the kernel evdev layer and reach every
  app on Wayland, X11 or a bare console. `keymap.py` holds the keycode and
  US-layout character tables; `typing.py` turns text into either keystrokes or
  a clipboard paste; `clipboard.py` picks `wl-copy`/`xclip`/`xsel` by session
  type.
- **semantics** — `a11y.py` walks the AT-SPI tree so callers can target "the
  button called Send" instead of a pixel.
- **pixels** — `capture.py` selects a backend; `capture_x11.py` and
  `capture_portal.py` implement it.

`session.Desktop` is the only module that ties all three together. **Both front
ends are thin wrappers over it**: `cli.py` (argparse, one `cmd_*` per
subcommand) and `mcp_server.py`. A new capability belongs on `Desktop` first,
then gets surfaced in both front ends — they are expected to stay in step.

### Coordinate trust is the central design constraint

Wayland never tells an application where its own surface sits, so GTK4 apps
report *every* control at 0,0 through AT-SPI. `a11y.coords_trusted()` treats a
non-toplevel rectangle at exactly 0,0 as "position unknown", and that single
flag drives `Desktop.click`'s `method="auto"`: trusted + on-screen + showing →
move the real pointer; otherwise → `a11y.do_action()`, which invokes the
accessible action with no cursor involved and so also reaches controls that are
covered or scrolled out of view. Asking for `method="pointer"` on an untrusted
element raises with an explanation rather than clicking the wrong place. Keep
that honesty: never silently fall back to aiming at an untrusted rectangle.

`Element` is a snapshot. `Element.path` is a list of child indices re-resolved
through `find_app()` + `node_at_path()` on every action, so a path goes stale
as soon as the UI changes shape — re-`find()` rather than caching elements
across interactions.

### Capture backends are pluggable

Both backends present the same shape — `start()` / `grab()` / `close()`,
context-manager support, and a `backend` string attribute — so nothing above
`capture.py` knows which one it holds. `backend_order()` picks by
`XDG_SESSION_TYPE` (falling back to the `DISPLAY`/`WAYLAND_DISPLAY` pair, where
Wayland must win because XWayland also sets `DISPLAY`). `open_session()`
collects per-backend errors and only reports them if *every* backend fails — a
Cinnamon desktop with no ScreenCast portal is not an error. Adding a backend
means a new module plus entries in `BACKENDS` and `new_session()`.

### Input takeover warns once, through the lazy properties

`Desktop` creates devices and the capture pipeline on first use and reuses
them, so holding one `Desktop` is much faster than repeated cold starts. The
`pointer` and `keyboard` properties call `_warn_once()`, which raises a desktop
notification and sleeps before the first real event. **Any new code path that
injects input must go through those properties** — constructing `Pointer` or
`Keyboard` directly bypasses the warning.

### Failure modes are deliberate, not defensive

- A missing clipboard tool raises `ClipboardError` instead of pasting: pasting
  without having set the clipboard enters whatever the user copied earlier,
  which looks like success and is not.
- `cli.main()` catches exactly `A11yError`, `UInputError`, `CaptureError`,
  `ClipboardError` and `ValueError` → exit 2 with a one-line message. A new
  exception type needs adding to that tuple or it surfaces as a traceback.
- `a11y._safe()` swallows per-node AT-SPI errors so one wedged app cannot stop
  a tree walk; `Atspi.set_timeout(800, 3000)` and the 20000-node budget in
  `walk()` bound it further.

### MCP server

Hand-rolled JSON-RPC over stdio — no MCP SDK dependency. **stdout carries the
protocol and nothing else**; all diagnostics go through `log()` to stderr. A
`print()` added anywhere reachable from `serve()` corrupts the session. Tools
live in the `TOOLS` list (name, description, `inputSchema`, `handler`); adding
one means appending a dict there. `tool_screenshot` downscales to 1568px and
tells the model the scale factor and that clicks use *screen* coordinates —
keep that note in sync with any resizing change, or the model clicks the wrong
place.

Registered in user scope, so it is live in every project:
`claude mcp add uictl --scope user -- /path/to/automate-tools/bin/uictl serve`.

## Conventions

- `from __future__ import annotations` at the top of every module; modern
  built-in generics in signatures.
- Module docstrings explain *why* the mechanism was chosen (which display
  server blocks what), not what the code does. Comments mark Wayland/X11
  asymmetries and kernel struct layouts. Match that register.
- Error messages name the fix — the package to install, the command to run, or
  the alternative method to use. `doctor.py` is the same idea as a whole
  program: every check carries its own remediation string.
