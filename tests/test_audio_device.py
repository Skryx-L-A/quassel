"""Default input resolution without opening a microphone."""
from quassel import audio, coreaudio


def test_mac_default_input_is_resolved_for_each_capture(monkeypatch):
    selected = [10]
    names = {10: "Built-in", 20: "Headset"}
    devices = [{"name": "Built-in", "max_input_channels": 1},
               {"name": "Headset", "max_input_channels": 1}]

    class SD:
        def query_devices(self):
            return devices

    monkeypatch.setattr(audio, "_sd", lambda: SD())
    monkeypatch.setattr(coreaudio, "default_input_device", lambda: selected[0])
    monkeypatch.setattr(coreaudio, "device_name", lambda dev: names[dev])
    assert audio._mac_device("default") == 0
    selected[0] = 20
    assert audio._mac_device("default") == 1
