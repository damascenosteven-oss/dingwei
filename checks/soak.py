"""Run eight three-hour cycles on the same Android emulator, retaining each measured window."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("short_tests", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    for case in ("moving", "stationary", "offline"):
        if not json.loads((args.short_tests / f"{case}.json").read_text())["passed"]:
            raise SystemExit(f"短测尚未通过 {case}")
    for case in ("doze", "process", "reboot", "stop", "family"):
        receipt = json.loads((args.short_tests / f"{case}.json").read_text())
        if not receipt.get("verified"):
            raise SystemExit(f"短测缺少实测记录 {case}")
    args.output.mkdir(parents=True, exist_ok=False)
    expected_apk = json.loads((args.short_tests / "moving.json").read_text())[
        "apkSHA256"
    ]
    started = time.time()
    # Each cycle contains 180 measured minutes, plus set-up and radio recovery.
    cycle = [
        "moving",
        "stationary",
        "offline",
        "moving",
        "stationary",
        "stationary",
        "moving",
        "offline",
    ]
    results = []
    for n in range(8):
        for i, scenario in enumerate(cycle):
            directory = args.output / f"{n + 1:02d}-{i + 1}-{scenario}"
            run = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("locked_route.py")),
                    scenario,
                    str(directory),
                ],
                check=False,
            )
            file = directory / "result.json"
            if run.returncode or not file.exists():
                raise SystemExit(
                    f"第 {n + 1} 轮 {scenario} 中断，保留 {directory} 以定位并补测。"
                )
            result = json.loads(file.read_text())
            if not result.get("passed"):
                raise SystemExit(f"验收指标未通过 {directory}")
            if result.get("apkSHA256") != expected_apk:
                raise SystemExit(f"安装包与通过短测的版本不符 {directory}")
            results.append(result)
            with (args.output / "progress.jsonl").open("a", encoding="utf-8") as out:
                print(json.dumps(result), file=out)
    elapsed = time.time() - started
    measured = sum(item["duration"] for item in results)
    summary = {
        "elapsedSeconds": elapsed,
        "measuredSeconds": measured,
        "windows": len(results),
        "apkSHA256": expected_apk,
        "totalBackfilled": sum(r.get("confirmedQueued", 0) for r in results),
        "maxSecondsBetweenWindows": max(
            (
                datetime.fromisoformat(right["start"])
                - datetime.fromisoformat(left["plannedEnd"])
            ).total_seconds()
            for left, right in zip(results, results[1:])
        ),
        "scenarios": {},
    }
    for case in set(cycle):
        group = [r for r in results if r["scenario"] == case]
        summary["scenarios"][case] = {
            "receivedDuringWindow": sum(r["received"] for r in group),
            "confirmedBackfill": sum(r.get("confirmedQueued", 0) for r in group),
            "maxArrivalGapSeconds": max(r["maxArrivalGapSeconds"] for r in group),
            "maxLatencySeconds": max(
                (
                    value
                    for r in group
                    for value in (
                        r["maxLatencySeconds"],
                        r.get("maxBackfillLatencySeconds"),
                    )
                    if value is not None
                ),
                default=None,
            ),
            "maxIndependentUpperSeconds": max(
                (
                    r["maxIndependentUpperSeconds"]
                    for r in group
                    if r.get("maxIndependentUpperSeconds") is not None
                ),
                default=None,
            ),
            "minIndependentWithin60s": min(
                (
                    r["independentWithin60s"]
                    for r in group
                    if "independentWithin60s" in r
                ),
                default=None,
            ),
            "rawNegativeDelays": sum(r.get("rawNegativeDelays", 0) for r in group),
            "peakQueue": max(r.get("peakQueue", 0) for r in group),
            "maxRecoverySeconds": max(
                (
                    r["recoverySeconds"]
                    for r in group
                    if r.get("recoverySeconds") is not None
                ),
                default=None,
            ),
            "rejected": sum(r["rejected"] for r in group),
        }
    summary["passed"] = elapsed >= 86400 and measured >= 86400
    (args.output / "result.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    if not summary["passed"]:
        raise SystemExit("实际测量时长不足 24 小时。")


if __name__ == "__main__":
    main()
