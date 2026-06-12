"""VoxType-Pille: minimalistisches Always-on-top-Overlay unten-mittig
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
from .audio import rms_level  # noqa: E402
from .state import RUNDIR, state_read  # noqa: E402

BARS = 9
RESULT_SHOW_MS = 3000
BUS_NAME = "io.github.skryx.voxtype.Pill"
KWIN_PLUGIN = "voxtype-pill-follow"
DBUS_XML = """<node><interface name='io.github.skryx.voxtype.Pill'>
<method name='SetActiveOutput'><arg type='s' name='output' direction='in'/></method>
</interface></node>"""

CSS_TEMPLATE = """
.voxtype-pillwin {{ background: transparent; }}
.voxtype-pill {{
    background-color: rgba(16, 16, 22, {op});
    border-radius: 999px;
    padding: {pad_v}px {pad_h}px;
    border: 1px solid rgba(255, 255, 255, 0.06);
}}
.voxtype-pill label {{ color: #e8e8ee; font-size: {font}px; }}
.voxtype-text {{
    background-color: rgba(16, 16, 22, {op});
    border-radius: 12px;
    padding: 6px 14px;
}}
.voxtype-text label {{ color: #cfcfd8; font-size: {font_small}px; }}
"""

KWIN_JS = f"""function voxtypeSend() {{
    callDBus("{BUS_NAME}", "/", "{BUS_NAME}", "SetActiveOutput",
             workspace.activeScreen.name);
}}
workspace.windowActivated.connect(voxtypeSend);
voxtypeSend();
"""


def esc(s):
    return GLib.markup_escape_text(s or "")


def daemon_active():
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "voxtyped"],
        check=False).returncode == 0


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


class PulseDot(Gtk.DrawingArea):
    """Roter Aufnahme-Punkt, der langsam und ganz leicht pulsiert."""

    def __init__(self):
        super().__init__()
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, w, h):
        t = time.monotonic()
        breathe = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 2.4)   # 2,4-s-Atmung
        r = (min(w, h) / 2 - 1.5) * (0.82 + 0.14 * breathe)
        alpha = 0.65 + 0.35 * breathe
        cr.set_source_rgba(1.0, 0.33, 0.33, alpha)
        cr.arc(w / 2, h / 2, r, 0, 2 * math.pi)
        cr.fill()


class Pill(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.skryx.voxtype.pill")
        self.cfg = config.Cfg()
        self.win = None
        self.css = None
        self.wave = None
        self.icon = None
        self.textbox = None
        self.pillbox = None
        self.dot = None
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

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        halign=Gtk.Align.CENTER)
        self.textbox = Gtk.Box()
        self.textbox.add_css_class("voxtype-text")
        self.textlabel = Gtk.Label(wrap=True, max_width_chars=60,
                                   justify=Gtk.Justification.CENTER)
        self.textbox.append(self.textlabel)
        self.textbox.set_visible(False)
        outer.append(self.textbox)

        self.pillbox = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.pillbox.add_css_class("voxtype-pill")
        self.icon = Gtk.Label()
        self.pillbox.append(self.icon)
        self.dot = PulseDot()
        self.dot.set_visible(False)
        self.pillbox.append(self.dot)
        self.wave = Wave()
        self.pillbox.append(self.wave)
        outer.append(self.pillbox)
        self.win.set_child(outer)
        self.apply_style()

        # Linksklick: Diktat an/aus · Rechtsklick: Kontrollzentrum
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
            self.wave.set_content_width(int(54 * s))
            self.wave.set_content_height(int(14 * s))
        if self.dot:
            self.dot.set_content_width(int(14 * s))
            self.dot.set_content_height(int(14 * s))

    def reload_cfg(self):
        if self.cfg.reload():
            self.apply_style()
        self.win.set_visible(self.cfg.pill_enabled)
        return True

    # ------------------------------------------------------------- Zustand
    def set_mode(self, mode, text=""):
        self.mode = mode
        self.wave.active = mode == "recording"
        self.wave.set_visible(mode == "recording")
        self.dot.set_visible(mode == "recording")
        self.icon.set_visible(mode != "recording")
        if mode == "off":
            self.icon.set_markup("<span foreground='#5c5c66' size='small'>●</span>")
            self.textbox.set_visible(False)
        elif mode == "ready":
            self.icon.set_markup("<span foreground='#b9a7f5' size='small'>●</span>")
            self.textbox.set_visible(False)
        elif mode == "recording":
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
            self.wave.level = rms_level()
            self.dot.queue_draw()
        self.wave.tick()
        if self.mode in ("done", "error") and now > self.result_until:
            self.set_mode("ready" if self.on else "off")
        return True

    # --------------------------------------------------------------- Klicks
    def on_left_click(self, *_a):
        if daemon_active():
            subprocess.run(["systemctl", "--user", "stop",
                            "voxtyped", "voxtype-server", "voxtype-ydotoold"],
                           check=False)
            self.on = False
            self.set_mode("off")
        else:
            subprocess.run(["systemctl", "--user", "start",
                            "voxtyped", "voxtype-server"], check=False)
            self.on = True
            self.set_mode("ready")

    def on_right_click(self, *_a):
        subprocess.Popen(["voxtype"])


def main():
    Pill().run()


if __name__ == "__main__":
    main()
