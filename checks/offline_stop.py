"""Verify consent withdrawal with queued samples on the isolated emulator."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg

from emulator_lock import unlock_test_device


def main():
    output = Path(sys.argv[1])
    adb = [
        os.environ["QA_ADB"],
        "-P",
        os.environ["QA_ADB_PORT"],
        "-s",
        os.environ["QA_SERIAL"],
    ]

    def device(*command):
        return subprocess.check_output(
            [*adb, *command], text=True, encoding="utf-8", timeout=15
        ).strip()

    def now():
        return datetime.now(timezone.utc)

    assert device("emu", "avd", "name").splitlines()[0] == "Dingwei_Issue3"
    assert not output.exists()
    pkg = "com.familywatch.elderlocation"

    def queue():
        lines = device(
            "shell",
            "run-as "
            + pkg
            + " sqlite3 -readonly databases/elder-location.db 'SELECT sequence FROM pending_locations ORDER BY sequence'",
        )
        return [int(n) for n in lines.splitlines()]

    user = json.loads(Path(os.environ["QA_FAMILIES"]).read_text())[0]
    api = "http://127.0.0.1:18763/api/v1"
    base = api + f"/families/{user['family']}/elders/{user['elder']}"
    db = psycopg.connect(
        os.environ["QA_DB"],
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )
    (device_id,) = db.execute(
        "SELECT id FROM elder_devices WHERE active AND elder_id=%s", (user["elder"],)
    ).fetchone()
    report = {"started": now()}
    with httpx.Client(trust_env=False, timeout=10) as client:
        auth = client.post(
            api + "/auth/login", json={k: user[k] for k in ("email", "password")}
        )
        auth.raise_for_status()
        unlock_test_device(device)
        device("shell", "am", "start", "-n", pkg + "/.MainActivity")
        try:
            for radio in ("wifi", "data"):
                device("shell", "svc", radio, "disable")
            deadline = time.monotonic() + 160
            while time.monotonic() < deadline and not queue():
                device("emu", "geo", "fix", "121.475", "31.232", "12", "9", "3")
                time.sleep(4)
            ids = queue()
            if not ids:
                raise RuntimeError("撤销前未观察到可核对的离线队列")
            report["queuedBeforeStop"] = ids
            wanted = client.post(
                base + "/locate-requests",
                headers={"X-CSRF-Token": auth.json()["csrfToken"]},
            )
            wanted.raise_for_status()
            request_id = wanted.json()["id"]
            report["requestBeforeStop"] = wanted.json()["state"]
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("device_consent.py")),
                    "stop",
                ],
                check=True,
            )
            report["stoppedLocally"] = now()
            report["queuedAfterStop"] = queue()
        finally:
            for radio in ("wifi", "data"):
                device("shell", "svc", radio, "enable")
        report["networkRestored"] = now()
        until = time.monotonic() + 240
        active = True
        while time.monotonic() < until:
            (active,) = db.execute(
                "SELECT active FROM elder_devices WHERE id=%s", (device_id,)
            ).fetchone()
            if not active:
                break
            time.sleep(2)
        status = client.get(base + "/locate-requests/" + request_id)
        status.raise_for_status()
        report["serverSharingActive"] = active
        report["requestAfterStop"] = status.json()["state"]
        report["requestFailure"] = status.json()["failureCode"]
        (report["queuedIdsReceived"],) = db.execute(
            "SELECT count(*) FROM track_points WHERE device_id=%s AND sequence=ANY(%s)",
            (device_id, ids),
        ).fetchone()
        (report["anyPointsAfterStop"],) = db.execute(
            "SELECT count(*) FROM track_points WHERE device_id=%s AND received_at >= %s",
            (device_id, report["stoppedLocally"]),
        ).fetchone()
    report["finished"] = now()
    report["verified"] = (
        not active
        and not report["queuedAfterStop"]
        and not report["queuedIdsReceived"]
        and not report["anyPointsAfterStop"]
        and report["requestFailure"] == "sharing_stopped"
    )
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, default=str))
    if not report["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
