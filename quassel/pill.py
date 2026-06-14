"""Quassel-Pille: minimalistisches Always-on-top-Overlay unten-mittig
(an Wispr Flow orientiert).

- Folgt dem aktiven Monitor (KWin-Skript meldet Fensteraktivierung per D-Bus)
- Linksklick: Diktat ein-/ausschalten · Rechtsklick: Kontrollzentrum öffnen
- Zustände: grauer Punkt (aus) · heller Punkt (bereit) · rotes Glühen +
  Wellenform mit echtem Mikrofon-Pegel + Live-Transkript (Aufnahme) ·
  „…" (Transkription) · kurz das Ergebnis
- Größe/Transparenz/Sichtbarkeit aus config.ini ([pill]), live übernommen

Wayland-Overlay über gtk4-layer-shell (KWin/GNOME/wlroots), Fallback:
normales rahmenloses Fenster.
"""
import math
import os
import subprocess
import time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

HAVE_LAYER = False
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
    HAVE_LAYER = True
except (ValueError, ImportError):
    pass

from . import config  # noqa: E402
from .state import RUNDIR, state_read  # noqa: E402

BARS = 9
RESULT_SHOW_MS = 3000
BUS_NAME = "io.github.skryx.quassel.Pill"
KWIN_PLUGIN = "quassel-pill-follow"
DBUS_XML = """<node><interface name='io.github.skryx.quassel.Pill'>
<method name='SetActiveOutput'><arg type='s' name='output' direction='in'/></method>
<method name='GetActiveOutput'><arg type='s' name='output' direction='out'/></method>
</interface></node>"""

CSS_TEMPLATE = """
.quassel-pillwin {{ background: transparent; }}
.quassel-pill {{
    background-color: rgba(17, 32, 26, {op});
    border-radius: 999px;
    padding: {pad_v}px {pad_h}px;
    border: 1px solid rgba(255, 255, 255, 0.05);
}}
.quassel-pill label {{ color: #E7F0EB; font-size: {font}px; }}
.quassel-text {{
    background-color: rgba(17, 32, 26, {op});
    border-radius: 12px;
    padding: 6px 14px;
}}
.quassel-text label {{ color: #BAC8C0; font-size: {font_small}px; }}
"""

# Direction B (Lokal): Pine als Akzent, gedämpftes Grau für "aus", Bernstein für Fehler.
WAVE_PINE = (0.20, 0.76, 0.55)   # #34C18C
WAVE_GRAY = (0.42, 0.47, 0.44)   # #6A786F
WAVE_AMBER = (0.91, 0.66, 0.23)  # #E9A93A

KWIN_JS = f"""function quasselSend() {{
    callDBus("{BUS_NAME}", "/", "{BUS_NAME}", "SetActiveOutput",
             workspace.activeScreen.name);
}}
workspace.windowActivated.connect(quasselSend);
quasselSend();
"""


def esc(s):
    return GLib.markup_escape_text(s or "")


def daemon_active():
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "quasseld"],
        check=False).returncode == 0


class Waveform(Gtk.DrawingArea):
    """Fünf Balken wie auf der Website. Bewegen sich nur bei Aufnahme
    (Sprechen), sonst ruhen sie auf einer leisen, festen Höhe."""

    N = 5
    REST = [0.30, 0.46, 0.38, 0.52, 0.34]   # ruhende Anteile der Höhe

    def __init__(self, get_state):
        super().__init__()
        self.get_state = get_state          # -> (animating: bool, (r,g,b))
        self.set_draw_func(self.draw)

    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def draw(self, _area, cr, w, h):
        animating, (r, g, b) = self.get_state()
        gap = w * 0.11
        bw = (w - gap * (self.N - 1)) / self.N
        t = time.monotonic()
        cr.set_source_rgb(r, g, b)
        for i in range(self.N):
            if animating:
                frac = 0.22 + 0.78 * (0.5 + 0.5 * math.sin(t * 7.5 + i * 1.1))
            else:
                frac = self.REST[i]
            bh = max(2.0, h * frac)
            x = i * (bw + gap)
            y = (h - bh) / 2
            self._rrect(cr, x, y, bw, bh, bw / 2)
            cr.fill()


class Pill(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.skryx.quassel.pill")
        self.cfg = config.Cfg()
        self.win = None
        self.css = None
        self.textbox = None
        self.pillbox = None
        self.wave = None
        self.last_ts = None
        self.result_until = 0.0
        self.mode = "off"
        self.on = False
        self.current_output = None
        self.last_unit_poll = 0.0

    # -------------------------------------------------------------- Aufbau
    def do_activate(self):
        if self.win:
            return
        self.win = Gtk.Window(application=self, decorated=False, resizable=False)
        self.win.add_css_class("quassel-pillwin")
        if HAVE_LAYER:
            LayerShell.init_for_window(self.win)
            LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self.win, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_margin(self.win, LayerShell.Edge.BOTTOM, 36)

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            self.win.get_display(), self.css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        halign=Gtk.Align.CENTER)
        self.textbox = Gtk.Box()
        self.textbox.add_css_class("quassel-text")
        self.textlabel = Gtk.Label(wrap=True, max_width_chars=60,
                                   justify=Gtk.Justification.CENTER)
        self.textbox.append(self.textlabel)
        self.textbox.set_visible(False)
        outer.append(self.textbox)

        self.pillbox = Gtk.Box(halign=Gtk.Align.CENTER)
        self.pillbox.add_css_class("quassel-pill")
        self.wave = Waveform(self.wave_state)
        self.pillbox.append(self.wave)
        outer.append(self.pillbox)
        self.win.set_child(outer)
        self.apply_style()

        # Linksklick: Kontrollzentrum öffnen (sicher) · Rechtsklick: Diktat an/aus.
        # (Früher schaltete Linksklick aus — ein versehentlicher Klick beim Anvisieren
        #  des Textfelds unter der Pille beendete dann mitten im Diktat ganz Quassel.)
        left = Gtk.GestureClick(button=1)
        left.connect("released", self.on_left_click)
        self.win.add_controller(left)
        right = Gtk.GestureClick(button=3)
        right.connect("released", self.on_right_click)
        self.win.add_controller(right)

        self.hold()
        self.on = daemon_active()
        self.set_mode("ready" if self.on else "off")
        GLib.timeout_add(80, self.tick)
        GLib.timeout_add(1000, self.reload_cfg)
        self.setup_monitor_follow()
        self.win.present()

    def do_shutdown(self):
        subprocess.run(["busctl", "--user", "call", "org.kde.KWin", "/Scripting",
                        "org.kde.kwin.Scripting", "unloadScript", "s",
                        KWIN_PLUGIN], check=False, capture_output=True)
        Gtk.Application.do_shutdown(self)

    # --------------------------------------------------- Monitor-Verfolgung
    def setup_monitor_follow(self):
        """KWin meldet bei jeder Fensteraktivierung den aktiven Monitor an
        uns (callDBus aus einem KWin-Skript) — die Pille zieht dorthin um."""
        if not HAVE_LAYER:
            return
        try:
            Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                             Gio.BusNameOwnerFlags.NONE, self.on_bus, None, None)
        except GLib.Error:
            pass

    def on_bus(self, conn, _name):
        conn.register_object(
            "/", Gio.DBusNodeInfo.new_for_xml(DBUS_XML).interfaces[0],
            self.on_dbus_call)
        path = os.path.join(RUNDIR, "kwin-follow.js")
        os.makedirs(RUNDIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(KWIN_JS)
        for args in (["unloadScript", "s", KWIN_PLUGIN],
                     ["loadScript", "ss", path, KWIN_PLUGIN],
                     ["start"]):
            subprocess.run(["busctl", "--user", "call", "org.kde.KWin",
                            "/Scripting", "org.kde.kwin.Scripting", *args],
                           check=False, capture_output=True)

    def on_dbus_call(self, _conn, _sender, _path, _iface, method, params, inv):
        if method == "SetActiveOutput":
            GLib.idle_add(self.move_to_output, params[0])
            inv.return_value(None)
        elif method == "GetActiveOutput":
            inv.return_value(GLib.Variant("(s)", (self.current_output or "",)))

    def move_to_output(self, name):
        if name == self.current_output or not self.win:
            return False
        monitors = self.win.get_display().get_monitors()
        for i in range(monitors.get_n_items()):
            mon = monitors.get_item(i)
            if mon.get_connector() == name:
                self.current_output = name
                visible = self.win.get_visible()
                self.win.set_visible(False)
                LayerShell.set_monitor(self.win, mon)
                if visible and self.cfg.pill_enabled:
                    self.win.present()
                break
        return False

    # ------------------------------------------------------------ Aussehen
    def apply_style(self):
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        css = CSS_TEMPLATE.format(op=op, pad_v=int(5 * s), pad_h=int(12 * s),
                                  font=int(12 * s), font_small=int(11 * s))
        self.css.load_from_data(css.encode())
        if self.wave:
            # feste Größe -> Pille hüpft beim Zustandswechsel nicht
            self.wave.set_content_width(int(22 * s))
            self.wave.set_content_height(int(14 * s))

    def reload_cfg(self):
        if self.cfg.reload():
            self.apply_style()
        self.win.set_visible(self.cfg.pill_enabled)
        return True

    # ------------------------------------------------------------- Zustand
    def wave_state(self):
        """(bewegt sich?, Farbe) für die Wellenform — nur bei Aufnahme bewegt."""
        if self.mode == "recording":
            return True, WAVE_PINE
        if self.mode == "off":
            return False, WAVE_GRAY
        if self.mode == "error":
            return False, WAVE_AMBER
        return False, WAVE_PINE        # ready / transcribing / done: ruhend, Pine

    def set_mode(self, mode, text=""):
        self.mode = mode
        if mode in ("off", "ready"):
            self.textbox.set_visible(False)
        elif mode == "recording":
            self.show_text(text, italic=True)
        elif mode == "transcribing":
            pass                       # Textbox bleibt wie zuletzt (Live-Transkript)
        elif mode in ("done", "error"):
            self.show_text(text)
            self.result_until = time.monotonic() + RESULT_SHOW_MS / 1000
        if self.wave:
            self.wave.queue_draw()

    def show_text(self, text, italic=False):
        if not self.cfg.pill_preview:
            self.textbox.set_visible(False)
            return
        if not text:
            self.textbox.set_visible(False)
            return
        short = text if len(text) <= 140 else "…" + text[-139:]
        markup = f"<i>{esc(short)}</i>" if italic else esc(short)
        self.textlabel.set_markup(markup)
        self.textbox.set_visible(True)

    def tick(self):
        now = time.monotonic()
        # Daemon-Status alle 2 s prüfen (an/aus-Anzeige + Klick-Toggle)
        if now - self.last_unit_poll > 2.0:
            self.last_unit_poll = now
            on = daemon_active()
            if on != self.on:
                self.on = on
                if self.mode in ("off", "ready"):
                    self.set_mode("ready" if on else "off")
        st = state_read()
        if self.on and st.get("ts") != self.last_ts:
            self.last_ts = st.get("ts")
            self.set_mode(st.get("state", "idle") if st.get("state") != "idle"
                          else "ready", st.get("text", ""))
        if self.mode == "recording":
            self.wave.queue_draw()
        if self.mode in ("done", "error") and now > self.result_until:
            self.set_mode("ready" if self.on else "off")
        return True

    # --------------------------------------------------------------- Klicks
    def on_left_click(self, *_a):
        # Sichere Hauptaktion: Kontrollzentrum öffnen (stoppt Quassel NIE).
        subprocess.Popen(["quassel-type"])

    def on_right_click(self, *_a):
        # Bewusste Geste zum An/Aus-Schalten.
        if daemon_active():
            subprocess.run(["systemctl", "--user", "stop",
                            "quasseld", "quassel-server", "quassel-ydotoold"],
                           check=False)
            self.on = False
            self.set_mode("off")
        else:
            subprocess.run(["systemctl", "--user", "start",
                            "quasseld", "quassel-server"], check=False)
            self.on = True
            self.set_mode("ready")


def main():
    Pill().run()


if __name__ == "__main__":
    main()
