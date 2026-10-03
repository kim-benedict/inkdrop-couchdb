#!/usr/bin/env python3
"""Low-cost disk write guard; never deletes data or changes egress limits."""
import argparse
import configparser
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

GIB = 1024**3
STATE = pathlib.Path("/var/lib/inkdrop/image-safety")
SNIPPET = pathlib.Path("/etc/caddy/inkdrop-storage-guard.caddy")
CADDY = pathlib.Path("/etc/caddy/Caddyfile")
BLOCK = """@inkdropStorageBlocked {
    method POST PUT PATCH DELETE
    path /inkdropnotes /inkdropnotes/*
    not path /inkdropnotes/_bulk_get /inkdropnotes/_revs_diff /inkdropnotes/_find /inkdropnotes/_changes /inkdropnotes/_all_docs
}
respond @inkdropStorageBlocked "Insufficient storage: writes paused, existing data preserved" 507
"""


def decision(free, database, blocked=False):
    if free < 6 * GIB or database >= 8 * GIB:
        return True
    if blocked and (free < 7 * GIB or database >= int(7.5 * GIB)):
        return True
    return False


def atomic(file, text, mode=0o600):
    tmp = file.with_suffix(file.suffix + ".new")
    with tmp.open("w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(mode)
    os.replace(tmp, file)


def check():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    free = shutil.disk_usage("/srv/inkdrop/data").free
    database = int(
        subprocess.check_output(
            ["du", "-s", "-B1", "/srv/inkdrop/data"], text=True
        ).split()[0]
    )
    previous = (
        json.loads((STATE / "status.json").read_text())
        if (STATE / "status.json").exists()
        else {}
    )
    blocked = decision(free, database, previous.get("writes_paused", False))
    intended = BLOCK if blocked else "# Storage healthy; writes allowed.\n"
    old = SNIPPET.read_text() if SNIPPET.exists() else ""
    if old != intended:
        atomic(SNIPPET, intended, 0o644)
        try:
            subprocess.run(
                ["caddy", "validate", "--config", str(CADDY), "--adapter", "caddyfile"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "reload", "caddy"], check=True, capture_output=True
            )
        except Exception:
            atomic(SNIPPET, old, 0o644)
            raise
    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "free_bytes": free,
        "db_allocated_bytes": database,
        "writes_paused": blocked,
    }
    atomic(STATE / "status.json", json.dumps(report))
    print(json.dumps(report))


def install():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    ini = pathlib.Path("/srv/inkdrop/config/local.ini")
    before = ini.read_text()
    st = ini.stat()
    backup = STATE / "local.ini.before-image-safety"
    if not backup.exists():
        atomic(backup, before)
    cfg = configparser.RawConfigParser(strict=False)
    cfg.read_string(before)
    settings = {
        "couchdb": {"max_attachment_size": "4194304"},
        "disk_monitor": {
            "enable": "true",
            "background_view_indexing_threshold": "75",
            "interactive_view_indexing_threshold": "80",
            "interactive_database_writes_threshold": "80",
        },
        "smoosh": {
            "db_channels": "image_safe_dbs",
            "view_channels": "image_safe_views",
        },
        "smoosh.image_safe_dbs": {
            "priority": "ratio",
            "min_priority": "2.0",
            "min_size": "16777216",
            "concurrency": "1",
        },
        "smoosh.image_safe_views": {
            "priority": "ratio",
            "min_priority": "2.0",
            "min_size": "16777216",
            "concurrency": "1",
        },
    }
    for section, values in settings.items():
        if not cfg.has_section(section):
            cfg.add_section(section)
        for k, v in values.items():
            cfg.set(section, k, v)
    output = io.StringIO()
    cfg.write(output)
    after = output.getvalue()
    changed = after != before
    if changed:
        atomic(ini, after, 0o644)
        os.chown(ini, st.st_uid, st.st_gid)
    caddy = CADDY.read_text()
    cbackup = STATE / "Caddyfile.before-image-safety"
    if not cbackup.exists():
        atomic(cbackup, caddy)
    if not SNIPPET.exists():
        atomic(SNIPPET, "# Storage healthy; writes allowed.\n", 0o644)
    if "import /etc/caddy/inkdrop-storage-guard.caddy" not in caddy:
        needle = "    handle @allowed {\n"
        if caddy.count(needle) != 1:
            raise RuntimeError("Unexpected Caddy routing; manual review needed")
        updated = caddy.replace(
            needle, needle + "        import /etc/caddy/inkdrop-storage-guard.caddy\n"
        )
        atomic(CADDY, updated, 0o644)
        try:
            subprocess.run(
                ["caddy", "validate", "--config", str(CADDY), "--adapter", "caddyfile"],
                check=True,
                capture_output=True,
            )
            subprocess.run(["systemctl", "reload", "caddy"], check=True)
        except Exception:
            atomic(CADDY, caddy, 0o644)
            raise
    script = pathlib.Path("/usr/local/sbin/inkdrop-image-safety.py")
    if pathlib.Path(__file__).resolve() != script:
        atomic(script, pathlib.Path(__file__).read_text(), 0o755)
    atomic(
        pathlib.Path("/etc/systemd/system/inkdrop-storage-guard.service"),
        """[Unit]
Description=Inkdrop storage write guard (preserve existing data)
After=caddy.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/inkdrop-image-safety.py --check
Nice=19
MemoryMax=64M
TimeoutStartSec=30
""",
        0o644,
    )
    atomic(
        pathlib.Path("/etc/systemd/system/inkdrop-storage-guard.timer"),
        """[Unit]
Description=Check Inkdrop storage headroom every minute
[Timer]
OnBootSec=45s
OnUnitActiveSec=60s
AccuracySec=5s
[Install]
WantedBy=timers.target
""",
        0o644,
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    check()
    subprocess.run(
        ["systemctl", "enable", "--now", "inkdrop-storage-guard.timer"], check=True
    )
    if changed:
        subprocess.run(["docker", "restart", "inkdrop-couchdb"], check=True)
        ready = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:5984/_up", timeout=2
                ) as r:
                    if r.status == 200:
                        ready = True
                        break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    ready = True
                    break
                time.sleep(1)
            except Exception:
                time.sleep(1)
        if not ready:
            atomic(ini, before, 0o644)
            os.chown(ini, st.st_uid, st.st_gid)
            subprocess.run(["docker", "restart", "inkdrop-couchdb"], check=True)
            raise RuntimeError(
                "New CouchDB config did not become healthy; restored prior config"
            )
    print("IMAGE_STORAGE_SAFETY_INSTALLED")


def probe_http():
    """Exercise the guard in a separate loopback-only test Caddy, not production."""
    with tempfile.TemporaryDirectory(prefix="inkdrop-storage-probe-") as d:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        config = pathlib.Path(d) / "Caddyfile"
        config.write_text(
            "{\n admin off\n}\nhttp://127.0.0.1:"
            + str(port)
            + " {\n route {\n"
            + BLOCK
            + 'respond "READ_OK" 200\n}\n}\n'
        )
        proc = subprocess.Popen(
            ["caddy", "run", "--config", str(config), "--adapter", "caddyfile"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = "http://127.0.0.1:" + str(port)
            for _ in range(30):
                try:
                    urllib.request.urlopen(url + "/_up", timeout=1).close()
                    break
                except Exception:
                    time.sleep(0.1)
            for method, path, expected in [
                ("POST", "/inkdropnotes/_bulk_docs", 507),
                ("PUT", "/inkdropnotes/file:test/index", 507),
                ("GET", "/inkdropnotes/file:test/index", 200),
                ("POST", "/inkdropnotes/_bulk_get", 200),
                ("POST", "/_session", 200),
            ]:
                req = urllib.request.Request(
                    url + path, data=b"{}" if method == "POST" else None, method=method
                )
                try:
                    with urllib.request.urlopen(req, timeout=2) as r:
                        actual = r.status
                except urllib.error.HTTPError as e:
                    actual = e.code
                if actual != expected:
                    raise RuntimeError(
                        "Guard route probe failed: "
                        + method
                        + " "
                        + path
                        + " "
                        + str(actual)
                    )
            print("GUARD_HTTP_PROBE_PASSED: writes 507, reads and login permitted")
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--probe-http", action="store_true")
    args = p.parse_args()
    if args.install:
        install()
    elif args.probe_http:
        probe_http()
    else:
        check()
