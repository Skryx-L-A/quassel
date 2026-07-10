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


def test_open_center_spawns_center():
    mac = mac_app.MacApp(MagicMock())
    with patch.object(subprocess, "Popen") as popen:
        mac.open_center()
    assert popen.call_args.args[0] == mac_app.center_command()


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
    with patch.object(mac_app, "state_read",
                      return_value={"state": "idle", "text": ""}):
        mac.sync_tray()
    mac.tray.set_mode.assert_called_once_with("ready", "")
    mac.tray.reset_mock()
    with patch.object(mac_app, "state_read",
                      return_value={"state": "recording", "text": "hi"}):
        mac.sync_tray()
    mac.tray.set_mode.assert_called_once_with("recording", "hi")


def test_sync_tray_without_tray_is_noop():
    mac = mac_app.MacApp(MagicMock())
    mac.tray = None
    mac.sync_tray()   # darf nicht werfen
