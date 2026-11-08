"""Exercise the native consent and stop buttons on the dedicated test AVD."""

import argparse
import json
import os
import re
import secrets
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from emulator_lock import unlock_test_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["enroll", "stop"])
    args = parser.parse_args()
    adb = [
        os.environ["QA_ADB"],
        "-P",
        os.environ["QA_ADB_PORT"],
        "-s",
        os.environ["QA_SERIAL"],
    ]

    def av(*parts):
        return subprocess.check_output([*adb, *map(str, parts)], timeout=20).decode(
            "utf-8"
        )

    if av("emu", "avd", "name").splitlines()[0] != "Dingwei_Issue3":
        raise RuntimeError("拒绝操作其他设备")

    def nodes():
        av("shell", "uiautomator", "dump", "/data/local/tmp/dingwei-view.xml")
        return list(
            ET.fromstring(av("shell", "cat", "/data/local/tmp/dingwei-view.xml")).iter(
                "node"
            )
        )

    def tap(n):
        x, y, r, b = map(int, re.findall(r"\d+", n.get("bounds")))
        av("shell", "input", "tap", (x + r) // 2, (y + b) // 2)

    def find_label(label):
        for attempt in range(5):
            for node in nodes():
                if node.get("text") == label:
                    return node
            av("shell", "input", "swipe", "500", "1400", "500", "700", "240")
        raise RuntimeError("未找到页面操作 " + label)

    unlock_test_device(av)
    pkg = "com.familywatch.elderlocation"
    av("shell", "am", "start", "-n", pkg + "/.MainActivity")
    if args.action == "stop":
        tap(find_label("停止位置共享"))
        find_label("连接家人守护")
        print("已通过老人端停止共享按钮，页面已回到绑定界面。")
        return
    users = json.loads(Path(os.environ["QA_FAMILIES"]).read_text())
    item = users[0]
    api = "http://127.0.0.1:18763/api/v1"
    with httpx.Client(trust_env=False, timeout=10) as web:
        login = web.post(
            api + "/auth/login", json={k: item[k] for k in ("email", "password")}
        )
        login.raise_for_status()
        base = api + f"/families/{item['family']}/elders/{item['elder']}"
        enrollment = web.post(
            base + "/device-enrollments",
            headers={"X-CSRF-Token": login.json()["csrfToken"]},
        )
        enrollment.raise_for_status()
        for i, value in enumerate(
            [enrollment.json()["code"], str(100000 + secrets.randbelow(900000))]
        ):
            edits = [n for n in nodes() if n.get("class") == "android.widget.EditText"]
            if len(edits) != 2:
                raise RuntimeError("绑定页面缺少输入框")
            tap(edits[i])
            av("shell", "input", "text", value)
            av("shell", "input", "keyevent", "4")
        tap(next(n for n in nodes() if n.get("class") == "android.widget.CheckBox"))
        tap(find_label("完成绑定并开始定位"))
        find_label("绑定成功")
        tap(find_label("进入运行状态"))
        status = web.get(base + "/device-status")
        status.raise_for_status()
        if status.json()["state"] in ("unbound", "stopped"):
            raise RuntimeError("绑定未生效")
    print("已在模拟器上明确同意共享并绑定到模拟家庭。")


if __name__ == "__main__":
    main()
