"""Fullscreen click/keyboard target grid, used as ground truth for injection.

Shows a labelled grid of tiles.  A tile turns green when clicked and its name
is written to the log, so an injected click at a tile's centre proves the
coordinate mapping end to end -- visible on screen and machine-checkable.
"""
import sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

LOG_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/probe.log"
LOG = open(LOG_PATH, "w", buffering=1)
COLS, ROWS = 4, 3


def log(kind, detail):
    LOG.write(f"{kind} {detail}\n")


CSS = b"""
.tile { background: #2c3550; color: #cbd5f5; font-size: 22px; border: 2px solid #47527a; }
.tile.hit { background: #22c55e; color: #05240f; font-weight: bold; }
.bar { background: #10141f; color: #e5e9f5; font-size: 20px; padding: 10px; }
entry { font-size: 20px; }
"""


class Probe(Gtk.Window):
    def __init__(self):
        super().__init__(title="uictl probe")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.status = Gtk.Label(label="uictl probe — click a tile")
        self.status.get_style_context().add_class("bar")
        outer.pack_start(self.status, False, False, 0)

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=6, column_spacing=6)
        grid.set_margin_start(6); grid.set_margin_end(6)
        grid.set_margin_top(6); grid.set_margin_bottom(6)
        self.tiles = {}
        for r in range(ROWS):
            for c in range(COLS):
                name = f"{chr(ord('A') + c)}{r + 1}"
                btn = Gtk.Button(label=name)
                btn.get_style_context().add_class("tile")
                btn.connect("clicked", self.on_tile, name)
                grid.attach(btn, c, r, 1, 1)
                self.tiles[name] = btn
        outer.pack_start(grid, True, True, 0)

        self.entry = Gtk.Entry(placeholder_text="typing target")
        self.entry.connect("changed",
                           lambda e: log("ENTRY", repr(e.get_text())))
        outer.pack_start(self.entry, False, False, 0)
        self.add(outer)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.SCROLL_MASK)
        self.connect("button-press-event", self.on_press)
        self.connect("scroll-event", self.on_scroll)
        self.connect("key-press-event", self.on_key)
        self.connect("destroy", Gtk.main_quit)
        self.fullscreen()
        # Injected events go to whoever has focus, so the probe is useless
        # unless it is frontmost.  A window manager that honours this keeps it
        # there; Wayland ignores it, which is why test_injection.py checks.
        self.set_keep_above(True)

    def on_press(self, _w, ev):
        log("CLICK", f"button={ev.button} x={int(ev.x_root)} y={int(ev.y_root)}")

    def on_scroll(self, _w, ev):
        log("SCROLL", Gdk.ScrollDirection(ev.direction).value_nick)

    def on_key(self, _w, ev):
        log("KEY", f"{Gdk.keyval_name(ev.keyval)}")

    def on_tile(self, btn, name):
        btn.get_style_context().add_class("hit")
        log("TILE", name)
        self.status.set_text(f"hit {name}")

    def geometry(self):
        """Write each tile's screen rectangle so the injector knows where to aim."""
        for name, btn in self.tiles.items():
            alloc = btn.get_allocation()
            ok, x, y = btn.get_window().get_origin()
            log("GEOM", f"{name} {x + alloc.x} {y + alloc.y} "
                        f"{alloc.width} {alloc.height}")
        log("READY", "probe up")


w = Probe()
w.show_all()
w.entry.grab_focus()
GLib.timeout_add(700, lambda: (w.geometry(), False)[1])
Gtk.main()
