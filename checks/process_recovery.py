"""Observe a process death, reboot, or explicit force-stop on the isolated QA emulator."""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from emulator_lock import unlock_test_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=["process", "reboot", "force-stop"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    target = [
        os.environ["QA_ADB"],
        "-P",
        os.environ["QA_ADB_PORT"],
        "-s",
        os.environ["QA_SERIAL"],
    ]

    def adb(*parts, check=True):
        run = subprocess.run(
            [*target, *parts],
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            check=check,
        )
        return run.stdout.strip()

    assert adb("emu", "avd", "name").splitlines()[0] == "Dingwei_Issue3"
    pkg = "com.familywatch.elderlocation"

    def pid():
        return adb("shell", "pidof", pkg, check=False)

    def now():
        return datetime.now(timezone.utc)

    db = psycopg.connect(
        os.environ["QA_DB"],
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )
    ids = db.execute(
        """SELECT d.id FROM elder_devices d JOIN elders e ON d.elder_id=e.id
                        JOIN families f ON f.id=e.family_id WHERE f.name=%s AND d.active""",
        ("模拟家庭1",),
    ).fetchall()
    if len(ids) != 1:
        raise RuntimeError("预期一个绑定的虚拟设备")
    ident = ids[0][0]

    def first(since):
        return db.execute(
            """SELECT sequence,captured_at,received_at FROM track_points
                             WHERE device_id=%s AND captured_at>=%s AND received_at>=%s
                             ORDER BY received_at LIMIT 1""",
            (ident, since, since),
        ).fetchone()

    def watch(since, seconds):
        until = time.monotonic() + seconds
        n = 0
        while time.monotonic() < until:
            adb(
                "emu",
                "geo",
                "fix",
                str(121.471 + n * 0.00004),
                "31.231",
                "12",
                "9",
                "3",
            )
            sample = first(since)
            if sample:
                return sample
            n += 1
            time.sleep(4)
        return None

    started = now()
    before = pid()
    if not before.isdigit():
        raise RuntimeError("定位应用进程未运行")
    report = dict(case=args.case, start=started, pidBefore=before)
    if args.case == "process":
        adb("shell", "am", "kill", pkg)
        time.sleep(2)
        if pid() == before:
            report["method"] = (
                "same-UID SIGKILL after am kill spared the foreground service"
            )
            adb("shell", "run-as", pkg, "kill", "-9", before)
        else:
            report["method"] = "am kill"
        sample = watch(started, 300)
        report.update(
            firstNewPoint=sample,
            pidAfter=pid(),
            verified=bool(sample and pid() and pid() != before),
        )
    elif args.case == "reboot":
        adb("reboot")
        end = time.monotonic() + 180
        time.sleep(4)
        while (
            time.monotonic() < end
            and adb("shell", "getprop", "sys.boot_completed", check=False) != "1"
        ):
            time.sleep(2)
        if adb("shell", "getprop", "sys.boot_completed", check=False) != "1":
            raise RuntimeError("模拟器未完成重启")
        report["bootCompleted"] = now()
        report["lockedAtBoot"] = "deviceLocked=1" in adb("shell", "dumpsys", "trust")
        sample = watch(started, 190)
        if not sample and report["lockedAtBoot"]:
            report["firstUnlock"] = now()
            unlock_test_device(adb)
            sample = watch(started, 300)
        report.update(firstNewPoint=sample, pidAfter=pid(), verified=bool(sample))
    else:
        adb("shell", "am", "force-stop", pkg)
        time.sleep(1)
        report["absentAfterStop"] = not pid()
        stopped_sample = watch(started, 95)
        unlock_test_device(adb)
        reopened = now()
        adb("shell", "am", "start", "-n", pkg + "/.MainActivity")
        reopened_sample = watch(reopened, 100)
        active = db.execute(
            "SELECT active FROM elder_devices WHERE id=%s", (ident,)
        ).fetchone()[0]
        report.update(
            serverSharingActive=active,
            reopenedAt=reopened,
            pointWhileStopped=stopped_sample,
            pointWithoutNewConsent=reopened_sample,
            verified=bool(
                report["absentAfterStop"]
                and not stopped_sample
                and not reopened_sample
                and not active
            ),
        )
    report["finished"] = now()
    if report.get("firstNewPoint"):
        report["secondsToFirstReceipt"] = (
            report["firstNewPoint"][2] - started
        ).total_seconds()
    args.output.write_text(json.dumps(report, default=str, indent=2), encoding="utf-8")
    print(json.dumps(report, default=str))
    if not report["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
