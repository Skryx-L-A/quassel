"""Measurement: which output device do the signal tones actually reach on macOS?

Four questions, four measurements. Nothing here plays an audible sound — the
only audio that ever leaves this script is silence written by a render callback.

  M1  Does PortAudio (sounddevice) follow a change of the system default
      output device while the process is running?
  M2  Does it follow after Pa_Terminate/Pa_Initialize?
  M3  Does an already initialised PortAudio see a device that appears later?
  M4  Does the CoreAudio default-output AudioUnit follow a change while it is
      running?

M1, M2 and M4 change the system default output device and restore it in a
finally block. M3 creates a private aggregate device, visible only to this
process, and destroys it again. Run it with:

    .venv/bin/python3 docs/measurements/2026-08-04-signal-tone-output-device/measure.py

Needs the repo venv (sounddevice, pyobjc). Device names in the committed
transcript (README.md) are replaced with neutral labels.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from quassel import coreaudio


def outputs():
    return coreaudio.output_devices()


def name(dev):
    return coreaudio.device_name(dev)


def m1_m2():
    print("== M1/M2: PortAudio and a change of the system default ==")
    import sounddevice as sd

    before = coreaudio.default_output_device()
    print("system default before      : id=%s %r" % (before, name(before)))
    print("PortAudio default at start : idx=%s %r"
          % (sd.default.device[1], sd.query_devices(sd.default.device[1])["name"]))

    other = [dev for dev, _n in outputs() if dev != before]
    if not other:
        print("only one output device - skipped")
        return
    target = other[0]
    try:
        coreaudio._set_default_output(target)
        time.sleep(0.3)
        print("system default now         : id=%s %r"
              % (coreaudio.default_output_device(), name(target)))
        print("PortAudio without re-init  : idx=%s %r"
              % (sd.default.device[1],
                 sd.query_devices(sd.default.device[1])["name"]))
        sd._terminate()
        sd._initialize()
        print("PortAudio after re-init    : idx=%s %r"
              % (sd.default.device[1],
                 sd.query_devices(sd.default.device[1])["name"]))
    finally:
        coreaudio._set_default_output(before)
        time.sleep(0.3)
        print("restored                   : %r"
              % name(coreaudio.default_output_device()))


def m3():
    print("\n== M3: does PortAudio see a device that appears later? ==")
    import sounddevice as sd
    from CoreAudio import (AudioHardwareCreateAggregateDevice,
                           AudioHardwareDestroyAggregateDevice)

    sub = outputs()
    if not sub:
        print("no output device - skipped")
        return
    # UID of the first output device, as the sub-device of the aggregate
    uid = _device_uid(sub[0][0])
    print("PortAudio devices at start : %d" % len(sd.query_devices()))
    err, dev = AudioHardwareCreateAggregateDevice(
        {"name": "QuasselProbe", "uid": "de.skryx.quassel.probe",
         "private": 1, "subdevices": [{"uid": uid}]}, None)
    if err:
        print("aggregate could not be created (err=%s) - skipped" % err)
        return
    try:
        names = [d["name"] for d in sd.query_devices()]
        print("aggregate visible without re-init: %s" % ("QuasselProbe" in names))
        sd._terminate()
        sd._initialize()
        names = [d["name"] for d in sd.query_devices()]
        print("aggregate visible after re-init  : %s" % ("QuasselProbe" in names))
    finally:
        AudioHardwareDestroyAggregateDevice(dev)


def _device_uid(dev):
    import ctypes
    libs = coreaudio._libs()
    addr = coreaudio._Addr(coreaudio._fourcc("uid "), coreaudio._SCOPE_GLOBAL, 0)
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    libs[1].AudioObjectGetPropertyData(ctypes.c_uint32(dev), ctypes.byref(addr),
                                       0, None, ctypes.byref(size),
                                       ctypes.byref(ref))
    buf = ctypes.create_string_buffer(512)
    libs[2].CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                           ctypes.c_long, ctypes.c_uint32]
    libs[2].CFStringGetCString(ref, buf, 512, coreaudio._UTF8)
    libs[2].CFRelease(ctypes.c_void_p(ref.value))
    return buf.value.decode("utf-8")


def m4():
    print("\n== M4: does the default-output AudioUnit follow the change? ==")
    calls = {"n": 0}

    def render(out):
        calls["n"] += 1
        out[:] = b"\x00" * len(out)          # silence, nothing else

    before = coreaudio.default_output_device()
    other = [dev for dev, _n in outputs() if dev != before]
    if not other:
        print("only one output device - skipped")
        return
    target = other[0]
    unit = coreaudio.DefaultOutputUnit(render, 16000, None)
    unit.start()
    try:
        time.sleep(0.3)
        print("system default   : id=%s %r" % (before, name(before)))
        print("unit playing on  : id=%s %r"
              % (unit.current_device(), name(unit.current_device())))
        print("render callbacks : %d" % calls["n"])

        coreaudio._set_default_output(target)
        time.sleep(0.6)
        print("system default changed to: id=%s %r" % (target, name(target)))
        print("unit playing on now      : id=%s %r"
              % (unit.current_device(), name(unit.current_device())))
        n1 = calls["n"]
        time.sleep(0.3)
        print("still rendering          : %s (%d -> %d callbacks)"
              % (calls["n"] > n1, n1, calls["n"]))
    finally:
        coreaudio._set_default_output(before)
        time.sleep(0.4)
        print("restored                 : %r"
              % name(coreaudio.default_output_device()))
        unit.close()


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("macOS only")
    print("output devices: %s" % [n for _d, n in outputs()])
    m1_m2()
    m3()
    m4()
