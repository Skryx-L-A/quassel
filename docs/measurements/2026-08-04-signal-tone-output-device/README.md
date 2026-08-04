# Measurement, 4 August 2026: where the signal tones actually came out

The report was simple. The start and stop tones played through the built-in
speakers while the Bluetooth headset was the system output device, and picking
the headset as the microphone in Quassel changed nothing. Picking "System
default" as the microphone changed nothing either. This is where that came from
and why the fix took a different audio path rather than a different device
lookup.

Measured on a MacBook Pro (Apple M5 Pro, macOS 25.5) with the repo venv.
Reproduce with `measure.py` in this folder. It plays no audible sound: the only
audio it ever produces is silence written by a render callback. It changes the
system default output device and restores it in a `finally` block, and the
second device in the transcript below is a temporary aggregate device the script
creates and destroys again — the real headset had disconnected by the time of
the final run, and the measurement needs two outputs to say anything.

## The cause

Quassel played the tones through a `sounddevice` output stream, which is
PortAudio. PortAudio reads the default output device once, when it initialises,
and never asks again. The same goes for the device list.

```
== M1/M2: PortAudio and a change of the system default ==
system default before      : id=72 'MacBook Pro-Lautsprecher'
PortAudio default at start : idx=2 'MacBook Pro-Lautsprecher'
system default now         : id=86 'Quassel Testausgabe'
PortAudio without re-init  : idx=2 'MacBook Pro-Lautsprecher'
PortAudio after re-init    : idx=3 'Quassel Testausgabe'
restored                   : 'MacBook Pro-Lautsprecher'
```

That alone explains the report. The daemon starts at login, with the built-in
speakers as the default; the headset connects later. Everything the app plays
afterwards goes to the speakers, for as long as the process lives.

A device that appears after the process started is worse off than stale — it is
not in the list at all:

```
== M3: does PortAudio see a device that appears later? ==
PortAudio devices at start : 4
aggregate visible without re-init: False
aggregate visible after re-init  : True
```

Re-initialising PortAudio fixes both, and is not usable here. `Pa_Terminate`
tears down every open stream, and the start tone plays while the recording
stream is open — refreshing the device list at that moment would kill the
dictation that just started.

The microphone setting never entered into it. It selects an input device and has
no bearing on where output goes, which is why choosing the headset as the
microphone had no effect.

## The fix, and why this one

CoreAudio's default-output AudioUnit tracks the default device by itself,
including while it is running:

```
== M4: does the default-output AudioUnit follow the change? ==
system default   : id=72 'MacBook Pro-Lautsprecher'
unit playing on  : id=72 'MacBook Pro-Lautsprecher'
render callbacks : 28
system default changed to: id=86 'Quassel Testausgabe'
unit playing on now      : id=86 'Quassel Testausgabe'
still rendering          : True (78 -> 106 callbacks)
restored                 : 'MacBook Pro-Lautsprecher'
```

No enumeration, no re-initialisation, no interference with the recording stream,
and the device follows a headset being plugged in mid-session. The tones now go
through that unit (`quassel/coreaudio.py`), and the warm-keeping that 2.6.0
introduced for Bluetooth is kept: the unit stays open for 60 seconds after a
tone so the radio link does not fall asleep between the start and stop tone.

Two things fall away as a side effect. The unit accepts 16 kHz mono int16
directly and converts internally, so the sample-rate negotiation against the
device is gone. And PortAudio's output-underflow flag is gone with it, because
CoreAudio has no equivalent; in its place the player watches whether the unit is
still asking for data at all, which is the failure that actually happens when a
device disappears underneath it.

## Which device is the right one

The tones follow the system default output. That is where macOS sends every
other sound, and on the machine in the report the headset was both the default
input and the default output — so choosing it as the microphone and expecting
the tones there amounts to the same thing.

The two can come apart: a microphone on one device, the system output on
another. For that case the control center has a **"Play tones on"** setting.
It stays on "System default output" unless changed, and a device selected there
is bound explicitly, which means it deliberately stops following the system.
