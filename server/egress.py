#!/usr/bin/env python3
import datetime
import json
import os
import pathlib
import re
import socket
import subprocess
import tempfile
import time

STATE = pathlib.Path("/var/lib/inkdrop/egress-budget.json")
CHUNK = 4 * 1024**2
FIRST_CAP = 64 * 1024**2
MONTHLY_CAP = 256 * 1024**2


def month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def replace(limit):
    present = (
        subprocess.run(
            ["nft", "list", "table", "inet", "inkdrop_budget"], capture_output=True
        ).returncode
        == 0
    )
    script = (
        ("delete table inet inkdrop_budget\n" if present else "")
        + """
table inet inkdrop_budget {
 quota budget { over LIMIT bytes; }
 chain output { type filter hook output priority -150; policy accept;
  ip6 daddr 2000::/3 quota name "budget" drop
  ip daddr != { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } quota name "budget" drop
 }
 chain forward { type filter hook forward priority -150; policy accept;
  ip6 daddr 2000::/3 quota name "budget" drop
  ip daddr != { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } quota name "budget" drop
 }
}
""".replace(
            "LIMIT", str(limit)
        )
    )
    subprocess.run(
        ["nft", "-f", "-"], input=script, text=True, check=True, capture_output=True
    )


def save(data):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=STATE.parent, prefix=".egress-")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, STATE)
        directory = os.open(STATE.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def next_reservation(data, current):
    if data is None:
        data = {"month": current, "first_month": current, "reserved": 0}
    if not isinstance(data, dict):
        raise ValueError("Invalid egress ledger")
    for key in ("month", "first_month"):
        if not isinstance(data.get(key), str) or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", data[key]
        ):
            raise ValueError("Invalid egress ledger date")
    if type(data.get("reserved")) is not int or data["reserved"] < 0:
        raise ValueError("Invalid egress ledger reservation")
    if current < data["month"] or data["first_month"] > data["month"]:
        raise ValueError("Clock or ledger moved backwards")
    data = dict(data)
    if data["month"] != current:
        data.update(month=current, reserved=0)
    cap = FIRST_CAP if current == data["first_month"] else MONTHLY_CAP
    if data["reserved"] + CHUNK > cap:
        return None
    data["reserved"] += CHUNK
    return data


def reserve():
    previous = json.loads(STATE.read_text()) if STATE.exists() else None
    data = next_reservation(previous, month())
    if data is None:
        return False
    save(data)
    replace(CHUNK)
    return True


def used():
    data = json.loads(
        subprocess.check_output(
            ["nft", "-j", "list", "quota", "inet", "inkdrop_budget", "budget"]
        )
    )
    return next(
        item["quota"].get("used", 0) for item in data["nftables"] if "quota" in item
    )


def notify():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        address = "\0" + address[1:] if address.startswith("@") else address
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(b"READY=1")


def main():
    import fcntl

    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE.parent / "egress.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        replace(0)
        try:
            reserve()
            notify()
            print("Outbound guard active", flush=True)
            current = month()
            while True:
                if month() != current:
                    replace(0)
                    reserve()
                    current = month()
                elif used() >= CHUNK - 131072:
                    reserve()
                time.sleep(2)
        finally:
            replace(0)


if __name__ == "__main__":
    main()
