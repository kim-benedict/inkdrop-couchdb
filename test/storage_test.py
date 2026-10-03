import importlib.util
import pathlib
import unittest

p = pathlib.Path(__file__).resolve().parents[1] / "server/storage.py"
s = importlib.util.spec_from_file_location("safety", p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class GuardTests(unittest.TestCase):
    def test_low_disk_or_large_db_pauses_writes(self):
        self.assertTrue(m.decision(5 * m.GIB, 1 * m.GIB))
        self.assertTrue(m.decision(20 * m.GIB, 8 * m.GIB))

    def test_healthy_server_stays_writable(self):
        self.assertFalse(m.decision(20 * m.GIB, 1 * m.GIB))

    def test_hysteresis_prevents_reload_oscillation(self):
        self.assertTrue(m.decision(int(6.5 * m.GIB), 1 * m.GIB, True))
        self.assertFalse(m.decision(8 * m.GIB, 7 * m.GIB, True))


if __name__ == "__main__":
    unittest.main()
