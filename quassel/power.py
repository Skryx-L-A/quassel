"""Sleep/wake event adapters; callbacks contain the platform-independent logic."""
import atexit
import select
import shutil
import subprocess
import sys
import threading


WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007


class PowerEvents:
    def __init__(self, on_sleep, on_wake):
        self.on_sleep = on_sleep
        self.on_wake = on_wake
        self._observer = None
        self._center = None
        self._proc = None
        self._thread = None
        self._stop = threading.Event()

    def dispatch(self, sleeping):
        (self.on_sleep if sleeping else self.on_wake)()

    def windows_message(self, message, event):
        """Call from an existing Windows window procedure; no new window."""
        if message != WM_POWERBROADCAST:
            return False
        if event == PBT_APMSUSPEND:
            self.dispatch(True)
        elif event in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            self.dispatch(False)
        else:
            return False
        return True

    def start(self, platform=None):
        platform = platform or sys.platform
        if platform == "darwin":
            try:
                from AppKit import NSWorkspace, NSWorkspaceWillSleepNotification, NSWorkspaceDidWakeNotification
            except ImportError:
                return False
            self._center = NSWorkspace.sharedWorkspace().notificationCenter()
            self._observer = [
                self._center.addObserverForName_object_queue_usingBlock_(
                    name, None, None, lambda _note, sleeping=sleeping: self.dispatch(sleeping))
                for name, sleeping in ((NSWorkspaceWillSleepNotification, True),
                                       (NSWorkspaceDidWakeNotification, False))]
            return True
        if platform.startswith("linux") and shutil.which("dbus-monitor"):
            try:
                self._proc = subprocess.Popen(
                    ["dbus-monitor", "--system",
                     "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return False
            self._thread = threading.Thread(target=self._linux_loop, daemon=True,
                                            name="power-events")
            self._thread.start()
            return True
        # Windows's existing message loop must call windows_message().
        return False

    def _linux_loop(self):
        proc = self._proc
        while not self._stop.is_set() and proc.poll() is None:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if ready:
                line = proc.stdout.readline()
                if "boolean true" in line:
                    self.dispatch(True)
                elif "boolean false" in line:
                    self.dispatch(False)

    def stop(self):
        self._stop.set()
        if self._center is not None:
            for observer in self._observer or []:
                self._center.removeObserver_(observer)
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        if self._thread is not None:
            self._thread.join(timeout=1)


_monitor = None


def install(core):
    """Install once after first dictation; never touch an unrelated server."""
    global _monitor
    if _monitor is not None:
        return _monitor
    from . import selfheal
    def wake():
        from . import audio
        if sys.platform == "darwin":
            audio._mac_device("default")
        selfheal.recover()
    monitor = PowerEvents(core.panic_stop, wake)
    try:
        started = monitor.start()
    except Exception:
        # A missing or broken optional OS bridge must not lose the dictation.
        monitor.stop()
        return None
    if started:
        _monitor = monitor
        atexit.register(monitor.stop)
    return _monitor
