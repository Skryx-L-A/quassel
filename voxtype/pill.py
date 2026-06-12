"""VoxType-Pille: minimalistisches Always-on-top-Overlay unten-mittig
(an Wispr Flow orientiert).

Zustände: dezentes Mikro-Symbol (bereit) · rote Wellenform mit echtem
Mikrofon-Pegel + Live-Transkript (Aufnahme) · „…" (Transkription) ·
kurz das Ergebnis. Klick öffnet das Kontrollzentrum.

Größe, Transparenz und Sichtbarkeit kommen aus config.ini ([pill]) und
werden live übernommen. Wayland-Overlay über gtk4-layer-shell (KWin/GNOME/
wlroots), Fallback: normales rahmenloses Fenster.
"""
import math
import subprocess
import time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

HAVE_LAYER = False
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
    HAVE_LAYER = True
except (ValueError, ImportError):
    pass

from . import config  # noqa: E402
from .audio import rms_level  # noqa: E402
from .state import state_read  # noqa: E402

BARS = 9
RESULT_SHOW_MS = 3000

CSS_TEMPLATE = """
.voxtype-pillwin {{ background: transparent; }}
.voxtype-pill {{
    background-color: rgba(16, 16, 22, {op});
    border-radius: 999px;
    padding: {pad_v}px {pad_h}px;
}}
.voxtype-pill label {{ color: #e8e8ee; font-size: {font}px; }}
.voxtype-text {{
    background-color: rgba(16, 16, 22, {op});
    border-radius: 12px;
    padding: 6px 14px;
}}
.voxtype-text label {{ color: #cfcfd8; font-size: {font_small}px; }}
"""


def esc(s):
    return GLib.markup_escape_text(s or "")


class Wave(Gtk.DrawingArea):
    """Kleine Wellenform-Anzeige, gespeist vom echten Mikrofon-Pegel."""

    def __init__(self):
        super().__init__()
        self.levels = [0.0] * BARS
        self.level = 0.0
        self.active = False
        self.set_draw_func(self.draw)

    def tick(self):
        target = self.level if self.active else 0.0
        t = time.monotonic()
        for i in range(BARS):
            # mittige Balken reagieren stärker, leichte Phasen-Wellen obendrauf
            weight = 0.45 + 0.55 * math.cos((i - BARS // 2) / BARS * 2.2) ** 2
            wobble = 0.12 * math.sin(t * 9 + i * 1.7) * (target > 0.02)
            goal = max(0.06, min(1.0, target * weight * 1.6 + wobble))
            self.levels[i] += (goal - self.levels[i]) * 0.45
        self.queue_draw()

    def draw(self, _area, cr, w, h):
        bar_w = w / (BARS * 1.7)
        gap = bar_w * 0.7
        x = (w - BARS * bar_w - (BARS - 1) * gap) / 2
        cr.set_source_rgba(1.0, 0.36, 0.36, 1.0)
        for lvl in self.levels:
            bh = max(2.0, lvl * h)
            cr.rectangle(x, (h - bh) / 2, bar_w, bh)
            cr.fill()
            x += bar_w + gap


class Pill(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.skryx.voxtype.pill")
        self.cfg = config.Cfg()
        self.win = None
        self.css = None
        self.wave = None
        self.icon = None
        self.textbox = None
        self.last_ts = None
        self.result_until = 0.0
        self.mode = "ready"

    def do_activate(self):
        if self.win:
            return
        self.win = Gtk.Window(application=self, decorated=False, resizable=False)
        self.win.add_css_class("voxtype-pillwin")
        if HAVE_LAYER:
            LayerShell.init_for_window(self.win)
            LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self.win, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_margin(self.win, LayerShell.Edge.BOTTOM, 36)

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            self.win.get_display(), self.css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_style()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        halign=Gtk.Align.CENTER)
        self.textbox = Gtk.Box()
        self.textbox.add_css_class("voxtype-text")
        self.textlabel = Gtk.Label(wrap=True, max_width_chars=60,
                                   justify=Gtk.Justification.CENTER)
        self.textbox.append(self.textlabel)
        self.textbox.set_visible(False)
        outer.append(self.textbox)

        pill = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        pill.add_css_class("voxtype-pill")
        self.icon = Gtk.Label()
        pill.append(self.icon)
        self.wave = Wave()
        pill.append(self.wave)
        outer.append(pill)
        self.win.set_child(outer)
        self.apply_style()  # jetzt mit existierender Wellenform skalieren

        click = Gtk.GestureClick()
        click.connect("released", self.on_click)
        self.win.add_controller(click)

        self.hold()
        self.set_mode("ready")
        GLib.timeout_add(80, self.tick)
        GLib.timeout_add(1000, self.reload_cfg)
        self.win.present()

    # ------------------------------------------------------------ Aussehen
    def apply_style(self):
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        css = CSS_TEMPLATE.format(op=op, pad_v=int(5 * s), pad_h=int(12 * s),
                                  font=int(12 * s), font_small=int(11 * s))
        self.css.load_from_data(css.encode())
        if self.wave:
            self.wave.set_content_width(int(54 * s))
            self.wave.set_content_height(int(14 * s))

    def reload_cfg(self):
        if self.cfg.reload():
            self.apply_style()
        self.win.set_visible(self.cfg.pill_enabled)
        return True

    # ------------------------------------------------------------ Zustand
    def set_mode(self, mode, text=""):
        self.mode = mode
        self.wave.active = mode == "recording"
        self.wave.set_visible(mode == "recording")
        if mode == "ready":
            self.icon.set_markup("<span foreground='#8a8a96' size='small'>●</span>")
            self.textbox.set_visible(False)
        elif mode == "recording":
            self.icon.set_markup("<span foreground='#ff5c5c'>●</span>")
            self.show_text(text, italic=True)
        elif mode == "transcribing":
            self.icon.set_markup("<span foreground='#e8e8ee'>…</span>")
        elif mode == "done":
            self.icon.set_markup("<span foreground='#7ddf7d'>✓</span>")
            self.show_text(text)
            self.result_until = time.monotonic() + RESULT_SHOW_MS / 1000
        elif mode == "error":
            self.icon.set_markup("<span foreground='#ff8888'>✕</span>")
            self.show_text(text)
            self.result_until = time.monotonic() + RESULT_SHOW_MS / 1000

    def show_text(self, text, italic=False):
        if not text:
            self.textbox.set_visible(False)
            return
        short = text if len(text) <= 140 else "…" + text[-139:]
        markup = f"<i>{esc(short)}</i>" if italic else esc(short)
        self.textlabel.set_markup(markup)
        self.textbox.set_visible(True)

    def tick(self):
        st = state_read()
        if st.get("ts") != self.last_ts:
            self.last_ts = st.get("ts")
            self.set_mode(st.get("state", "idle") if st.get("state") != "idle"
                          else "ready", st.get("text", ""))
        if self.mode == "recording":
            self.wave.level = rms_level()
        self.wave.tick()
        if self.mode in ("done", "error") and time.monotonic() > self.result_until:
            self.set_mode("ready")
        return True

    def on_click(self, *_a):
        subprocess.Popen(["voxtype"])


def main():
    Pill().run()


if __name__ == "__main__":
    main()
