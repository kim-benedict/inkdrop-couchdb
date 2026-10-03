#!/usr/bin/env python3
import argparse
import base64
import configparser
import getpass
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path("/srv/inkdrop")
SOURCE = pathlib.Path(__file__).resolve().parent
IMAGE = "apache/couchdb@sha256:f64db91fa08ecafada590785d3e3ebf8d9d7ecd7e5b8bc95d18fbdc74b2d5ac1"


def run(*args):
    subprocess.run(args, check=True)


def write(file, text, mode=0o600):
    file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, "w") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


def password(label):
    value = getpass.getpass(label + " password (16+ characters): ")
    if len(value) < 16 or any(char in value for char in "\r\n\0"):
        raise ValueError(
            "Password must have at least 16 characters and no line breaks."
        )
    if value != getpass.getpass("Repeat " + label.lower() + " password: "):
        raise ValueError("Passwords do not match.")
    return value


def request(admin, method, path, data=None):
    token = base64.b64encode(("couchadmin:" + admin).encode()).decode()
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:5984" + path,
        body,
        {"Authorization": "Basic " + token, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def access_design():
    return {
        "_id": "_design/access",
        "validate_doc_update": """function(newDoc, oldDoc, userCtx, secObj) {
  if (userCtx.roles.indexOf('_admin') !== -1 || userCtx.name === 'couchadmin' || userCtx.name === 'inkdrop') return;
  throw({forbidden: 'This account is read-only.'});
}""",
    }


def setup(host):
    if os.geteuid() != 0 or sys.platform != "linux":
        raise RuntimeError("Run on a fresh Debian 12 host with sudo.")
    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}",
        host,
    ):
        raise ValueError("Use a DNS hostname, such as notes.example.com.")
    if ROOT.exists():
        raise RuntimeError(
            "/srv/inkdrop already exists; setup never overwrites an existing deployment."
        )
    for command in ("docker", "caddy", "nft", "systemctl", "du"):
        if not shutil.which(command):
            raise RuntimeError("Install docker.io, caddy, nftables, and python3 first.")
    if (
        subprocess.run(
            ["docker", "inspect", "inkdrop-couchdb"], capture_output=True
        ).returncode
        == 0
    ):
        raise RuntimeError("The inkdrop-couchdb container already exists.")
    if pathlib.Path("/var/lib/inkdrop/egress-budget.json").exists():
        raise RuntimeError("An outbound ledger already exists; it must not be reset.")
    admin = password("Admin")
    sync = password("Inkdrop")
    reader = password("Reader")
    if len({admin, sync, reader}) != 3:
        raise ValueError(
            "Use different passwords for admin, sync, and reader accounts."
        )
    run("docker", "pull", IMAGE)
    for directory in (ROOT / "data", ROOT / "config"):
        directory.mkdir(parents=True, mode=0o700)
        os.chown(directory, 5984, 5984)
    config = configparser.RawConfigParser()
    config.read(SOURCE / "local.ini")
    config["admins"] = {"couchadmin": admin}
    output = io.StringIO()
    config.write(output)
    ini = ROOT / "config/local.ini"
    write(ini, output.getvalue())
    os.chown(ini, 5984, 5984)
    run(
        "docker",
        "run",
        "-d",
        "--name",
        "inkdrop-couchdb",
        "--restart",
        "unless-stopped",
        "--memory",
        "600m",
        "--memory-swap",
        "1g",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
        "-p",
        "127.0.0.1:5984:5984",
        "-v",
        str(ROOT / "data") + ":/opt/couchdb/data",
        "-v",
        str(ROOT / "config") + ":/opt/couchdb/etc/local.d",
        IMAGE,
    )
    ready = False
    for _ in range(45):
        try:
            request(admin, "GET", "/_up")
            ready = True
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    if not ready:
        raise RuntimeError(
            "CouchDB did not become ready. Configuration and data were kept."
        )
    hashed = configparser.RawConfigParser()
    hashed.read(ini)
    if not hashed.get("admins", "couchadmin").startswith("-pbkdf2-"):
        raise RuntimeError(
            "CouchDB did not hash its admin password; stop and inspect local.ini privately."
        )
    for database in ("_users", "_replicator", "inkdropnotes"):
        try:
            request(admin, "PUT", "/" + database)
        except urllib.error.HTTPError as error:
            if error.code != 412:
                raise
    for name, secret in (("inkdrop", sync), ("reader", reader)):
        doc = {
            "_id": "org.couchdb.user:" + name,
            "name": name,
            "type": "user",
            "roles": [],
            "password": secret,
        }
        request(admin, "PUT", "/_users/" + urllib.parse.quote(doc["_id"], safe=""), doc)
    request(
        admin,
        "PUT",
        "/inkdropnotes/_security",
        {
            "admins": {"names": ["couchadmin"], "roles": []},
            "members": {"names": ["inkdrop", "reader"], "roles": []},
        },
    )
    request(admin, "PUT", "/inkdropnotes/_design/access", access_design())
    request(
        admin,
        "PUT",
        "/inkdropnotes/_design/mobile",
        {
            "_id": "_design/mobile",
            "filters": {
                "mobile": "function(doc) { return doc._id.indexOf('file:') === -1; }"
            },
        },
    )
    for source, destination in (
        ("egress.py", "/usr/local/sbin/inkdrop-egress.py"),
        ("inkdrop-egress.service", "/etc/systemd/system/inkdrop-egress.service"),
    ):
        target = pathlib.Path(destination)
        if target.exists():
            raise RuntimeError(
                "An outbound guard already exists; refusing to replace it."
            )
        write(
            target,
            (SOURCE / source).read_text(),
            0o755 if source.endswith(".py") else 0o644,
        )
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "inkdrop-egress.service")
    caddy = pathlib.Path("/etc/caddy/Caddyfile")
    backup = ROOT / "Caddyfile.before-setup"
    write(backup, caddy.read_text() if caddy.exists() else "")
    snippet = pathlib.Path("/etc/caddy/inkdrop-storage-guard.caddy")
    write(snippet, "# Storage healthy; writes allowed.\n", 0o644)
    template = (
        (SOURCE / "Caddyfile.template").read_text().replace("{$INKDROP_HOST}", host)
    )
    temp = pathlib.Path("/etc/caddy/Caddyfile.inkdrop-new")
    write(temp, template, 0o644)
    run("caddy", "validate", "--config", str(temp), "--adapter", "caddyfile")
    os.replace(temp, caddy)
    run("systemctl", "enable", "--now", "caddy")
    run(sys.executable, str(SOURCE / "storage.py"), "--install")
    run("systemctl", "reload", "caddy")
    print("Ready: https://" + host + "/inkdropnotes")
    print(
        "Use the inkdrop account for sync. Keep the reader account for read-only integrations."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    args = parser.parse_args()
    try:
        setup(args.host)
    except Exception as error:
        print(
            "Setup stopped ("
            + type(error).__name__
            + "). Existing data and outbound ledger were kept.",
            file=sys.stderr,
        )
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error), file=sys.stderr)
        sys.exit(1)
