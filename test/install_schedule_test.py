import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = pathlib.Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location(
    "install_schedule", SERVER / "install_schedule.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Tests(unittest.TestCase):
    def test_all_examples_and_unit_sandbox(self):
        for path in SERVER.parent.glob("examples/*/job.json"):
            job = installer.load_job(path)
            units = installer.units_for(job)
            service = next(
                value for key, value in units.items() if key.suffix == ".service"
            )
            timer = next(
                value for key, value in units.items() if key.suffix == ".timer"
            )
            self.assertIn("User=inkdrop-todo", service)
            self.assertIn("IPAddressAllow=localhost", service)
            self.assertIn("LoadCredential=storage-status:", service)
            self.assertIn("OnCalendar=" + job["calendar"] + " Asia/Seoul", timer)
            self.assertIn("Persistent=true", timer)

    def test_rejects_unsafe_config_and_template_paths(self):
        original = installer.load_job(SERVER.parent / "examples/daily-todo/job.json")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            directory = root / "job"
            directory.mkdir()
            (directory / "template.md").write_text("* [ ] Test\n")
            (root / "outside.md").write_text("Private content")
            path = directory / "job.json"
            for overrides in [
                {"id": "../../other"},
                {"calendar": "00:00\nUnit=other.service"},
                {"template": "../outside.md"},
                {"timezone": "No/SuchZone"},
            ]:
                path.write_text(json.dumps(dict(original, **overrides)))
                with self.assertRaises((ValueError, KeyError)):
                    installer.load_job(path)
            (directory / "template.md").unlink()
            (directory / "template.md").symlink_to(root / "outside.md")
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError):
                installer.load_job(path)

    def test_refuses_unrelated_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "inkdrop-daily-todo.service"
            path.write_text("[Service]\nExecStart=/bin/other\n")
            with self.assertRaises(ValueError):
                installer.assert_managed(path, "daily-todo")

    def test_failed_install_restores_files_and_timer(self):
        job = installer.load_job(SERVER.parent / "examples/daily-todo/job.json")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            units = root / "units"
            units.mkdir()
            private = root / "private"
            directory = private / job["id"]
            directory.mkdir(parents=True)
            worker = root / "worker.py"
            worker.write_text("old worker")
            config = directory / "config.json"
            config.write_text("old config")
            auth = directory / "auth.json"
            auth.write_text(
                json.dumps({"username": "inkdrop", "password": "synthetic"})
            )
            service = units / "inkdrop-daily-todo.service"
            service.write_text(installer.MARKER + "\nold service")
            timer = units / "inkdrop-daily-todo.timer"
            timer.write_text(installer.MARKER + "\nold timer")
            before = {p: p.read_text() for p in (worker, config, auth, service, timer)}

            def snapshot(paths):
                return {
                    p: (p.read_text(), 0o600) if p.exists() else None for p in paths
                }

            def run(*args):
                if args[:2] == ("systemd-analyze", "verify"):
                    raise RuntimeError("Synthetic verification failure")
                return ""

            with patch.object(installer, "ROOT", private), patch.object(
                installer, "UNITS", units
            ), patch.object(installer, "WORKER", worker), patch.object(
                installer, "require_host"
            ), patch.object(
                installer, "snapshot", side_effect=snapshot
            ), patch.object(
                installer, "state", return_value=True
            ), patch.object(
                installer, "run", side_effect=run
            ) as commands, patch.object(
                installer.pwd, "getpwnam"
            ) as account, patch.object(
                installer, "CouchDB"
            ) as client:
                account.return_value.pw_uid = 999
                account.return_value.pw_shell = "/usr/sbin/nologin"
                client.return_value.request.return_value = {
                    "docs": [{"_id": "book:test", "name": "todo"}]
                }
                with self.assertRaises(RuntimeError):
                    installer.install(job, "http://127.0.0.1:5984/inkdropnotes")
                for p, content in before.items():
                    self.assertEqual(p.read_text(), content)
                commands.assert_any_call(
                    "systemctl", "start", "inkdrop-daily-todo.timer"
                )


if __name__ == "__main__":
    unittest.main()
