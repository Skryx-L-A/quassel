"""Tests für quassel/mac_app.py — Qt/Prozesse gemockt."""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import mac_app, server_mac


def test_daemon_and_center_commands_use_this_python():
    assert mac_app.daemon_command() == [sys.executable, "-m", "quassel.daemon"]
    assert mac_app.center_command() == [sys.executable, "-m", "quassel.center"]


def test_start_launches_server_and_daemon():
    app = MagicMock()
    mac = mac_app.MacApp(app)
    daemon = MagicMock()
    with patch.object(server_mac, "kill_orphans") as orphans, \
         patch.object(server_mac, "start") as srv, \
         patch.object(subprocess, "Popen", return_value=daemon) as popen:
        mac.start()
    orphans.assert_called_once()     # Reste abgestürzter Läufe zuerst wegräumen
    srv.assert_called_once()
    assert popen.call_args.args[0] == mac_app.daemon_command()
    assert popen.call_args.kwargs["start_new_session"] is True
    assert mac.daemon is daemon
    assert mac.enabled is True


def test_open_center_opens_in_process_with_controller():
    """Das Zentrum muss IM Prozess mit controller=MacApp öffnen — als
    Kindprozess bekäme es controller=None und An/Aus wäre tot."""
    mac = mac_app.MacApp(MagicMock())
    win = MagicMock()
    with patch("quassel.center.Center", return_value=win) as center:
        mac.open_center()
        mac.open_center()   # zweiter Aufruf hebt nur das Fenster an
    center.assert_called_once_with(controller=mac)
    assert win.show.call_count == 2
    win.raise_.assert_called()
    win.activateWindow.assert_called()


def test_toggle_off_stops_daemon_and_syncs_tray_and_pill():
    mac = mac_app.MacApp(MagicMock())
    daemon = MagicMock()
    daemon.poll.return_value = None
    mac.daemon = daemon
    mac.enabled = True
    mac.tray = MagicMock()
    mac.pill = MagicMock()
    with patch.object(server_mac, "terminate_group") as tg:
        mac.toggle()
    tg.assert_called_once_with(daemon, timeout=mac_app.DAEMON_STOP_TIMEOUT)
    assert mac.enabled is False
    assert mac.daemon is None
    mac.tray.set_mode.assert_called_once_with("off")
    mac.pill.set_mode.assert_called_once_with("off")
    assert mac.pill.on is False     # Pille darf state.json nicht mehr lesen


def test_toggle_on_restarts_daemon_and_syncs_ui():
    mac = mac_app.MacApp(MagicMock())
    mac.enabled = False
    mac.tray = MagicMock()
    mac.pill = MagicMock()
    daemon = MagicMock()
    with patch.object(subprocess, "Popen", return_value=daemon) as popen:
        mac.toggle()
    assert popen.call_args.args[0] == mac_app.daemon_command()
    assert popen.call_args.kwargs["start_new_session"] is True
    assert mac.daemon is daemon
    assert mac.enabled is True
    mac.tray.set_mode.assert_called_once_with("ready")
    mac.pill.set_mode.assert_called_once_with("ready")
    assert mac.pill.on is True


def test_center_on_toggle_uses_controller():
    """center.on_toggle muss mit controller den controller schalten —
    nicht in den systemd-No-Op-Pfad laufen."""
    from quassel.center import Center
    fake = MagicMock()
    Center.on_toggle(fake)
    fake.controller.toggle.assert_called_once()
    fake.refresh_status.assert_called_once()


def test_shutdown_stops_daemon_group_then_server():
    mac = mac_app.MacApp(MagicMock())
    daemon = MagicMock()
    daemon.poll.return_value = None
    mac.daemon = daemon
    with patch.object(server_mac, "terminate_group") as tg, \
         patch.object(server_mac, "stop") as srv_stop:
        mac.shutdown()
    tg.assert_called_once_with(daemon, timeout=mac_app.DAEMON_STOP_TIMEOUT)
    srv_stop.assert_called_once()
    assert mac.daemon is None


def test_shutdown_is_idempotent():
    """Signal-Handler UND app.exec()-Ende rufen shutdown — nur der erste
    Aufruf darf etwas tun (Review M1: kein zweiter stop/pkill-Pfad)."""
    mac = mac_app.MacApp(MagicMock())
    daemon = MagicMock()
    daemon.poll.return_value = None
    mac.daemon = daemon
    with patch.object(server_mac, "terminate_group") as tg, \
         patch.object(server_mac, "stop") as srv_stop:
        mac.shutdown()
        mac.shutdown()
        mac.shutdown()
    tg.assert_called_once()
    srv_stop.assert_called_once()


def test_shutdown_reaps_daemon_that_died_on_its_own():
    mac = mac_app.MacApp(MagicMock())
    daemon = MagicMock()
    daemon.poll.return_value = 1
    mac.daemon = daemon
    with patch.object(server_mac, "terminate_group") as tg, \
         patch.object(server_mac, "stop"):
        mac.shutdown()
    tg.assert_not_called()
    daemon.wait.assert_called_once()


def test_quit_shuts_down_and_quits_qt():
    app = MagicMock()
    mac = mac_app.MacApp(app)
    with patch.object(mac, "shutdown") as sd:
        mac.quit()
    sd.assert_called_once()
    app.quit.assert_called_once()


def test_sync_tray_maps_idle_to_ready():
    mac = mac_app.MacApp(MagicMock())
    mac.tray = MagicMock()
    mac.enabled = True
    with patch.object(mac_app, "state_read",
                      return_value={"state": "idle", "text": ""}):
        mac.sync_tray()
    mac.tray.set_mode.assert_called_once_with("ready", "")
    mac.tray.reset_mock()
    with patch.object(mac_app, "state_read",
                      return_value={"state": "recording", "text": "hi"}):
        mac.sync_tray()
    mac.tray.set_mode.assert_called_once_with("recording", "hi")


def test_sync_tray_shows_off_when_disabled():
    """Ausgeschaltet zählt der (letzte) state.json-Inhalt nicht mehr."""
    mac = mac_app.MacApp(MagicMock())
    mac.tray = MagicMock()
    mac.enabled = False
    with patch.object(mac_app, "state_read",
                      return_value={"state": "recording", "text": "hi"}) as sr:
        mac.sync_tray()
    sr.assert_not_called()
    mac.tray.set_mode.assert_called_once_with("off")


def test_sync_tray_without_tray_is_noop():
    mac = mac_app.MacApp(MagicMock())
    mac.tray = None
    mac.sync_tray()   # darf nicht werfen
