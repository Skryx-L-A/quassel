import hashlib
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import net


def test_hash_map_covers_standard_models_and_vad():
    from quassel import config
    for name in config.MODELS:
        assert "ggml-" + name + ".bin" in net.SHA256
    assert config.VAD_MODEL_FILE in net.SHA256


def test_matches_hash(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(b"safe")
    assert net._matches_hash(p, hashlib.sha256(b"safe").hexdigest())
