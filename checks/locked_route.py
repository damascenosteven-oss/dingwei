"""Drive the dedicated AVD through its GPS console and observe committed PostgreSQL rows."""

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def max_gap(values, start, end):
    inside = sorted({value for value in values if start <= value <= end})
    samples = [start, *inside, end]
    return max(right - left for left, right in zip(samples, samples[1:]))


def route_delay_bound(speed, captured, observed, issued):
    # Each 30-second moving segment has a distinct speed. A cached fix from a
    # different window or an unobserved input cannot establish a timing bound.
    if speed is None:
        return None
    # The AVD NMEA path reported 0.63 m/s for a 0.64 m/s input.
    # Speed tags are 4 cm/s apart. A 1 cm/s quantization cannot match two.
    key = round(speed * 100)
    segment = next((issued[k] for k in (key, key - 1, key + 1) if k in issued), None)
    if not segment or not segment["wall"] - 5 <= captured <= segment["wall"] + 35:
        return None
    duration = observed - segment["elapsed"]
    return duration if duration >= 0 else None


def run_device_command(command, record):
    # Error 10055 was observed before ADB could connect to its host daemon.
    # Retry only failed host connections. An Android-side failure invalidates the test.
    for attempt in range(3):
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as error:
            diagnostic = error.stderr or ""
            if "cannot connect to daemon" not in diagnostic or attempt == 2:
                raise
            record(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "attempt": attempt + 1,
                    "error": "adb_host_connection",
                    "windows10055": "10055" in diagnostic,
                }
            )
            time.sleep(2 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["moving", "stationary", "doze", "offline"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    durations = {"moving": 1800, "stationary": 1200, "doze": 900, "offline": 900}
    duration = durations[args.scenario]
    args.output.mkdir(parents=True, exist_ok=False)
    serial = os.environ["QA_SERIAL"]
    adb = [os.environ["QA_ADB"], "-P", os.environ["QA_ADB_PORT"], "-s", serial]

    def record_transport(event):
        with (args.output / "transport.jsonl").open("a", encoding="utf-8") as log:
            print(json.dumps(event), file=log)

    def device(*command):
        return run_device_command([*adb, *command], record_transport)

    assert device("emu", "avd", "name").splitlines()[0] == "Dingwei_Issue3"
    assert device("shell", "getprop", "ro.build.version.sdk") == "36"
    db = psycopg.connect(
        os.environ["QA_DB"],
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )
    identity = db.execute(
        """SELECT d.id FROM elder_devices d JOIN elders e ON d.elder_id=e.id
                              JOIN families f ON e.family_id=f.id
                              WHERE f.name=%s AND d.active""",
        ("模拟家庭1",),
    ).fetchall()
    assert len(identity) == 1
    device("shell", "input", "keyevent", "KEYCODE_HOME")
    device("shell", "input", "keyevent", "KEYCODE_SLEEP")
    time.sleep(1)

    def screen_state():
        return next(
            line.strip()
            for line in device("shell", "dumpsys", "power").splitlines()
            if "mWakefulness=" in line
        )

    def locked():
        return (
            "deviceLocked=1"
            in device("shell", "dumpsys", "trust")
            .split("(current):", 1)[-1]
            .splitlines()[0]
        )

    assert "Asleep" in screen_state() and locked(), (
        "The AVD must have a locked keyguard"
    )

    def scenario_mode(on):
        if args.scenario == "doze":
            if on:
                device("shell", "dumpsys", "battery", "unplug")
                device("shell", "dumpsys", "deviceidle", "force-idle")
            else:
                device("shell", "dumpsys", "deviceidle", "unforce")
                device("shell", "dumpsys", "battery", "reset")
        if args.scenario == "offline":
            for radio in ("wifi", "data"):
                device("shell", "svc", radio, "disable" if on else "enable")

    def queue_rows():
        sql = (
            "SELECT sequence,capturedAtEpochMs FROM pending_locations ORDER BY sequence"
        )
        command = (
            "run-as com.familywatch.elderlocation sqlite3 -readonly databases/elder-location.db '"
            + sql
            + "'"
        )
        return [
            [int(field) for field in line.split("|")]
            for line in device("shell", command).splitlines()
            if line
        ]

    installed_apk = device(
        "shell", "pm", "path", "com.familywatch.elderlocation"
    ).splitlines()[0]
    if not installed_apk.startswith("package:/data/app/"):
        raise RuntimeError("无法确认已安装测试包")
    apk_hash = device(
        "shell", "sha256sum", installed_apk.removeprefix("package:")
    ).split()[0]
    image = device("shell", "getprop", "ro.build.fingerprint")
    scenario_mode(True)
    start = time.time()
    started = time.monotonic()
    end = start + duration

    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat()

    meta = {
        "scenario": args.scenario,
        "serial": serial,
        "apkSHA256": apk_hash,
        "systemImage": image,
        "source": "AVD system geo fix",
        "start": iso(start),
        "plannedEnd": iso(end),
        "duration": duration,
    }
    (args.output / "window.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    peak_queue = 0
    issued = {}
    commits = {}

    def observe_commits():
        records = db.execute(
            "SELECT sequence,captured_at,speed_mps FROM track_points WHERE device_id=%s AND received_at>=%s",
            (identity[0][0], iso(start)),
        ).fetchall()
        observed = time.monotonic() - started
        for seq, captured, speed in records:
            if seq not in commits:
                commits[seq] = {
                    "observed": observed,
                    "speed": speed,
                    "captured": captured.isoformat(),
                    "upperSeconds": route_delay_bound(
                        speed, captured.timestamp(), observed, issued
                    ),
                }

    try:
        last_beat = time.time()
        index = 0
        with (args.output / "observer.jsonl").open(
            "x", encoding="utf-8", buffering=1
        ) as out:
            while time.monotonic() - started < duration:
                if time.time() - last_beat > 45:
                    raise RuntimeError(
                        "宿主机观察中断超过 45 秒，保留本次记录并补测此窗口"
                    )
                last_beat = time.time()
                elapsed = time.monotonic() - started
                moving = args.scenario in ("moving", "offline")
                angle = elapsed / 240
                lon = 121.4712 + (0.003 * math.sin(angle) if moving else 0)
                lat = 31.2310 + (0.002 * (1 - math.cos(angle)) if moving else 0)
                knots = "3" if moving else "0"
                if args.scenario == "moving":
                    # 0.64 to 3.00 m/s. Keep each speed unique within this window.
                    cm_per_second = 64 + 4 * min(59, int(elapsed // 30))
                    knots = f"{cm_per_second / 100 / 0.514444:.5f}"
                    issued.setdefault(
                        cm_per_second, {"elapsed": elapsed, "wall": time.time()}
                    )
                result = device(
                    "emu",
                    "geo",
                    "fix",
                    f"{lon:.7f}",
                    f"{lat:.7f}",
                    "12",
                    "9",
                    knots,
                )
                if "OK" not in result:
                    raise RuntimeError("GPS console failed")
                if args.scenario == "moving":
                    observe_commits()
                if index % 6 == 0:
                    state = screen_state()
                    if "Asleep" not in state and "Dozing" not in state:
                        raise RuntimeError(state)
                    if not locked():
                        raise RuntimeError("AVD keyguard was unlocked")
                    count, newest = db.execute(
                        "SELECT count(*),max(received_at) FROM track_points WHERE device_id=%s AND received_at >= %s",
                        (identity[0][0], iso(start)),
                    ).fetchone()
                    clock_before = (
                        db.execute("SELECT clock_timestamp()").fetchone()[0].timestamp()
                    )
                    phone_time = float(device("shell", "date", "+%s.%N"))
                    clock_after = (
                        db.execute("SELECT clock_timestamp()").fetchone()[0].timestamp()
                    )
                    queued = queue_rows()
                    peak_queue = max(peak_queue, len(queued))
                    print(
                        json.dumps(
                            {
                                "queuedCount": len(queued),
                                "deviceMinusServerSeconds": [
                                    phone_time - clock_after,
                                    phone_time - clock_before,
                                ],
                                "observed": iso(time.time()),
                                "screen": state,
                                "keyguardLocked": True,
                                "count": count,
                                "newest": newest.isoformat() if newest else None,
                                "idle": device(
                                    "shell", "cmd", "deviceidle", "get", "deep"
                                ),
                            }
                        ),
                        file=out,
                    )
                index += 1
                time.sleep(
                    max(
                        0,
                        min(started + duration, started + index * 5) - time.monotonic(),
                    )
                )
        if args.scenario == "moving":
            observe_commits()
            (args.output / "timing-evidence.json").write_text(
                json.dumps(
                    {"sourceSegments": issued, "visibleCommits": commits}, indent=2
                ),
                encoding="utf-8",
            )
        before_restore = queue_rows()
        (args.output / "queue-at-end.json").write_text(
            json.dumps(before_restore), encoding="utf-8"
        )
    finally:
        recovery_start = time.time()
        scenario_mode(False)
    recovery = {}
    if args.scenario in ("offline", "doze"):
        t0 = time.monotonic()
        first = None
        confirmed = []
        ids = [row[0] for row in before_restore]
        while time.monotonic() - t0 < (125 if args.scenario == "offline" else 605):
            first = db.execute(
                "SELECT min(received_at) FROM track_points WHERE device_id=%s AND received_at>=%s",
                (identity[0][0], iso(recovery_start)),
            ).fetchone()[0]
            if ids:
                confirmed = db.execute(
                    "SELECT sequence,captured_at,received_at FROM track_points WHERE device_id=%s AND sequence=ANY(%s)",
                    (identity[0][0], ids),
                ).fetchall()
            if first and len(confirmed) == len(ids):
                break
            time.sleep(2)
        recovery = {
            "releaseAt": iso(recovery_start),
            "firstReceiptAfterRelease": first.isoformat() if first else None,
            "recoverySeconds": first.timestamp() - recovery_start if first else None,
            "queuedBeforeRelease": len(ids),
            "confirmedQueued": len(confirmed),
            "maxBackfillLatencySeconds": max(
                (
                    (received - captured).total_seconds()
                    for _, captured, received in confirmed
                ),
                default=None,
            ),
            "pendingAfter": len(queue_rows()),
            "allQueuedMatched": all(
                any(
                    n == seq and round(cap.timestamp() * 1000) == ms
                    for n, cap, rec in confirmed
                )
                for seq, ms in before_restore
            ),
        }
        (args.output / "backfill.json").write_text(
            json.dumps(confirmed, default=str), encoding="utf-8"
        )
    rows = db.execute(
        """SELECT sequence,captured_at,received_at,is_mock,valid_for_safety,accuracy_m,
                                latitude,longitude,speed_mps FROM track_points WHERE device_id=%s
                         AND received_at BETWEEN %s AND %s ORDER BY received_at,sequence""",
        (identity[0][0], iso(start), iso(end)),
    ).fetchall()
    (args.output / "receipts.json").write_text(
        json.dumps(rows, default=str), encoding="utf-8"
    )
    # Multiple SDK callbacks for the same GPS fix cannot improve either reliability measure.
    first_receipts = {}
    for row in rows:
        captured = row[1].timestamp()
        if start <= captured <= end:
            first_receipts.setdefault(captured, row[2].timestamp())
    # Radio isolation can prevent receipt without preventing GPS collection.
    captures = sorted(
        set(first_receipts)
        | {ms / 1000 for _, ms in before_restore if start <= ms / 1000 <= end}
    )
    arrivals = list(first_receipts.values())
    gaps = [b - a for a, b in zip([start, *captures], [*captures, end])]
    delays = [received - captured for captured, received in first_receipts.items()]
    arrival_gap = max_gap(arrivals, start, end)
    good_cadence = sum(g <= 90 for g in gaps) / len(gaps)
    good_latency = sum(0 <= d <= 60 for d in delays) / len(delays) if delays else 0
    moved = bool(rows) and max(r[6] for r in rows) - min(r[6] for r in rows) > 0.001
    rejected = db.execute(
        """SELECT payload FROM event_log WHERE event_type='location.rejected'
                             AND created_at BETWEEN %s AND %s AND payload->>'deviceId'=%s""",
        (iso(start), iso(time.time()), str(identity[0][0])),
    ).fetchall()
    reject_count = sum(len(r[0]["sequences"]) for r in rejected)
    result = {
        **meta,
        **recovery,
        "rejected": reject_count,
        "peakQueue": max(peak_queue, len(before_restore)),
        "received": len(rows),
        "uniqueCaptures": len(captures),
        "mock": sum(r[3] for r in rows),
        "maxArrivalGapSeconds": arrival_gap,
        "maxCaptureGapSeconds": max(gaps),
        "maxLatencySeconds": max(delays, default=None),
        "rawNegativeDelays": sum(delay < 0 for delay in delays),
        "arrivalWithin60s": good_latency,
        "captureWithin90s": good_cadence,
        "observedMovement": moved if args.scenario == "moving" else None,
    }
    if args.scenario == "moving":
        # Source command starts before this speed can reach the Android GPS
        # callback. A read-only independent query returns only after commit.
        # Both bounds use the same host monotonic clock, including deep idle.
        by_capture = {}
        for row in rows:
            cap = row[1].timestamp()
            bound = commits.get(row[0], {}).get("upperSeconds")
            if cap not in by_capture or (
                bound is not None
                and (by_capture[cap] is None or bound < by_capture[cap])
            ):
                by_capture[cap] = bound
        for _, captured_ms in before_restore:
            by_capture.setdefault(captured_ms / 1000, None)
        bounds = [value for value in by_capture.values() if value is not None]
        bounded_rate = sum(value <= 60 for value in bounds) / max(len(by_capture), 1)
        result["rawCrossClockWithin60s"] = good_latency
        result["rawNegativeDelays"] = sum(delay < 0 for delay in delays)
        result["independentWithin60s"] = bounded_rate
        result["timedDistinctCaptures"] = len(by_capture)
        result["unmatchedDistinctCaptures"] = len(by_capture) - len(bounds)
        result["maxIndependentUpperSeconds"] = max(bounds, default=None)
        result["passed"] = (
            moved
            and good_cadence >= 0.9
            and bounded_rate >= 0.95
            and arrival_gap <= 180
        )
    if args.scenario == "stationary":
        result["passed"] = bool(captures) and max(gaps) <= 600 and arrival_gap <= 600
    if args.scenario == "offline":
        result["zeroArrivalsWhileOffline"] = not rows
        result["passed"] = (
            not rows
            and recovery["queuedBeforeRelease"] > 0
            and recovery["allQueuedMatched"]
            and recovery["recoverySeconds"] is not None
            and 0 <= recovery["recoverySeconds"] <= 120
        )
    if args.scenario == "doze":
        observations = [
            json.loads(line)
            for line in (args.output / "observer.jsonl").read_text().splitlines()
        ]
        result["deepIdleObservations"] = sum(
            item["idle"] == "IDLE" for item in observations
        )
        result["totalObservations"] = len(observations)
        result["verified"] = bool(
            observations
            and result["deepIdleObservations"] == len(observations)
            and recovery["firstReceiptAfterRelease"]
            and recovery["allQueuedMatched"]
        )
        result["withinStationaryLimit"] = (
            bool(captures) and max(gaps) <= 600 and arrival_gap <= 600
        )
    (args.output / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))
    if result.get("passed") is False or result.get("verified") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
