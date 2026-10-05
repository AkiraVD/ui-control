# uictl

Drive Linux desktop applications — click, type, and look at the screen — from
the command line, from Python, or from Claude through MCP.

Built and verified on two machines: **Ubuntu 24.04 / GNOME 46 / Wayland** and
**Linux Mint 22.3 / Cinnamon 6.6 / X11**. The input layer works anywhere Linux
evdev does (Wayland, X11, even a bare console); screen capture takes whichever
of two routes the session allows.

Moving to another machine? Run `uictl doctor` there first — it checks every
capability and tells you the exact command to fix whatever is missing.

## Why it is built this way

Wayland deliberately blocks the tricks X11 automation relied on. `xdotool`
still runs here but only sees XWayland clients, and GNOME 46 refuses the old
`org.gnome.Shell.Screenshot` D-Bus method outright. So each capability takes
the route that actually works:

| Need | Mechanism | Notes |
|------|-----------|-------|
| Mouse + keyboard | `/dev/uinput` virtual devices | Below the display server, so every app receives them. No root: systemd grants the seat owner an ACL on `/dev/uinput`. |
| Finding controls | AT-SPI accessibility bus | Gives real names, roles and actions — target "the Send button", not a pixel. |
| Screenshots, Wayland | `xdg-desktop-portal` ScreenCast + PipeWire | The compositor owns the pixels. One consent dialog ever; the restore token makes later captures silent (~0.2 s). |
| Screenshots, X11 | `Gdk` root-window grab | An X server hands any client the screen, so there is nothing to negotiate: no portal, no consent, ~0.03 s. Also the only route on Cinnamon, XFCE and MATE, which ship no ScreenCast portal at all. |
| Text the layout cannot type | clipboard, via `wl-copy` or `xclip`/`xsel` | Picked to match the session — on X11 `wl-copy` has no compositor to talk to, and on Wayland `xclip` only reaches XWayland clients. |

`uictl` chooses each of these at runtime, so the same checkout drives both
machines. `UICTL_CAPTURE=x11|portal` overrides the capture choice.

## Install

Nothing to install on a GNOME or Cinnamon desktop — it uses `python3-gi`,
`python3-dbus` and `python3-pil`, which are already there. If something is
missing:

```bash
sudo apt install python3-gi python3-dbus python3-pil gir1.2-atspi-2.0
sudo apt install gstreamer1.0-pipewire gir1.2-gst-plugins-base-1.0  # Wayland capture
sudo apt install wl-clipboard   # Wayland: typing non-ASCII text
sudo apt install xclip          # X11: typing non-ASCII text
```

Run it straight from the checkout with `./bin/uictl`, or `pip install -e .`
for a `uictl` on your PATH. Where there is no `pip` (Mint 22 ships none for
the system Python), a one-line wrapper does the same job without one:

```bash
printf '#!/bin/sh\nexec %s/bin/uictl "$@"\n' "$PWD" > ~/.local/bin/uictl
chmod +x ~/.local/bin/uictl
```

## Verified environment

Everything below was measured on the two machines this runs on. Nothing here
is a hard requirement except a graphical session and `/dev/uinput`; `uictl
doctor` tells you where a new machine differs.

| | development machine | second machine |
|---|---|---|
| OS | Ubuntu 24.04.5 LTS, kernel 7.0.0-31-generic | Linux Mint 22.3, kernel 6.8.0-51-generic |
| Desktop | GNOME Shell 46.0, **Wayland** (`ubuntu:GNOME`) | Cinnamon 6.6.9, **X11** (`X-Cinnamon`) |
| Hardware | Intel Core i5-12400, 16 GB RAM | Intel Core i5-7300HQ, 16 GB RAM |
| Display | 1920x1080, single monitor | 1920x1080, single monitor |
| Keyboard | XKB layout `us` | XKB layout `us` |
| Python | 3.12.3, PyGObject 3.48.2 | 3.12.3, PyGObject 3.48.2 |
| Accessibility | at-spi 2.52.0 | at-spi 2.52.0 |
| Capture route | portal: xdg-desktop-portal 1.18.4 + -gnome 46.2, ScreenCast v5, PipeWire 1.0.5, GStreamer 1.24.2 | x11: Gdk root window (GTK 3.24.41). Its xdg-desktop-portal 1.20.0 has only the `-gtk` and `-xapp` backends, neither of which does ScreenCast |
| Clipboard | `wl-copy` | `xclip` |

Measured performance, development machine: screenshot 0.2 s cold (new
process, consent already given), 0.01–0.05 s when a session is held open.
Second machine: 0.03 s to open the capture session and 0.03–0.08 s per grab,
with no consent step at all. Virtual device creation is ~0.25 s on both.

## Moving to another machine

`uictl doctor` is the preflight check:

```
[  ok  ] session                  x11 on X-Cinnamon (coordinates are reliable everywhere here)
[  ok  ] input (/dev/uinput)      writable, no root needed
[  ok  ] accessibility (AT-SPI)   36 application(s) attached, 9 exposing a tree
[  ok  ] screen capture           x11: Gdk root window, 1920x1080 (the cursor is never in the image)
[ warn ] capture fallback         portal: no ScreenCast backend answering
[  ok  ] keyboard layout          us (matches the built-in keycode table)
[  ok  ] clipboard                xclip; non-typeable text is pasted through it
```

It names the capture route it will use and the one it would fall back to, so
a missing portal on an X11 desktop reads as a warning rather than a failure.

What differs elsewhere, in rough order of likelihood:

- **`/dev/uinput` not writable.** A desktop login normally gets it by ACL. On
  a server, over SSH, or on a distro without that rule, doctor prints the
  `usermod`/udev rule to fix it. This is the only piece that may need root.
- **A different Wayland desktop.** Capture there goes through the portal, so
  KDE needs `xdg-desktop-portal-kde` and wlroots compositors
  `xdg-desktop-portal-wlr`. Everything else is desktop-agnostic.
- **X11 instead of Wayland.** Everything works and most of it gets *better*:
  capture skips the portal entirely (so Cinnamon, XFCE and MATE need no extra
  package), and AT-SPI reports real screen coordinates for every control, so
  pointer clicking is reliable everywhere and the "position unknown" caveat
  below disappears. Two things to know: the cursor is not in an X11 grab, and
  the grab covers the whole root window rather than one monitor.
- **No clipboard tool.** Non-ASCII text goes through the clipboard, so a
  session with no matching tool refuses to type it rather than pasting
  whatever was copied earlier. `sudo apt install xclip` on X11,
  `wl-clipboard` on Wayland.
- **A non-US keyboard layout.** Keycodes are interpreted by the active
  layout. Doctor warns; use `type --strategy paste` for anything non-trivial.
- **Multiple monitors.** The pointer is set up for the size reported by
  Mutter. Check with `uictl doctor`; pass an explicit size to `Desktop()` if
  a mixed-DPI layout reports something unexpected.
- **Wayland compositor without a ScreenCast portal.** Input and accessibility
  still work; screenshots do not. (On X11 this does not matter — the root
  window route needs no portal.)

## Command line

```bash
uictl doctor                    # can this machine be automated?
uictl apps                      # applications on the accessibility bus
uictl windows                   # open windows with their rectangles
uictl tree gnome-calculator --interesting   # what can I click in this app?
uictl find --name Send --role "push button"

uictl click --name "=" --app gnome-calculator --exact
uictl click --at 960,540        # click a point
uictl click --name Save --method action     # invoke without moving the cursor

uictl type "hello world"        # types; falls back to clipboard for accents
uictl key ctrl+s
uictl scroll --dy -3 --at 960,540
uictl drag 100,100 400,400

uictl shot --out screen.png
uictl shot --window Calculator --out calc.png
uictl wait --name "Save As" --timeout 15
```

Add `--json` to any command for machine-readable output.

## Python

```python
from uictl import Desktop

with Desktop() as d:
    d.launch("gnome-calculator", wait=3)
    for key in ["7", "×", "6", "="]:
        d.click(name=key, app="gnome-calculator", exact=True)
    d.screenshot("result.png")
```

## Use from Claude (MCP)

Registered in user scope, so it is available in any project:

```bash
claude mcp add uictl --scope user -- /path/to/automate-tools/bin/uictl serve
```

Fourteen tools: `screenshot`, `find_elements`, `click`, `type_text`,
`press_key`, `scroll`, `drag`, `move_mouse`, `list_windows`, `list_apps`,
`ui_tree`, `wait_for_element`, `focus_window`, `launch_app`.

`screenshot` hands Claude the image plus the coordinate space to click in, so
it can look at the screen and act on what it sees.

## Safety: it warns before taking over

The first time a session touches the real input devices it raises a desktop
notification and pauses (3 s by default), so you can stop typing before the
cursor moves.

```bash
UICTL_WARN=0 uictl click --at 100,100    # skip the warning
UICTL_WARN_DELAY=8 uictl type hello      # longer grace period
```

## The two ways to click, and when each works

This is the thing to understand before automating anything on Wayland.

**Wayland never tells an application where its own window is.** GTK4 apps
therefore report *every* control at position 0,0 through AT-SPI. Sizes are
right, positions are missing. uictl detects this and shows it honestly:

```
push button '4'    [position unknown, 64x44] @gnome-calculator
push button 'Files' [6,1014 60x66] @gnome-shell     <- real coordinates
```

So there are two click strategies, and `--method auto` picks for you:

- **action** — invokes the control through the accessibility layer. No cursor
  moves. Works even when the control has no known position, is scrolled out of
  view, or is covered by another window. This is what drives GTK/GNOME apps.
- **pointer** — moves the real cursor and clicks. Faithful (triggers hover
  states) and works on any toolkit, but needs a trustworthy coordinate, so it
  is for screenshot-derived points and apps that report real positions.

Asking for `--method pointer` on a control with no known position fails with
an explanation rather than clicking the wrong place.

## What each kind of app supports

Measured on this machine:

| Application | Accessibility tree | How to drive it |
|---|---|---|
| GTK/GNOME (Calculator, Nautilus, Settings, virt-manager) | Full | `find` + `click --name`; positions unknown, so the action path |
| GNOME Shell itself | Full, **with real coordinates** | Either strategy |
| Electron / Qt (Telegram, Viber, vesktop, RustDesk) | **None by default** | `shot` then `click --at x,y` |
| Browsers (Brave/Chrome) | Frame only | Prefer CDP/DevTools over clicking pixels |
| Remote desktop, VM consoles (AnyDesk, Remmina) | Opaque pixels | `shot` then `click --at x,y` |

Chromium and Electron apps expose their tree only when started with
`--force-renderer-accessibility`; without it, use screenshots.

## Known limits

- **Window raising is unreliable.** Wayland has no general "activate this
  window" call. `focus_window` tries accessibility, then XWayland, then tells
  you to click the window or alt-tab instead.
- **Clicks hit whatever is topmost.** An always-on-top window silently
  swallows coordinate clicks aimed underneath it — this cost real debugging
  time during development. The action strategy is immune.
- **Keystrokes go through the active layout** (US on both machines). Text the
  layout cannot produce — Vietnamese diacritics, CJK, emoji — is routed
  through the clipboard automatically, which needs `wl-clipboard` or `xclip`
  to match the session. Terminals need `--paste-combo ctrl+shift+v`.
- **An X11 grab has no cursor in it** and spans every monitor. Crop with
  `shot --window NAME` when one window is what you wanted.
- The pointer device also registers as a joystick (`js1`), a harmless side
  effect of advertising absolute axes.
- Capture needs a graphical session; it cannot run over plain SSH.

## Layout

```
uictl/uinput.py          virtual mouse + keyboard on /dev/uinput
uictl/keymap.py          keycodes, combo parsing, US layout table
uictl/typing.py          text -> keystrokes, or clipboard when it must
uictl/clipboard.py       wl-copy / xclip / xsel, whichever this session can use
uictl/a11y.py            AT-SPI tree: find controls, invoke, coordinate trust
uictl/capture.py         picks a capture backend, hands back a session
uictl/capture_portal.py  ScreenCast portal + PipeWire frames (Wayland)
uictl/capture_x11.py     Gdk root-window grab (X11)
uictl/session.py         Desktop: the API everything else is built on
uictl/cli.py             the uictl command
uictl/mcp_server.py      MCP server for Claude (no SDK dependency)
tests/probe.py           fullscreen click-target grid used to verify injection
tests/test_injection.py  clicks, typing and scrolling against the probe
tests/test_capture.py    capture backend choice and frame sanity
```

## Verifying it still works

`tests/probe.py` is a fullscreen grid of tiles that turn green when clicked
and log which tile was hit, so an injected click at a tile's centre proves the
coordinate mapping end to end.

```bash
python3 tests/probe.py /tmp/probe.log &
python3 tests/test_injection.py /tmp/probe.log
```

`tests/test_capture.py` needs no probe: it checks the backend this session
picked, that a grab matches the screen size and is not a blank frame, and
that the backend this session *cannot* use fails with an explanation instead
of handing back something empty.

```bash
python3 tests/test_capture.py
```
