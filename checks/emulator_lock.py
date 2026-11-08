"""Unlock only the acceptance AVD with its locally provisioned test PIN."""

import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def unlock_test_device(adb):
    if adb("emu", "avd", "name").splitlines()[0] != "Dingwei_Issue3":
        raise RuntimeError("设备身份不符")
    if "deviceLocked=1" not in adb("shell", "dumpsys", "trust"):
        return
    pin = Path(os.environ["QA_PIN_FILE"]).read_text().strip()
    if not pin.isdigit():
        raise RuntimeError("测试 PIN 无效")
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "wm", "dismiss-keyguard")
    for attempt in range(3):
        adb("shell", "uiautomator", "dump", "/data/local/tmp/dingwei-keyguard.xml")
        xml = ET.fromstring(adb("shell", "cat", "/data/local/tmp/dingwei-keyguard.xml"))
        keys = {n.get("resource-id", "").split("/")[-1]: n for n in xml.iter("node")}
        if "key_enter" in keys:
            break
        adb("shell", "input", "swipe", "500", "1700", "500", "400", "250")
    else:
        raise RuntimeError("模拟器未显示系统 PIN 键盘")
    for label in ["key" + n for n in pin] + ["key_enter"]:
        x, y, r, b = map(int, re.findall(r"\d+", keys[label].get("bounds")))
        adb("shell", "input", "tap", str((x + r) // 2), str((y + b) // 2))
    time.sleep(2)
    if "deviceLocked=1" in adb("shell", "dumpsys", "trust"):
        raise RuntimeError("模拟器 PIN 解锁失败")
