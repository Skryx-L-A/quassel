"""Profil-Schalter: Standardinstanz unverändert, Profil mit eigenen Pfaden und Port."""
import importlib
import os
import unittest
from unittest import mock


def _reload(env):
    with mock.patch.dict(os.environ, env, clear=False):
        for k in ("QUASSEL_PROFILE", "QUASSEL_PORT"):
            if k not in env:
                os.environ.pop(k, None)
        from quassel import profile, config, state, whisperclient
        for m in (profile, config, state, whisperclient):
            importlib.reload(m)
        return (profile.name(), profile.port(), config.CONFDIR, config.DATADIR,
                config.MODELDIR, state.RUNDIR, whisperclient.SERVER)


class ProfileTest(unittest.TestCase):
    def tearDown(self):
        _reload({})

    def test_default_is_unchanged(self):
        name, port, conf, data, models, run, server = _reload({})
        self.assertEqual(name, "")
        self.assertEqual(port, 8765)
        self.assertEqual(server, "http://127.0.0.1:8765")
        self.assertFalse(conf.lower().endswith("-next"))
        self.assertEqual(models, os.path.join(data, "models"))

    def test_profile_gets_own_dirs_and_port(self):
        base = _reload({})
        name, port, conf, data, models, run, server = _reload({"QUASSEL_PROFILE": "next"})
        self.assertEqual(name, "next")
        self.assertNotEqual(port, 8765)
        self.assertTrue(8766 <= port <= 8865)
        self.assertTrue(conf.endswith("-next"))
        self.assertNotEqual(run, base[5])
        self.assertIn("-next", run)
        self.assertEqual(models, base[4])  # Modelle bleiben geteilt
        self.assertEqual(server, f"http://127.0.0.1:{port}")

    def test_invalid_profile_falls_back(self):
        name, port, *_ = _reload({"QUASSEL_PROFILE": "../evil"})
        self.assertEqual((name, port), ("", 8765))

    def test_port_override(self):
        _, port, *_ = _reload({"QUASSEL_PROFILE": "next", "QUASSEL_PORT": "9123"})
        self.assertEqual(port, 9123)


if __name__ == "__main__":
    unittest.main()
