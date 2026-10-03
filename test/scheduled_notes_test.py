import copy
import datetime
import hashlib
import importlib.util
import pathlib
import unittest
import urllib.error

s = importlib.util.spec_from_file_location(
    "daily", pathlib.Path(__file__).resolve().parents[1] / "server/scheduled_notes.py"
)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
UTC = datetime.timezone.utc
CONFIG = {"book_id": "book:test", "notebook_name": "todo", "timezone": "Asia/Seoul"}


class Client:
    def __init__(self):
        self.notes = {}
        self.writes = 0
        self.name = "todo"
        self.conflict = False

    def request(self, method, suffix, body=None):
        if method == "GET":
            return {"_id": "book:test", "name": self.name}
        if method == "POST":
            match = body["selector"]
            return {
                "docs": [
                    {"_id": n["_id"], "bookId": n["bookId"], "title": n["title"]}
                    for n in self.notes.values()
                    if n["bookId"] == match["bookId"] and n["title"] == match["title"]
                ]
            }
        if self.conflict:
            raise urllib.error.HTTPError(
                "http://127.0.0.1", 409, "conflict", None, None
            )
        self.writes += 1
        self.notes[body["_id"]] = copy.deepcopy(body)
        return {"ok": True, "id": body["_id"]}


def guard(now):
    return {
        "checked_at": now.isoformat(),
        "writes_paused": False,
        "free_bytes": 20 * 1024**3,
        "db_allocated_bytes": 1024,
    }


class Tests(unittest.TestCase):
    def test_korean_midnight_changes_date(self):
        c = Client()
        now = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
        result = m.ensure_today(c, CONFIG, guard(now), now)
        self.assertEqual(result["date"], "2026-10-04")
        self.assertEqual(c.writes, 1)
        doc = next(iter(c.notes.values()))
        self.assertEqual(doc["body"], "* [ ] \n")
        self.assertEqual(doc["share"], "private")

    def test_retries_never_overwrite_a_users_note(self):
        c = Client()
        now = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
        doc = m.note_for("book:test", "2026-10-04", now)
        doc["_id"] = "note:manual"
        doc["body"] = "Personal tasks must survive"
        c.notes[doc["_id"]] = doc
        before = copy.deepcopy(c.notes)
        self.assertEqual(m.ensure_today(c, CONFIG, {}, now)["result"], "already_exists")
        self.assertEqual(c.notes, before)
        self.assertEqual(c.writes, 0)

    def test_twice_is_idempotent(self):
        c = Client()
        now = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
        m.ensure_today(c, CONFIG, guard(now), now)
        m.ensure_today(c, CONFIG, guard(now), now)
        self.assertEqual(c.writes, 1)

    def test_disk_pause_stale_guard_and_renamed_book_fail_before_writing(self):
        now = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
        for g in [
            dict(guard(now), writes_paused=True),
            dict(
                guard(now), checked_at=(now - datetime.timedelta(minutes=6)).isoformat()
            ),
            dict(guard(now), free_bytes=1),
        ]:
            c = Client()
            with self.assertRaises(ValueError):
                m.ensure_today(c, CONFIG, g, now)
            self.assertEqual(c.writes, 0)
        c = Client()
        c.name = "another"
        with self.assertRaises(ValueError):
            m.ensure_today(c, CONFIG, guard(now), now)

    def test_dry_run_and_deleted_note_dont_write(self):
        c = Client()
        now = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
        self.assertEqual(m.ensure_today(c, CONFIG, {}, now, True)["result"], "dry_run")
        self.assertEqual(c.writes, 0)
        c.conflict = True
        self.assertEqual(
            m.ensure_today(c, CONFIG, guard(now), now)["result"],
            "already_exists_or_deleted",
        )

    def test_auth_cannot_redirect_or_target_an_external_server(self):
        with self.assertRaises(ValueError):
            m.CouchDB("http://remote.example:5984/notes", "inkdrop", "test")
        with self.assertRaises(ValueError):
            m.CouchDB("http://name:secret@127.0.0.1:5984/notes", "inkdrop", "test")
        with self.assertRaises(ValueError):
            m.CouchDB("http://127.0.0.1:5984/notes/_all_docs", "inkdrop", "test")

    def test_weekly_and_monthly_retries_share_one_note(self):
        for period, prefix, days, title in [
            ("weekly", "Review ", [28, 29, 30], "Review 2026-W40"),
            ("monthly", "Plan ", [1, 15, 30], "Plan 2026-09"),
        ]:
            c = Client()
            config = dict(CONFIG, id=period, period=period, title_prefix=prefix)
            for day in days:
                now = datetime.datetime(2026, 9, day, 1, tzinfo=UTC)
                m.ensure_today(c, config, guard(now), now)
            self.assertEqual(c.writes, 1)
            self.assertEqual(next(iter(c.notes.values()))["title"], title)

    def test_iso_week_crosses_year_correctly(self):
        self.assertEqual(m.period_key(datetime.date(2027, 1, 1), "weekly"), "2026-W53")
        with self.assertRaises(ValueError):
            m.period_key(datetime.date.today(), "hourly")

    def test_templates_expand_and_count_tasks(self):
        now = datetime.datetime(2026, 10, 3, 15, tzinfo=UTC)
        config = dict(
            CONFIG,
            id="review",
            body="{{date}} / {{period}}\n- [ ] A\n* [x] B\n+ [X] C\n",
        )
        doc = m.note_for("book:test", "2026-W40", now, config)
        self.assertTrue(doc["body"].startswith("2026-10-04 / 2026-W40"))
        self.assertEqual((doc["numOfTasks"], doc["numOfCheckedTasks"]), (3, 2))
        with self.assertRaises(ValueError):
            m.note_for("book:test", "2026-10-04", now, dict(config, body="a" * 16385))

    def test_daily_ids_remain_compatible_and_jobs_are_distinct(self):
        now = datetime.datetime(2026, 10, 3, 15, tzinfo=UTC)
        legacy = (
            "note:"
            + hashlib.sha256(
                ("inkdrop-daily-todo\0book:test\0" + "2026-10-04").encode()
            ).hexdigest()[:32]
        )
        daily = m.note_for("book:test", "2026-10-04", now)
        self.assertEqual(daily["_id"], legacy)
        self.assertNotEqual(
            daily["_id"],
            m.note_for("book:test", "2026-10-04", now, {"id": "other"})["_id"],
        )


if __name__ == "__main__":
    unittest.main()
