#!/usr/bin/env python3
import argparse
import base64
import datetime
import hashlib
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CouchDB:
    def __init__(self, url, username, password):
        endpoint = urllib.parse.urlsplit(url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or endpoint.port != 5984
        ):
            raise ValueError("This job only connects to loopback CouchDB on port 5984.")
        if (
            endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Credentials must be supplied separately from the URL.")
        if not re.fullmatch(r"/[a-z][a-z0-9_$()+-]*", endpoint.path):
            raise ValueError("Use one CouchDB database name in the URL.")
        self.url = url.rstrip("/")
        self.authorization = (
            "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        )
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, suffix, body=None):
        request = urllib.request.Request(
            self.url + suffix,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": self.authorization,
                "Content-Type": "application/json",
            },
            method=method,
        )
        with self.opener.open(request, timeout=10) as response:
            data = response.read(131073)
            if len(data) > 131072:
                raise ValueError("Database response exceeds size limit.")
            return json.loads(data)


def period_key(day, period):
    if period == "daily":
        return day.isoformat()
    if period == "weekly":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "monthly":
        return day.strftime("%Y-%m")
    raise ValueError("Period must be daily, weekly, or monthly.")


def note_for(book_id, key, now, config=None):
    config = config or {}
    job_id = config.get("id", "daily-todo")
    namespace = (
        "inkdrop-daily-todo"
        if job_id == "daily-todo"
        else "inkdrop-scheduled-note:" + job_id
    )
    note_id = hashlib.sha256(
        (namespace + "\0" + book_id + "\0" + key).encode()
    ).hexdigest()[:32]
    local_day = (
        now.astimezone(ZoneInfo(config.get("timezone", "Asia/Seoul")))
        .date()
        .isoformat()
    )
    body = (
        config.get("body", "* [ ] \n")
        .replace("{{date}}", local_day)
        .replace("{{period}}", key)
    )
    if len(body.encode()) > 16384:
        raise ValueError("Template exceeds 16 KiB.")
    tasks = re.findall(r"^\s*[-*+]\s+\[([ xX])\]", body, re.MULTILINE)
    stamp = int(now.timestamp() * 1000)
    return {
        "_id": "note:" + note_id,
        "doctype": "markdown",
        "bookId": book_id,
        "title": config.get("title_prefix", "") + key,
        "body": body,
        "tags": [],
        "createdAt": stamp,
        "updatedAt": stamp,
        "timestamp": stamp,
        "status": "active",
        "share": "private",
        "pinned": False,
        "numOfTasks": len(tasks),
        "numOfCheckedTasks": sum(task.lower() == "x" for task in tasks),
    }


def ensure_today(client, config, guard, now=None, dry_run=False):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Use a timezone-aware date.")
    local_day = now.astimezone(ZoneInfo(config["timezone"])).date()
    day = local_day.isoformat()
    key = period_key(local_day, config.get("period", "daily"))
    title = config.get("title_prefix", "") + key
    book_id = config["book_id"]
    if not book_id.startswith("book:") or any(char in book_id for char in "/?#"):
        raise ValueError("Invalid notebook ID.")
    book = client.request("GET", "/" + urllib.parse.quote(book_id, safe=""))
    if book.get("_deleted") or book.get("name") != config["notebook_name"]:
        raise ValueError("Target notebook is missing or renamed. No note created.")
    found = client.request(
        "POST",
        "/_find",
        {
            "selector": {"bookId": book_id, "title": title},
            "fields": ["_id", "bookId", "title"],
            "limit": 2,
        },
    )
    if not isinstance(found.get("docs"), list):
        raise ValueError("Invalid note lookup response.")
    if found["docs"]:
        if not found["docs"][0].get("_id", "").startswith("note:"):
            raise ValueError("Unexpected document in note lookup.")
        return {
            "date": day,
            "result": "already_exists",
            "note_id": found["docs"][0]["_id"],
        }
    note = note_for(book_id, key, now, config)
    if dry_run:
        return {"date": day, "result": "dry_run", "note_id": note["_id"]}
    checked_at = datetime.datetime.fromisoformat(
        guard["checked_at"].replace("Z", "+00:00")
    )
    age = (now - checked_at).total_seconds()
    if age < -60 or age > 300 or guard.get("writes_paused") is not False:
        raise ValueError("Storage guard is stale or writes are paused.")
    if guard["free_bytes"] < 6 * 1024**3 or guard["db_allocated_bytes"] >= 8 * 1024**3:
        raise ValueError("Storage safety threshold reached.")
    try:
        result = client.request(
            "PUT", "/" + urllib.parse.quote(note["_id"], safe=""), note
        )
        if result.get("ok") is not True or result.get("id") != note["_id"]:
            raise ValueError("Database did not confirm the note write.")
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
        return {
            "date": day,
            "result": "already_exists_or_deleted",
            "note_id": note["_id"],
        }
    return {"date": day, "result": "created", "note_id": note["_id"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    directory = pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"])
    config = json.loads((directory / "config").read_text())
    auth = json.loads((directory / "couchdb-auth").read_text())
    guard = json.loads((directory / "storage-status").read_text())
    if auth["username"] != "inkdrop":
        raise ValueError("Use the existing Inkdrop sync account, not an administrator.")
    client = CouchDB(config["database_url"], auth["username"], auth["password"])
    print(json.dumps(ensure_today(client, config, guard, dry_run=args.dry_run)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"result": "failed", "error": type(error).__name__}))
        raise SystemExit(1)
