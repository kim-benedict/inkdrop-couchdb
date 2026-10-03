#!/usr/bin/env python3
import argparse
import datetime
import getpass
import json
import os
import pathlib
import pwd
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

from scheduled_notes import CouchDB, period_key

ROOT = pathlib.Path("/etc/inkdrop/schedules")
UNITS = pathlib.Path("/etc/systemd/system")
WORKER = pathlib.Path("/usr/local/sbin/inkdrop-scheduled-notes.py")
SOURCE = pathlib.Path(__file__).resolve().parent
MARKER = "# Managed by inkdrop-couchdb scheduled notes"
USER = "inkdrop-todo"


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value):
        raise ValueError("Job ID must use lowercase letters, digits, and hyphens.")
    return value


def load_job(path):
    path = pathlib.Path(path).resolve()
    if path.stat().st_size > 8192:
        raise ValueError("Job configuration exceeds 8 KiB.")
    job = json.loads(path.read_text())
    job_id(job["id"])
    ZoneInfo(job["timezone"])
    period_key(datetime.date.today(), job["period"])
    for key, limit in [("notebook", 128), ("calendar", 128), ("title_prefix", 128)]:
        value = job.get(key, "" if key == "title_prefix" else None)
        if (
            not isinstance(value, str)
            or len(value) > limit
            or any(c in value for c in "\r\n\0%")
        ):
            raise ValueError("Invalid " + key + ".")
        job[key] = value
    if not job["notebook"] or not job["calendar"]:
        raise ValueError("Notebook and calendar must not be empty.")
    template = (path.parent / job["template"]).resolve()
    if path.parent not in template.parents or template.stat().st_size > 16384:
        raise ValueError("Use a template within the job directory, at most 16 KiB.")
    job["body"] = template.read_text()
    return job


def run(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def units_for(job):
    directory = ROOT / job["id"]
    name = "inkdrop-" + job["id"]
    service = f"""{MARKER}
[Unit]
Description=Create scheduled Inkdrop notes ({job['id']})
After=docker.service inkdrop-storage-guard.service
StartLimitIntervalSec=3600
StartLimitBurst=5

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {WORKER}
LoadCredential=config:{directory}/config.json
LoadCredential=couchdb-auth:{directory}/auth.json
LoadCredential=storage-status:/var/lib/inkdrop/image-safety/status.json
User={USER}
Group={USER}
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_INET AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=64M
TimeoutStartSec=45
Restart=on-failure
RestartSec=60
"""
    timer = f"""{MARKER}
[Unit]
Description=Schedule Inkdrop notes ({job['id']})

[Timer]
OnCalendar={job['calendar']} {job['timezone']}
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0
Unit={name}.service

[Install]
WantedBy=timers.target
"""
    return {UNITS / (name + ".service"): service, UNITS / (name + ".timer"): timer}


def assert_managed(path, identifier):
    if not path.exists():
        return
    content = path.read_text()
    if content.startswith(MARKER + "\n"):
        return
    if identifier == "daily-todo":
        if (
            path.suffix == ".service"
            and "ExecStart=/usr/bin/python3 /usr/local/sbin/inkdrop-daily-todo.py\n"
            in content
        ):
            return
        sibling = path.with_suffix(".service")
        if (
            path.suffix == ".timer"
            and sibling.exists()
            and "ExecStart=/usr/bin/python3 /usr/local/sbin/inkdrop-daily-todo.py\n"
            in sibling.read_text()
        ):
            return
    raise ValueError("Refusing to replace an unrelated unit: " + path.name)


def atomic_write(path, content, mode):
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(paths):
    directory = pathlib.Path("/var/lib/inkdrop/schedule-backups")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / stamp
    backup.mkdir(mode=0o700)
    previous = {}
    for number, path in enumerate(paths):
        previous[path] = (
            (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
        )
        if previous[path] is not None:
            saved = backup / str(number)
            atomic_write(saved, previous[path][0], 0o600)
    atomic_write(backup / "paths.json", json.dumps([str(p) for p in paths]), 0o600)
    return previous


def state(unit, property_name):
    result = subprocess.run(
        ["systemctl", property_name, unit], capture_output=True, text=True
    )
    return result.stdout.strip() in ("enabled", "active")


def require_host(require_guard=True):
    if os.geteuid() != 0 or sys.platform != "linux":
        raise RuntimeError("Run installation on the Debian server with sudo.")
    if (
        require_guard
        and not pathlib.Path("/var/lib/inkdrop/image-safety/status.json").is_file()
    ):
        raise RuntimeError("Install the server storage guard first.")


def install(job, database):
    require_host()
    unit_paths = units_for(job)
    for path in unit_paths:
        assert_managed(path, job["id"])
    run("systemd-analyze", "calendar", job["calendar"] + " " + job["timezone"])
    directory = ROOT / job["id"]
    auth_path = directory / "auth.json"
    legacy = pathlib.Path("/etc/inkdrop/daily-todo.auth")
    if auth_path.exists():
        auth = json.loads(auth_path.read_text())
    elif legacy.exists():
        auth = json.loads(legacy.read_text())
    else:
        auth = {
            "username": "inkdrop",
            "password": getpass.getpass("Inkdrop sync password: "),
        }
    if auth["username"] != "inkdrop" or not auth["password"]:
        raise ValueError("Use the existing inkdrop sync account.")
    client = CouchDB(database, auth["username"], auth["password"])
    found = client.request(
        "POST",
        "/_find",
        {
            "selector": {"name": job["notebook"], "_id": {"$regex": "^book:"}},
            "fields": ["_id", "name"],
            "limit": 2,
        },
    )["docs"]
    if len(found) != 1:
        raise ValueError("Notebook name must match exactly one existing notebook.")
    config = {
        key: job[key] for key in ("id", "timezone", "period", "title_prefix", "body")
    }
    config.update(
        database_url=database, book_id=found[0]["_id"], notebook_name=job["notebook"]
    )
    try:
        account = pwd.getpwnam(USER)
        if account.pw_uid == 0 or account.pw_shell not in (
            "/usr/sbin/nologin",
            "/sbin/nologin",
        ):
            raise ValueError(
                "The scheduler account must be an unprivileged nologin user."
            )
    except KeyError:
        run(
            "useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            USER,
        )
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    ROOT.chmod(0o700)
    directory.chmod(0o700)
    contents = dict(unit_paths)
    contents.update(
        {
            directory / "config.json": json.dumps(config),
            auth_path: json.dumps(auth),
            WORKER: (SOURCE / "scheduled_notes.py").read_text(),
        }
    )
    previous = snapshot(list(contents))
    name = "inkdrop-" + job["id"]
    timer = name + ".timer"
    enabled, active = state(timer, "is-enabled"), state(timer, "is-active")
    try:
        existing = [p.name for p in unit_paths if p.exists()]
        if existing:
            run("systemctl", "stop", *existing)
        for path, content in contents.items():
            atomic_write(
                path, content, 0o644 if path in unit_paths or path == WORKER else 0o600
            )
        run("systemd-analyze", "verify", *(str(p) for p in unit_paths))
        run("systemctl", "daemon-reload")
        run("systemctl", "reset-failed", name + ".service")
        run("systemctl", "start", name + ".service")
        run("systemctl", "enable", "--now", timer)
    except Exception:
        for path, old in previous.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old[0], old[1])
        run("systemctl", "daemon-reload")
        if (UNITS / timer).exists():
            run("systemctl", "enable" if enabled else "disable", timer)
            if active:
                run("systemctl", "start", timer)
        raise
    print(run("systemctl", "list-timers", "--all", "--no-pager", timer))


def uninstall(identifier):
    require_host(require_guard=False)
    identifier = job_id(identifier)
    name = "inkdrop-" + identifier
    paths = [UNITS / (name + suffix) for suffix in (".timer", ".service")]
    for path in paths:
        assert_managed(path, identifier)
    snapshot(paths)
    run("systemctl", "disable", "--now", name + ".timer")
    run("systemctl", "stop", name + ".service")
    for path in paths:
        path.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    print("Schedule removed. Existing notes and private configuration were kept.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--uninstall", metavar="JOB_ID")
    parser.add_argument("--database", default="http://127.0.0.1:5984/inkdropnotes")
    args = parser.parse_args()
    if bool(args.job) == bool(args.uninstall) or (args.uninstall and args.check):
        parser.error("Choose a job configuration or --uninstall JOB_ID.")
    if args.uninstall:
        uninstall(args.uninstall)
    else:
        job = load_job(args.job)
        if args.check:
            print(
                json.dumps(
                    {key: job[key] for key in ("id", "calendar", "timezone", "period")}
                )
            )
        else:
            install(job, args.database)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = (
            str(error)
            if isinstance(error, (ValueError, RuntimeError))
            else type(error).__name__
        )
        print("Schedule failed: " + detail, file=sys.stderr)
        raise SystemExit(1)
