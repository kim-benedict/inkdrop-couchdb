import importlib.util
import pathlib
import unittest

file = pathlib.Path(__file__).resolve().parents[1] / "server/egress.py"
spec = importlib.util.spec_from_file_location("egress", file)
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)


class EgressTests(unittest.TestCase):
    def test_first_month_is_conservative(self):
        data = None
        for _ in range(egress.FIRST_CAP // egress.CHUNK):
            data = egress.next_reservation(data, "2026-01")
        self.assertEqual(data["reserved"], egress.FIRST_CAP)
        self.assertIsNone(egress.next_reservation(data, "2026-01"))

    def test_restart_cannot_restore_spent_reservations(self):
        data = egress.next_reservation(None, "2026-01")
        again = egress.next_reservation(data, "2026-01")
        self.assertEqual(again["reserved"], 2 * egress.CHUNK)
        self.assertEqual(data["reserved"], egress.CHUNK)

    def test_month_rollover_preserves_installation_date(self):
        data = {
            "month": "2026-01",
            "first_month": "2026-01",
            "reserved": egress.FIRST_CAP,
        }
        current = egress.next_reservation(data, "2026-02")
        self.assertEqual(current["reserved"], egress.CHUNK)
        self.assertEqual(current["first_month"], "2026-01")

    def test_corrupt_ledger_or_backward_clock_is_rejected(self):
        for data in (
            [],
            {},
            {"month": "wrong", "reserved": 0},
            {"month": "2026-02", "first_month": "2026-01", "reserved": True},
        ):
            with self.assertRaises(ValueError):
                egress.next_reservation(data, "2026-01")
        with self.assertRaises(ValueError):
            egress.next_reservation(
                {"month": "2026-02", "first_month": "2026-01", "reserved": 0}, "2026-01"
            )
